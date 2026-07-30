"""
tests/bulletins/management/commands/test_backfill_bulletin_target_dates.py

Covers:
  - Read-only by default (no Bulletin.target_date rows written without --commit).
  - --commit backfills every bulletin missing a target_date.
  - Idempotence: a second --commit run mutates zero rows because the queryset
    (target_date__isnull=True) is empty once the first run has completed.
  - A forced derivation failure raises CommandError (non-zero exit) and still
    lets remaining bulletins be processed.
  - Nothing-to-do path (no eligible bulletins) exits cleanly.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import target_day_for_valid_from
from tests.factories import BulletinFactory, PipelineRunFactory

_PATCH_TARGET = (
    "apps.bulletins.management.commands.backfill_bulletin_target_dates"
    ".target_day_for_valid_from"
)


def _make_bulletins_missing_target_date(n: int) -> list[Bulletin]:
    """Seed n Bulletin rows with target_date explicitly nulled out."""
    run = PipelineRunFactory.create()
    bulletins = []
    for i in range(n):
        bulletin = BulletinFactory.create(
            bulletin_id=f"backfill-test-{i:04d}",
            pipeline_run=run,
            valid_from=datetime.datetime(2026, 3, 25, 7, 0, tzinfo=UTC),
            target_date=None,
        )
        bulletins.append(bulletin)
    return bulletins


@pytest.mark.django_db
class TestBackfillBulletinTargetDatesCommand:
    """Tests for the backfill_bulletin_target_dates management command."""

    # ------------------------------------------------------------------
    # Read-only (default)
    # ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no Bulletin.target_date values are persisted."""
        _make_bulletins_missing_target_date(3)

        call_command("backfill_bulletin_target_dates")

        assert Bulletin.objects.filter(target_date__isnull=True).count() == 3

    def test_dry_run_reports_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry run prints how many bulletins would be populated."""
        _make_bulletins_missing_target_date(2)

        call_command("backfill_bulletin_target_dates")

        out = capsys.readouterr().out
        assert "2" in out
        assert "READ-ONLY" in out

    # ------------------------------------------------------------------
    # --commit path
    # ------------------------------------------------------------------

    def test_commit_populates_target_date_for_every_row(self) -> None:
        """--commit derives and persists target_date for every eligible row."""
        bulletins = _make_bulletins_missing_target_date(3)

        call_command("backfill_bulletin_target_dates", commit=True)

        for bulletin in bulletins:
            bulletin.refresh_from_db()
            assert bulletin.target_date == target_day_for_valid_from(
                bulletin.valid_from
            )

    def test_commit_skips_bulletins_that_already_have_a_target_date(self) -> None:
        """Bulletins with an existing target_date are not touched again."""
        run = PipelineRunFactory.create()
        already_set = BulletinFactory.create(
            bulletin_id="already-0",
            pipeline_run=run,
            valid_from=datetime.datetime(2026, 3, 25, 7, 0, tzinfo=UTC),
            target_date=datetime.date(2020, 1, 1),
        )
        _make_bulletins_missing_target_date(1)

        call_command("backfill_bulletin_target_dates", commit=True)

        already_set.refresh_from_db()
        # Untouched — still the sentinel date, not the derived one.
        assert already_set.target_date == datetime.date(2020, 1, 1)

    def test_second_run_is_idempotent(self) -> None:
        """A second --commit run mutates zero rows once the first has completed."""
        _make_bulletins_missing_target_date(3)

        call_command("backfill_bulletin_target_dates", commit=True)
        assert Bulletin.objects.filter(target_date__isnull=True).count() == 0

        with patch(_PATCH_TARGET) as mock_derive:
            call_command("backfill_bulletin_target_dates", commit=True)

        mock_derive.assert_not_called()

    def test_nothing_to_do_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When every bulletin already has a target_date, the command exits cleanly."""
        run = PipelineRunFactory.create()
        BulletinFactory.create(pipeline_run=run)

        call_command("backfill_bulletin_target_dates", commit=True)

        out = capsys.readouterr().out
        assert "Nothing to do" in out

    # ------------------------------------------------------------------
    # Partial failure
    # ------------------------------------------------------------------

    def test_partial_failure_raises_command_error(self) -> None:
        """A bulletin that raises while deriving causes CommandError (non-zero exit)."""
        _make_bulletins_missing_target_date(2)

        with patch(
            _PATCH_TARGET,
            side_effect=[RuntimeError("boom"), datetime.date(2026, 3, 25)],
        ):
            with pytest.raises(CommandError, match="failed"):
                call_command("backfill_bulletin_target_dates", commit=True)

    def test_partial_failure_continues_other_bulletins(self) -> None:
        """Remaining bulletins are still processed after one fails."""
        bulletins = _make_bulletins_missing_target_date(3)

        call_counts: list[int] = []

        def _side_effect(valid_from: datetime.datetime) -> datetime.date:
            call_counts.append(1)
            if len(call_counts) == 1:
                raise RuntimeError("first one fails")
            return target_day_for_valid_from(valid_from)

        with patch(_PATCH_TARGET, side_effect=_side_effect):
            with pytest.raises(CommandError):
                call_command("backfill_bulletin_target_dates", commit=True)

        assert len(call_counts) == 3
        # Two of the three should have been successfully populated.
        populated = sum(
            1
            for b in bulletins
            if Bulletin.objects.get(pk=b.pk).target_date is not None
        )
        assert populated == 2
