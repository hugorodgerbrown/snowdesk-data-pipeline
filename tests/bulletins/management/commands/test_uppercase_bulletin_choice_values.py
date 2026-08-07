"""
tests/bulletins/management/commands/test_uppercase_bulletin_choice_values.py

Covers the ``uppercase_bulletin_choice_values`` management command
(SNOW-582), PipelineRun.status pass only — the Bulletin.source,
RegionDayRating.source, and render_model["source"] passes are added and
covered in a later ticket step:
  - Read-only by default (no PipelineRun.status values written without
    --commit).
  - --commit uppercases every legacy lower-case value, per target value.
  - Idempotence: a second --commit run selects nothing.
  - Nothing-to-do path (no eligible rows) exits cleanly.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.bulletins.models import PipelineRun
from tests.factories import PipelineRunFactory


def _seed_legacy_run(*, value: str = "success") -> None:
    """Create a PipelineRun and force its status to a legacy value."""
    run = PipelineRunFactory.create()
    PipelineRun.objects.filter(pk=run.pk).update(status=value)


@pytest.mark.django_db
class TestUppercaseBulletinChoiceValuesCommand:
    """Tests for the uppercase_bulletin_choice_values management command."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no PipelineRun.status values are persisted."""
        _seed_legacy_run(value="success")

        call_command("uppercase_bulletin_choice_values")

        assert PipelineRun.objects.filter(status="success").count() == 1
        assert (
            PipelineRun.objects.filter(status=PipelineRun.Status.SUCCESS).count() == 0
        )

    def test_dry_run_reports_the_breakdown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dry run names what it would convert, per target value."""
        _seed_legacy_run(value="success")
        _seed_legacy_run(value="failed")

        call_command("uppercase_bulletin_choice_values")

        out = capsys.readouterr().out
        assert "PipelineRun.status:" in out
        assert "SUCCESS: 1" in out
        assert "FAILED: 1" in out

    def test_commit_uppercases_every_legacy_row(self) -> None:
        """--commit rewrites every legacy value to its upper-case form."""
        _seed_legacy_run(value="pending")
        _seed_legacy_run(value="running")

        call_command("uppercase_bulletin_choice_values", "--commit")

        assert PipelineRun.objects.filter(status="pending").count() == 0
        assert PipelineRun.objects.filter(status="running").count() == 0
        assert (
            PipelineRun.objects.filter(status=PipelineRun.Status.PENDING).count() == 1
        )
        assert (
            PipelineRun.objects.filter(status=PipelineRun.Status.RUNNING).count() == 1
        )

    def test_second_run_selects_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running after a successful commit finds no eligible rows."""
        _seed_legacy_run(value="success")
        call_command("uppercase_bulletin_choice_values", "--commit")

        capsys.readouterr()
        call_command("uppercase_bulletin_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out

    def test_no_eligible_rows_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With every status already upper case, the command reports and returns."""
        PipelineRunFactory.create(status=PipelineRun.Status.SUCCESS)

        call_command("uppercase_bulletin_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out
