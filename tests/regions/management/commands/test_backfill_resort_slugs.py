"""
tests/regions/management/commands/test_backfill_resort_slugs.py

Covers ``backfill_resort_slugs`` (SNOW-796):
  - A dry run reports the candidates and writes nothing.
  - ``--commit`` mints ``slugify(name)`` for every row with no slug.
  - Rows that already carry a slug are left alone — a slug is never
    regenerated — so a second run is a no-op.
  - A collision, with an existing slug or between two candidates, is a
    failure: both rows are left unchanged and the command exits non-zero.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.regions.models import Resort
from tests.factories import ResortFactory

COMMAND = "backfill_resort_slugs"


def _unslugged(name: str) -> Resort:
    """Create a resort and strip the slug ``save()`` minted, as a pre-column row."""
    resort = ResortFactory.create(name=name)
    Resort.objects.filter(pk=resort.pk).update(slug=None)
    resort.refresh_from_db()
    assert resort.slug is None
    return resort


@pytest.mark.django_db
class TestBackfillResortSlugs:
    """--commit mints a slug per row; a bare run only reports."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit the candidates are counted and left as they are."""
        resort = _unslugged("Verbier")
        out = StringIO()

        call_command(COMMAND, stdout=out)

        resort.refresh_from_db()
        assert resort.slug is None
        assert "1 slug(s) would be minted" in out.getvalue()
        assert "No data written" in out.getvalue()

    def test_commit_mints_slugify_name(self) -> None:
        """--commit writes slugify(name) onto every row with no slug."""
        verbier = _unslugged("Verbier")
        crans = _unslugged("Crans-Montana")
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        verbier.refresh_from_db()
        crans.refresh_from_db()
        assert verbier.slug == "verbier"
        assert crans.slug == "crans-montana"
        assert "2 slug(s) minted, 0 failed" in out.getvalue()

    def test_existing_slug_is_never_rewritten(self) -> None:
        """A row that already has a slug is not a candidate."""
        resort = ResortFactory.create(name="Verbier", slug="verbier-4-vallees")

        call_command(COMMAND, "--commit", verbosity=0)

        resort.refresh_from_db()
        assert resort.slug == "verbier-4-vallees"

    def test_second_run_is_a_noop(self) -> None:
        """Idempotent: after one --commit there are no candidates left."""
        _unslugged("Verbier")
        call_command(COMMAND, "--commit", verbosity=0)
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        assert "Minting a slug for 0 resort(s)" in out.getvalue()

    def test_collision_with_an_existing_slug_fails_and_leaves_the_row(self) -> None:
        """A candidate whose slug is already taken is a failure, not a suffix."""
        ResortFactory.create(name="Verbier")
        duplicate = _unslugged("Verbier")

        with pytest.raises(CommandError, match="1 failure"):
            call_command(COMMAND, "--commit", verbosity=0)

        duplicate.refresh_from_db()
        assert duplicate.slug is None
        assert Resort.objects.filter(slug__startswith="verbier").count() == 1

    def test_collision_between_two_candidates_is_caught_in_the_dry_run(self) -> None:
        """Two unslugged rows wanting the same slug fail before anything is written."""
        _unslugged("Verbier")
        _unslugged("Verbier")

        with pytest.raises(CommandError, match="1 failure"):
            call_command(COMMAND, verbosity=0)

        assert not Resort.objects.exclude(slug__isnull=True).exists()
