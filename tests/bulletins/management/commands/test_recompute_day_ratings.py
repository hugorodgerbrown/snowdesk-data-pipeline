"""
tests/bulletins/management/commands/test_recompute_day_ratings.py — Tests
for the recompute_day_ratings management command.

Covers:
  - Default mode (no --commit) is read-only — recompute_region_day is still
    called (so the read-only run reports a real count) but with commit=False.
  - --commit calls recompute_region_day with commit=True for every distinct
    (region, date) pair.
  - --start-date / --end-date narrow the pair set.
  - Nothing-to-do path (no RegionDayRating rows) exits cleanly.
  - A missing region (MicroRegion.DoesNotExist) is counted as a failure and
    does not abort the run.
  - A recompute_region_day exception is counted as a failure and does not
    abort the run; the command exits non-zero (CommandError).
  - Region objects are cached — MicroRegion.objects.get is called once per
    distinct region, not once per pair (SNOW-602: the pair stream is driven
    by countdown(), not iterate_rows(), since a pair has no id).
  - stdout carries a decreasing "N pair(s) remaining" line per pair at
    verbosity 1, none at verbosity 0.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory, RegionDayRatingFactory

_PATCH_TARGET = (
    "apps.bulletins.management.commands.recompute_day_ratings.recompute_region_day"
)


@pytest.mark.django_db
class TestRecomputeDayRatingsReadOnly:
    """Tests for the default (no --commit) read-only mode."""

    def test_dry_run_does_not_persist(self) -> None:
        """Without --commit, recompute_region_day is called with commit=False."""
        rating = RegionDayRatingFactory.create()

        with patch(_PATCH_TARGET) as mock_recompute:
            call_command("recompute_day_ratings", verbosity=0)

        mock_recompute.assert_called_once_with(
            mock_recompute.call_args[0][0], rating.date, commit=False
        )

    def test_dry_run_reports_correct_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The read-only run reports the number of pairs it would process."""
        RegionDayRatingFactory.create()
        RegionDayRatingFactory.create()

        call_command("recompute_day_ratings", verbosity=1)

        out = capsys.readouterr().out
        assert "(region, date) pairs to process: 2" in out
        assert "READ-ONLY" in out


@pytest.mark.django_db
class TestRecomputeDayRatingsCommit:
    """Tests for --commit mode."""

    def test_commit_recomputes_every_distinct_pair(self) -> None:
        """--commit calls recompute_region_day once per distinct (region, date)."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 2))

        with patch(_PATCH_TARGET) as mock_recompute:
            call_command("recompute_day_ratings", commit=True, verbosity=0)

        assert mock_recompute.call_count == 2
        for call in mock_recompute.call_args_list:
            assert call.kwargs == {"commit": True}

    def test_region_objects_are_cached(self) -> None:
        """MicroRegion.objects.get is called once per region, not per pair."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 2))

        with (
            patch(_PATCH_TARGET),
            patch.object(
                MicroRegion.objects, "get", wraps=MicroRegion.objects.get
            ) as mock_get,
        ):
            call_command("recompute_day_ratings", commit=True, verbosity=0)

        mock_get.assert_called_once_with(pk=region.pk)

    def test_start_date_filters_pairs(self) -> None:
        """--start-date excludes pairs before the given date."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))

        with patch(_PATCH_TARGET) as mock_recompute:
            call_command(
                "recompute_day_ratings",
                commit=True,
                start_date="2026-02-01",
                verbosity=0,
            )

        assert mock_recompute.call_count == 1
        assert mock_recompute.call_args[0][1] == datetime.date(2026, 3, 1)

    def test_end_date_filters_pairs(self) -> None:
        """--end-date excludes pairs after the given date."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))

        with patch(_PATCH_TARGET) as mock_recompute:
            call_command(
                "recompute_day_ratings",
                commit=True,
                end_date="2026-02-01",
                verbosity=0,
            )

        assert mock_recompute.call_count == 1
        assert mock_recompute.call_args[0][1] == datetime.date(2026, 1, 1)

    def test_nothing_to_do_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With no RegionDayRating rows, the command reports and returns."""
        call_command("recompute_day_ratings", commit=True, verbosity=1)

        out = capsys.readouterr().out
        assert "(region, date) pairs to process: 0" in out
        assert "Nothing to do." in out


@pytest.mark.django_db
class TestRecomputeDayRatingsFailureHandling:
    """A missing region or a recompute exception is a per-pair failure."""

    def test_missing_region_counts_as_failure(self) -> None:
        """A region_id with no matching MicroRegion row is a failure.

        The FK is enforced in practice (no genuinely orphaned row is
        reachable), so the lookup failure is exercised directly against
        ``MicroRegion.objects.get`` — the command guards against it
        defensively regardless.
        """
        RegionDayRatingFactory.create()

        with patch.object(
            MicroRegion.objects, "get", side_effect=MicroRegion.DoesNotExist
        ):
            with pytest.raises(CommandError, match="failed"):
                call_command("recompute_day_ratings", commit=True, verbosity=0)

    def test_recompute_exception_does_not_abort_other_pairs(self) -> None:
        """One pair raising does not stop the others from being attempted."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 2))

        with patch(
            _PATCH_TARGET,
            side_effect=[RuntimeError("boom"), None],
        ):
            with pytest.raises(CommandError, match="failed"):
                call_command("recompute_day_ratings", commit=True, verbosity=0)


@pytest.mark.django_db
class TestRecomputeDayRatingsCountdown:
    """SNOW-602: countdown output for the derived (region, date) pairs."""

    def test_stdout_carries_a_decreasing_remaining_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two pairs print '2 pair(s) remaining' then '1 pair(s) remaining'."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 3, 2))

        with patch(_PATCH_TARGET):
            call_command("recompute_day_ratings", commit=True, verbosity=1)

        out = capsys.readouterr().out
        assert "2 pair(s) remaining" in out
        assert "1 pair(s) remaining" in out

    def test_no_countdown_lines_at_verbosity_0(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Countdown lines are suppressed at verbosity 0."""
        RegionDayRatingFactory.create()

        with patch(_PATCH_TARGET):
            call_command("recompute_day_ratings", commit=True, verbosity=0)

        assert "remaining" not in capsys.readouterr().out
