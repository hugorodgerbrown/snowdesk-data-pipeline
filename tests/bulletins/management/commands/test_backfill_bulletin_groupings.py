"""
tests/bulletins/management/commands/test_backfill_bulletin_groupings.py

Covers:
  - Read-only by default (no BulletinGrouping rows written without --commit).
  - --commit backfills all missing groupings.
  - A forced partial failure from compute_bulletin_grouping_boundary raises
    CommandError (non-zero exit).
  - Nothing-to-do path (no eligible bulletins) exits cleanly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from bulletins.models import BulletinGrouping
from tests.factories import BulletinFactory, BulletinGroupingFactory, PipelineRunFactory

_PATCH_TARGET = (
    "bulletins.management.commands.backfill_bulletin_groupings"
    ".compute_bulletin_grouping_boundary"
)


def _make_bulletins(n: int) -> None:
    """Seed n Bulletin rows without any BulletinGrouping."""
    run = PipelineRunFactory.create()
    for i in range(n):
        BulletinFactory.create(
            bulletin_id=f"backfill-test-{i:04d}",
            pipeline_run=run,
        )


@pytest.mark.django_db
class TestBackfillBulletinGroupingsCommand:
    """Tests for the backfill_bulletin_groupings management command."""

    # ------------------------------------------------------------------
    # Read-only (default)
    # ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no BulletinGrouping rows are created."""
        _make_bulletins(3)

        call_command("backfill_bulletin_groupings")

        assert BulletinGrouping.objects.count() == 0

    def test_dry_run_reports_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry run prints how many bulletins would be processed."""
        _make_bulletins(2)

        call_command("backfill_bulletin_groupings")

        out = capsys.readouterr().out
        assert "2" in out
        assert "READ-ONLY" in out

    # ------------------------------------------------------------------
    # --commit path
    # ------------------------------------------------------------------

    def test_commit_calls_service_for_each_eligible_bulletin(self) -> None:
        """--commit calls compute_bulletin_grouping_boundary for each ungrouped bulletin."""
        _make_bulletins(3)

        with patch(_PATCH_TARGET, return_value=None) as mock_fn:
            call_command("backfill_bulletin_groupings", commit=True)

        assert mock_fn.call_count == 3

    def test_commit_skips_already_grouped_bulletins(self) -> None:
        """Bulletins that already have a BulletinGrouping are not processed again."""
        # One bulletin with a grouping, one without.
        run = PipelineRunFactory.create()
        already_grouped = BulletinFactory.create(
            bulletin_id="already-0", pipeline_run=run
        )
        BulletinGroupingFactory.create(
            bulletin=already_grouped,
            countries=["CH"],
        )
        BulletinFactory.create(bulletin_id="ungrouped-0", pipeline_run=run)

        with patch(_PATCH_TARGET, return_value=None) as mock_fn:
            call_command("backfill_bulletin_groupings", commit=True)

        # Only the ungrouped bulletin should be passed to the service.
        assert mock_fn.call_count == 1

    def test_nothing_to_do_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When all bulletins already have groupings the command exits without error."""
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(pipeline_run=run)
        BulletinGroupingFactory.create(bulletin=bulletin, countries=["CH"])

        call_command("backfill_bulletin_groupings", commit=True)

        out = capsys.readouterr().out
        assert "Nothing to do" in out

    # ------------------------------------------------------------------
    # Partial failure
    # ------------------------------------------------------------------

    def test_partial_failure_raises_command_error(self) -> None:
        """A bulletin that raises during grouping causes CommandError (non-zero exit)."""
        _make_bulletins(2)

        with patch(
            _PATCH_TARGET,
            side_effect=[RuntimeError("geometry error"), None],
        ):
            with pytest.raises(CommandError, match="failed"):
                call_command("backfill_bulletin_groupings", commit=True)

    def test_partial_failure_continues_other_bulletins(self) -> None:
        """Remaining bulletins are still attempted after one fails."""
        _make_bulletins(3)

        call_counts: list[int] = []

        def _side_effect(bulletin):  # type: ignore[no-untyped-def]
            call_counts.append(1)
            if len(call_counts) == 1:
                raise RuntimeError("first one fails")
            return None

        with patch(_PATCH_TARGET, side_effect=_side_effect):
            with pytest.raises(CommandError):
                call_command("backfill_bulletin_groupings", commit=True)

        # All three were attempted despite the first failure.
        assert len(call_counts) == 3
