"""
tests/locations/management/commands/test_backfill_location_short_ids.py

Covers ``backfill_location_short_ids`` (SNOW-797):
  - A dry run reports the candidates and writes nothing.
  - ``--commit`` mints an eleven-character id for every row with none.
  - Rows that already carry a short id are left alone — an id is never
    regenerated — so a second run is a no-op.
  - Persistent collisions are a failure: the row is left unchanged and the
    command exits non-zero.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from tests.factories import LocationFactory

COMMAND = "backfill_location_short_ids"


def _unminted() -> Location:
    """Create a location and strip the id the default minted, as a pre-column row."""
    location = LocationFactory.create()
    Location.objects.filter(pk=location.pk).update(short_id=None)
    location.refresh_from_db()
    assert location.short_id is None
    return location


@pytest.mark.django_db
class TestBackfillLocationShortIds:
    """--commit mints an id per row; a bare run only reports."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit the candidates are counted and left as they are."""
        location = _unminted()
        out = StringIO()

        call_command(COMMAND, stdout=out)

        location.refresh_from_db()
        assert location.short_id is None
        assert "1 short id(s) would be minted" in out.getvalue()
        assert "No data written" in out.getvalue()

    def test_commit_mints_an_eleven_character_id(self) -> None:
        """--commit writes a distinct token onto every row with none."""
        first = _unminted()
        second = _unminted()
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.short_id is not None and len(first.short_id) == 11
        assert second.short_id is not None and len(second.short_id) == 11
        assert first.short_id != second.short_id
        assert "2 short id(s) minted, 0 failed" in out.getvalue()

    def test_existing_id_is_never_rewritten(self) -> None:
        """A row that already has a short id is not a candidate."""
        location = LocationFactory.create()
        before = location.short_id

        call_command(COMMAND, "--commit", verbosity=0)

        location.refresh_from_db()
        assert location.short_id == before

    def test_second_run_is_a_noop(self) -> None:
        """Idempotent: after one --commit there are no candidates left."""
        _unminted()
        call_command(COMMAND, "--commit", verbosity=0)
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        assert "Minting a short id for 0 location(s)" in out.getvalue()

    def test_persistent_collision_fails_and_leaves_the_row(self) -> None:
        """A generator that only ever returns a taken id is a failure, not a loop."""
        existing = LocationFactory.create()
        location = _unminted()

        with (
            patch(
                "apps.locations.management.commands.backfill_location_short_ids"
                ".generate_short_id",
                return_value=existing.short_id,
            ),
            pytest.raises(CommandError, match="1 failure"),
        ):
            call_command(COMMAND, "--commit", verbosity=0)

        location.refresh_from_db()
        assert location.short_id is None
