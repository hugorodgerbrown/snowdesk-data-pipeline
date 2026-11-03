"""
tests/bulletins/management/commands/test_recompute_day_ratings.py — Tests
for the recompute_day_ratings management command.

Since SNOW-1400 the command enqueues one ``recompute_day_rating_task`` per
row through ``enqueue_each``; the tasks run inline here
(``ImmediateBackend``). Covers:
  - Read-only by default: it counts and recomputes nothing.
  - --commit recomputes every stale row (an older ``DAY_RATING_VERSION``)
    and leaves a current one alone; --all takes every row.
  - --start-date / --end-date narrow the selection.
  - A task that raises fails the run (CommandError) without aborting the
    rest.
  - stdout carries a countdown naming each row's region and day.
  - The task itself skips a row gone or current, and recomputes under the
    rebuild's lock.
"""

from __future__ import annotations

import datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.services.render_rebuild import recompute_one_day_rating
from apps.ingest.public import DAY_RATING_VERSION
from tests.factories import MicroRegionFactory, RegionDayRatingFactory

_PATCH_TARGET = "apps.bulletins.services.render_rebuild.recompute_region_day"


def _run(*args: str, verbosity: int = 1) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("recompute_day_ratings", *args, verbosity=verbosity, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestRecomputeDayRatingsReadOnly:
    """The default (no --commit) read-only mode."""

    def test_counts_and_recomputes_nothing(self) -> None:
        """Without --commit, no task runs."""
        RegionDayRatingFactory.create(version=0)
        RegionDayRatingFactory.create(version=0)

        with patch(_PATCH_TARGET) as recompute:
            out = _run()

        recompute.assert_not_called()
        assert "Recomputing 2 stale day rating(s) [READ-ONLY]" in out
        assert "2 day rating(s) would be enqueued" in out


@pytest.mark.django_db
class TestRecomputeDayRatingsCommit:
    """--commit mode."""

    def test_commit_recomputes_every_stale_row(self) -> None:
        """A row under an older version is recomputed; a current one is not."""
        region = MicroRegionFactory.create()
        stale = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 3, 1), version=0
        )
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 3, 2),
            version=DAY_RATING_VERSION,
        )

        with patch(_PATCH_TARGET) as recompute:
            _run("--commit", verbosity=0)

        recompute.assert_called_once()
        assert recompute.call_args.args[1] == stale.date
        assert recompute.call_args.kwargs == {"commit": True}

    def test_all_takes_current_rows_too(self) -> None:
        """--all is for a rule change made without a version bump."""
        RegionDayRatingFactory.create(version=DAY_RATING_VERSION)

        with patch(_PATCH_TARGET) as recompute:
            _run("--commit", "--all", verbosity=0)

        recompute.assert_called_once()

    def test_a_real_recompute_makes_the_row_current(self) -> None:
        """End to end: the row is rewritten under the current version."""
        rating = RegionDayRatingFactory.create(version=0)

        _run("--commit", verbosity=0)

        rating.refresh_from_db()
        assert rating.version == DAY_RATING_VERSION
        assert "Recomputing 0 stale" in _run("--commit", verbosity=1)

    def test_start_date_filters_rows(self) -> None:
        """--start-date excludes rows before it."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 1, 1), version=0
        )
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 3, 1), version=0
        )

        with patch(_PATCH_TARGET) as recompute:
            _run("--commit", "--start-date", "2026-02-01", verbosity=0)

        recompute.assert_called_once()
        assert recompute.call_args.args[1] == datetime.date(2026, 3, 1)

    def test_end_date_filters_rows(self) -> None:
        """--end-date excludes rows after it."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 1, 1), version=0
        )
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 3, 1), version=0
        )

        with patch(_PATCH_TARGET) as recompute:
            _run("--commit", "--end-date", "2026-02-01", verbosity=0)

        recompute.assert_called_once()
        assert recompute.call_args.args[1] == datetime.date(2026, 1, 1)


@pytest.mark.django_db
class TestRecomputeDayRatingsFailureHandling:
    """A failed task is counted, and does not stop the rest."""

    def test_a_failure_does_not_abort_other_rows(self) -> None:
        """Both rows are attempted; the run exits non-zero."""
        RegionDayRatingFactory.create(date=datetime.date(2026, 3, 1), version=0)
        RegionDayRatingFactory.create(date=datetime.date(2026, 3, 2), version=0)

        with (
            patch(_PATCH_TARGET, side_effect=[RuntimeError("boom"), None]) as rc,
            pytest.raises(CommandError, match="1 failure"),
        ):
            _run("--commit", verbosity=0)

        assert rc.call_count == 2


@pytest.mark.django_db
class TestRecomputeDayRatingsCountdown:
    """stdout reads as a countdown naming each row."""

    def test_names_each_rows_region_and_day(self) -> None:
        """The label is the region code and the day."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 3, 1), version=0
        )

        out = _run()

        assert "CH-4115 2026-03-01" in out

    def test_no_countdown_lines_at_verbosity_0(self) -> None:
        """Verbosity 0 keeps the countdown quiet."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(region=region, version=0)

        assert "CH-4115" not in _run(verbosity=0)


@pytest.mark.django_db
class TestRecomputeOneDayRating:
    """The task's body re-checks its row."""

    def test_a_gone_row_is_a_no_op(self) -> None:
        """Deleted since it was enqueued."""
        assert recompute_one_day_rating(999_999) == 0

    def test_a_current_row_is_skipped_unless_forced(self) -> None:
        """A bulletin task reached it first."""
        rating = RegionDayRatingFactory.create(version=DAY_RATING_VERSION)

        with patch(_PATCH_TARGET) as recompute:
            assert recompute_one_day_rating(rating.pk) == 0
            assert recompute_one_day_rating(rating.pk, force=True) == 1

        recompute.assert_called_once()
