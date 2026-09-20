"""
tests/bulletins/management/commands/test_purge_degenerate_bulletin_groupings.py.

Covers the purge_degenerate_bulletin_groupings management command (SNOW-1001):
  - Read-only by default (no rows deleted without --commit) and reports a count.
  - --commit deletes only the degenerate rows; a genuinely multi-region
    grouping survives.
  - A bulletin whose extra regions carry no boundary is still degenerate.
  - An empty database exits 0 with "Nothing to purge".
  - At -v 0 the command prints nothing and still honours --commit.
  - A zero or negative --batch-size is rejected at parse time rather than
    crashing inside range() or silently deleting nothing.
  - A failing DELETE raises CommandError (non-zero exit).
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.models import BulletinGrouping, BulletinGroupingQuerySet
from tests.factories import (
    BulletinFactory,
    BulletinGroupingFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)

_VALID_FROM = datetime.datetime(2026, 1, 14, 16, 0, 0, tzinfo=UTC)

_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
}


def _make_grouping(
    *,
    bulletin_id: str,
    boundaried: int,
    unboundaried: int = 0,
) -> BulletinGrouping:
    """Create a BulletinGrouping whose bulletin covers the requested regions.

    Args:
        bulletin_id: The bulletin's identifier, unique per call.
        boundaried: How many linked micro-regions carry a boundary.
        unboundaried: How many linked micro-regions carry no boundary.

    Returns:
        The created BulletinGrouping row.

    """
    major = MajorRegionFactory.create(prefix=f"CH-{bulletin_id}", country="CH")
    sub = SubRegionFactory.create(prefix=f"CH-{bulletin_id}1", major=major)
    run = PipelineRunFactory.create()
    bulletin = BulletinFactory.create(
        bulletin_id=bulletin_id,
        valid_from=_VALID_FROM,
        valid_to=_VALID_FROM,
        pipeline_run=run,
    )
    for index in range(boundaried + unboundaried):
        region = MicroRegionFactory.create(
            region_id=f"{bulletin_id}-{index}",
            subregion=sub,
            boundary=_BOUNDARY if index < boundaried else None,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return BulletinGroupingFactory.create(bulletin=bulletin, countries=["CH"])


@pytest.mark.django_db
class TestPurgeDegenerateBulletinGroupingsCommand:
    """Tests for the purge_degenerate_bulletin_groupings management command."""

    # ------------------------------------------------------------------
    # Read-only (default)
    # ------------------------------------------------------------------

    def test_read_only_run_deletes_nothing(self) -> None:
        """Without --commit the degenerate row survives."""
        _make_grouping(bulletin_id="single", boundaried=1)

        call_command("purge_degenerate_bulletin_groupings")

        assert BulletinGrouping.objects.count() == 1

    def test_read_only_run_reports_the_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The read-only run names how many rows would be deleted."""
        _make_grouping(bulletin_id="single", boundaried=1)

        call_command("purge_degenerate_bulletin_groupings")

        out = capsys.readouterr().out
        assert "READ-ONLY" in out
        assert "Degenerate groupings: 1" in out

    # ------------------------------------------------------------------
    # --commit path
    # ------------------------------------------------------------------

    def test_commit_deletes_only_degenerate_rows(self) -> None:
        """A one-region and a no-region grouping go; a two-region grouping stays."""
        _make_grouping(bulletin_id="single", boundaried=1)
        _make_grouping(bulletin_id="none", boundaried=0)
        kept = _make_grouping(bulletin_id="pair", boundaried=2)

        call_command("purge_degenerate_bulletin_groupings", commit=True)

        assert list(BulletinGrouping.objects.values_list("pk", flat=True)) == [kept.pk]

    def test_unboundaried_regions_do_not_rescue_a_row(self) -> None:
        """Only boundaried regions count, matching the ingest-time guard."""
        _make_grouping(bulletin_id="mixed", boundaried=1, unboundaried=2)

        call_command("purge_degenerate_bulletin_groupings", commit=True)

        assert BulletinGrouping.objects.count() == 0

    def test_commit_honours_a_small_batch_size(self) -> None:
        """Deleting in chunks removes every candidate."""
        _make_grouping(bulletin_id="one", boundaried=1)
        _make_grouping(bulletin_id="two", boundaried=1)
        _make_grouping(bulletin_id="three", boundaried=1)

        call_command("purge_degenerate_bulletin_groupings", commit=True, batch_size=2)

        assert BulletinGrouping.objects.count() == 0

    @pytest.mark.parametrize("bad_value", ["0", "-1"])
    def test_non_positive_batch_size_is_rejected(self, bad_value: str) -> None:
        """A batch size below one never reaches the delete loop.

        Zero would raise ValueError from range() and -1 would make the loop
        empty, so a --commit run would report success having deleted nothing.
        """
        _make_grouping(bulletin_id="single", boundaried=1)

        with pytest.raises(CommandError, match="must be a positive integer"):
            call_command(
                "purge_degenerate_bulletin_groupings",
                "--commit",
                "--batch-size",
                bad_value,
            )

        assert BulletinGrouping.objects.count() == 1

    # ------------------------------------------------------------------
    # Nothing to do
    # ------------------------------------------------------------------

    def test_empty_database_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty database is a no-op, not an error."""
        call_command("purge_degenerate_bulletin_groupings", commit=True)

        assert "Nothing to purge" in capsys.readouterr().out

    def test_only_healthy_rows_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A database of genuinely aggregated groupings has nothing to purge."""
        _make_grouping(bulletin_id="pair", boundaried=2)

        call_command("purge_degenerate_bulletin_groupings", commit=True)

        assert "Nothing to purge" in capsys.readouterr().out
        assert BulletinGrouping.objects.count() == 1

    # ------------------------------------------------------------------
    # Verbosity
    # ------------------------------------------------------------------

    def test_silent_commit_still_deletes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """At -v 0 the purge runs and says nothing."""
        _make_grouping(bulletin_id="single", boundaried=1)

        call_command("purge_degenerate_bulletin_groupings", commit=True, verbosity=0)

        assert capsys.readouterr().out == ""
        assert BulletinGrouping.objects.count() == 0

    def test_silent_read_only_run_deletes_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """At -v 0 without --commit the row still survives."""
        _make_grouping(bulletin_id="single", boundaried=1)

        call_command("purge_degenerate_bulletin_groupings", verbosity=0)

        assert capsys.readouterr().out == ""
        assert BulletinGrouping.objects.count() == 1

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------

    def test_failed_delete_raises_command_error(self) -> None:
        """A DELETE that raises is counted and exits non-zero."""
        _make_grouping(bulletin_id="single", boundaried=1)

        with (
            patch.object(
                BulletinGroupingQuerySet,
                "delete",
                side_effect=RuntimeError("database is locked"),
            ),
            pytest.raises(CommandError, match="failed to delete"),
        ):
            call_command("purge_degenerate_bulletin_groupings", commit=True)

        assert BulletinGrouping.objects.count() == 1
