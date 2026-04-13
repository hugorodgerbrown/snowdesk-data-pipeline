"""
tests/pipeline/management/commands/test_fetch_data.py — Tests for fetch_data.

Covers argument parsing, success/failure output, dry-run mode, force flag,
and error handling when the pipeline fails.
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
    run = PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=overrides.get("records_created", 3),
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
    run = PipelineRunFactory.create(
        status=PipelineRun.Status.FAILED,
        error_message=error_message,
    )
    return run


@pytest.mark.django_db
class TestFetchDataCommand:
    """Tests for the fetch_data management command."""

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_success_output(self, mock_run: MagicMock, capsys):
        """Successful run prints created/updated counts."""
        mock_run.return_value = _make_successful_run(
            records_created=5,
            records_updated=2,
        )

        call_command("fetch_data", "--date", "2025-03-15")

        output = capsys.readouterr().out
        assert "5 created" in output
        assert "2 updated" in output

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_passes_date_to_pipeline(self, mock_run: MagicMock):
        """The --date argument is forwarded as start and end."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_data", "--date", "2025-06-01")

        _, kwargs = mock_run.call_args
        assert str(kwargs["start"]) == "2025-06-01"
        assert str(kwargs["end"]) == "2025-06-01"

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_passes_force_flag(self, mock_run: MagicMock):
        """The --force flag is forwarded to run_pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_data", "--date", "2025-03-15", "--force")

        _, kwargs = mock_run.call_args
        assert kwargs["force"] is True

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_passes_dry_run_flag(self, mock_run: MagicMock):
        """The --dry-run flag is forwarded to run_pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_data", "--date", "2025-03-15", "--dry-run")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_dry_run_output(self, mock_run: MagicMock, capsys):
        """Dry run prints confirmation message."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_data", "--date", "2025-03-15", "--dry-run")

        output = capsys.readouterr().out
        assert "Dry run complete" in output

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_sets_triggered_by(self, mock_run: MagicMock):
        """The triggered_by label is set to 'fetch_data command'."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_data", "--date", "2025-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["triggered_by"] == "fetch_data command"

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_raises_on_failed_run(self, mock_run: MagicMock):
        """CommandError is raised when the pipeline run fails."""
        mock_run.return_value = _make_failed_run("connection refused")

        with pytest.raises(CommandError, match="connection refused"):
            call_command("fetch_data", "--date", "2025-03-15")

    @patch("pipeline.management.commands.fetch_data.run_pipeline")
    def test_raises_on_pipeline_exception(self, mock_run: MagicMock):
        """CommandError is raised when run_pipeline raises an exception."""
        mock_run.side_effect = RuntimeError("unexpected error")

        with pytest.raises(CommandError, match="unexpected error"):
            call_command("fetch_data", "--date", "2025-03-15")
