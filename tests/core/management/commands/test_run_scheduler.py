"""
tests/core/management/commands/test_run_scheduler.py — Tests for the
run_scheduler management command.

Covers:
  - ``call_command("run_scheduler")`` calls ``build_scheduler().start()``.
  - The command does not raise on SIGINT/KeyboardInterrupt.
  - The command propagates non-KeyboardInterrupt exceptions so Render
    crash-loops on a genuine startup failure.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from django.core.management import call_command


def test_run_scheduler_starts_scheduler() -> None:
    """run_scheduler builds the scheduler and calls start()."""
    mock_scheduler = mock.MagicMock(spec=BlockingScheduler)
    # Simulate get_jobs() returning two mock jobs with IDs so the command
    # can log their names.
    mock_job_a = mock.MagicMock()
    mock_job_a.id = "fetch_bulletins"
    mock_job_b = mock.MagicMock()
    mock_job_b.id = "fetch_weather"
    mock_scheduler.get_jobs.return_value = [mock_job_a, mock_job_b]

    with mock.patch(
        "schedule.build_scheduler",
        return_value=mock_scheduler,
    ):
        call_command("run_scheduler", verbosity=0, stdout=StringIO())

    mock_scheduler.start.assert_called_once_with()


def test_run_scheduler_handles_keyboard_interrupt() -> None:
    """run_scheduler exits cleanly when start() raises KeyboardInterrupt."""
    mock_scheduler = mock.MagicMock(spec=BlockingScheduler)
    mock_scheduler.get_jobs.return_value = []
    mock_scheduler.start.side_effect = KeyboardInterrupt

    with mock.patch(
        "schedule.build_scheduler",
        return_value=mock_scheduler,
    ):
        # Should not raise — KeyboardInterrupt is caught inside the command.
        call_command("run_scheduler", verbosity=0, stdout=StringIO())

    mock_scheduler.start.assert_called_once_with()


def test_run_scheduler_propagates_startup_failure() -> None:
    """run_scheduler propagates non-KeyboardInterrupt exceptions.

    If build_scheduler() itself raises (e.g. misconfigured settings), the
    exception must escape the command so Render sees a non-zero exit and
    crash-loops the worker rather than silently swallowing the error.
    """
    with mock.patch(
        "schedule.build_scheduler",
        side_effect=RuntimeError("bad config"),
    ):
        with pytest.raises(RuntimeError, match="bad config"):
            call_command("run_scheduler", verbosity=0, stdout=StringIO())
