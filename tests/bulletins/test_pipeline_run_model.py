"""
tests/bulletins/test_pipeline_run_model.py — Tests for the PipelineRun model.

Covers the terminal-state rule (SNOW-1228): ``mark_finished`` stores
PARTIAL when the run recorded any failed record and SUCCESS otherwise,
while ``mark_success`` — for callers that record no failures — always
stores SUCCESS. ``mark_failed`` logs one ERROR naming the run, what
triggered it (the provider, for fetch_bulletins) and the cause (SNOW-1425).
"""

from __future__ import annotations

import datetime
import logging

import pytest

from apps.bulletins.models import PipelineRun
from tests.factories import PipelineRunFactory


@pytest.mark.django_db
class TestPipelineRunTerminalState:
    """Tests for PipelineRun.mark_finished and mark_success."""

    def test_mark_finished_without_failures_is_success(self) -> None:
        """A run with no failed records finishes as SUCCESS."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.RUNNING, records_failed=0
        )

        run.mark_finished(3, 1)

        run.refresh_from_db()
        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 3
        assert run.records_updated == 1
        assert run.finished_at is not None

    def test_mark_finished_with_failures_is_partial(self) -> None:
        """A run that recorded a failed record is PARTIAL, not SUCCESS."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.RUNNING, records_failed=2
        )

        run.mark_finished(3, 1)

        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PARTIAL
        assert run.records_failed == 2
        assert run.records_created == 3
        assert run.finished_at is not None

    def test_mark_finished_logs_a_warning_when_partial(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A partial run is a warning in the log, carrying the failed count."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.RUNNING, records_failed=1
        )

        with caplog.at_level("WARNING", logger="apps.bulletins.models"):
            run.mark_finished(0, 0)

        assert "finished with failures" in caplog.text
        assert "1 failed" in caplog.text

    def test_mark_success_stays_success(self) -> None:
        """mark_success is unchanged for callers that never record failures."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.RUNNING, records_failed=0
        )

        run.mark_success(2, 0)

        run.refresh_from_db()
        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 2


@pytest.mark.django_db
class TestPipelineRunToString:
    """Tests for PipelineRun.to_string and its __str__ delegation."""

    def test_to_string_format(self) -> None:
        """to_string() gives the pk, the status and the start minute."""
        started_at = datetime.datetime(2026, 1, 15, 7, 30, tzinfo=datetime.UTC)
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.SUCCESS, started_at=started_at
        )
        run.refresh_from_db()

        assert run.to_string() == f"PipelineRun({run.pk}, SUCCESS, 2026-01-15 07:30)"

    def test_str_delegates_to_to_string(self) -> None:
        """str(run) is exactly to_string()."""
        run = PipelineRunFactory.create()

        assert str(run) == run.to_string()


@pytest.mark.django_db
class TestPipelineRunMarkFailed:
    """Tests for PipelineRun.mark_failed's single ERROR log (SNOW-1425)."""

    def test_logs_one_error_naming_the_trigger_and_cause(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The one ERROR names the run, the provider and the cause."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.RUNNING,
            triggered_by="fetch_bulletins command [ALBINA]",
        )

        with caplog.at_level(logging.ERROR, logger="apps.bulletins.models"):
            run.mark_failed(TimeoutError("Read timed out"))

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert str(run.pk) in message
        assert "[ALBINA]" in message
        assert "Read timed out" in message
        run.refresh_from_db()
        assert run.status == PipelineRun.Status.FAILED
        assert run.error_message == "Read timed out"
