"""
tests/pipeline/management/commands/test_backfill_data.py — Tests for backfill_data.

Covers argument parsing, date validation, success/failure output, dry-run
mode, force flag, and error handling when the pipeline fails.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.models import PipelineRun
from tests.factories import PipelineRunFactory


def _make_successful_run(**overrides) -> PipelineRun:
    """
    Build a PipelineRun in SUCCESS state for mocking run_pipeline return.

    Args:
        **overrides: Fields to override on the PipelineRun.

    Returns:
        A persisted PipelineRun marked as successful.

    """
    run = PipelineRunFactory(
        status=PipelineRun.Status.SUCCESS,
        records_created=overrides.get("records_created", 10),
        records_updated=overrides.get("records_updated", 0),
    )
    return run


def _make_failed_run(error_message: str = "API timeout") -> PipelineRun:
    """
    Build a PipelineRun in FAILED state for mocking run_pipeline return.

    Args:
        error_message: The error message to store on the run.

    Returns:
        A persisted PipelineRun marked as failed.

    """
    run = PipelineRunFactory(
        status=PipelineRun.Status.FAILED,
        error_message=error_message,
    )
    return run


@pytest.mark.django_db
class TestBackfillDataCommand:
    """Tests for the backfill_data management command."""

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_success_output(self, mock_run: MagicMock, capsys):
        """Successful run prints created/updated counts and day span."""
        mock_run.return_value = _make_successful_run(
            records_created=15,
            records_updated=3,
        )

        call_command(
            "backfill_data",
            "--start-date",
            "2025-03-01",
            "--end-date",
            "2025-03-31",
        )

        output = capsys.readouterr().out
        assert "15 created" in output
        assert "3 updated" in output
        assert "31 day(s)" in output

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_passes_dates_to_pipeline(self, mock_run: MagicMock):
        """Start and end dates are forwarded to run_pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "backfill_data",
            "--start-date",
            "2025-01-01",
            "--end-date",
            "2025-01-31",
        )

        _, kwargs = mock_run.call_args
        assert str(kwargs["start"]) == "2025-01-01"
        assert str(kwargs["end"]) == "2025-01-31"

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_passes_force_flag(self, mock_run: MagicMock):
        """The --force flag is forwarded to run_pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "backfill_data",
            "--start-date",
            "2025-03-01",
            "--end-date",
            "2025-03-01",
            "--force",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["force"] is True

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_passes_dry_run_flag(self, mock_run: MagicMock):
        """The --dry-run flag is forwarded to run_pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "backfill_data",
            "--start-date",
            "2025-03-01",
            "--end-date",
            "2025-03-01",
            "--dry-run",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_dry_run_output(self, mock_run: MagicMock, capsys):
        """Dry run prints confirmation message."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "backfill_data",
            "--start-date",
            "2025-03-01",
            "--end-date",
            "2025-03-01",
            "--dry-run",
        )

        output = capsys.readouterr().out
        assert "Dry run complete" in output

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_sets_triggered_by(self, mock_run: MagicMock):
        """The triggered_by label is set to 'backfill_data command'."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "backfill_data",
            "--start-date",
            "2025-03-01",
            "--end-date",
            "2025-03-01",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["triggered_by"] == "backfill_data command"

    def test_rejects_end_before_start(self):
        """CommandError is raised when end-date is before start-date."""
        with pytest.raises(CommandError, match="on or after"):
            call_command(
                "backfill_data",
                "--start-date",
                "2025-03-31",
                "--end-date",
                "2025-03-01",
            )

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_raises_on_failed_run(self, mock_run: MagicMock):
        """CommandError is raised when the pipeline run fails."""
        mock_run.return_value = _make_failed_run("rate limited")

        with pytest.raises(CommandError, match="rate limited"):
            call_command(
                "backfill_data",
                "--start-date",
                "2025-03-01",
                "--end-date",
                "2025-03-31",
            )

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_raises_on_pipeline_exception(self, mock_run: MagicMock):
        """CommandError is raised when run_pipeline raises an exception."""
        mock_run.side_effect = RuntimeError("disk full")

        with pytest.raises(CommandError, match="disk full"):
            call_command(
                "backfill_data",
                "--start-date",
                "2025-03-01",
                "--end-date",
                "2025-03-01",
            )

    @patch("pipeline.management.commands.backfill_data.run_pipeline")
    def test_single_day_range(self, mock_run: MagicMock, capsys):
        """A single-day range (start == end) works correctly."""
        mock_run.return_value = _make_successful_run(records_created=2)

        call_command(
            "backfill_data",
            "--start-date",
            "2025-06-15",
            "--end-date",
            "2025-06-15",
        )

        output = capsys.readouterr().out
        assert "1 day(s)" in output
        assert "2 created" in output
