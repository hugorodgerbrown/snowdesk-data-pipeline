"""
tests/test_schedule.py — Unit tests for schedule.py.

Covers:
  - ``build_scheduler()`` returns a :class:`BlockingScheduler` with exactly two jobs.
  - The jobs have the expected IDs: ``fetch_bulletins`` and ``fetch_weather``.
  - Each job's ``CronTrigger`` has the correct non-default field expressions.
  - Firing each job's callable invokes ``call_command`` with the expected arguments.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import schedule as schedule_module
from schedule import build_scheduler


@pytest.fixture
def scheduler() -> BlockingScheduler:
    """Return a freshly built scheduler (not started)."""
    return build_scheduler()


@pytest.fixture
def jobs(scheduler: BlockingScheduler) -> dict[str, object]:
    """Return a mapping of job ID → job for the scheduler fixture."""
    return {job.id: job for job in scheduler.get_jobs()}


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


def test_build_scheduler_returns_blocking_scheduler(
    scheduler: BlockingScheduler,
) -> None:
    """build_scheduler() returns a BlockingScheduler instance."""
    assert isinstance(scheduler, BlockingScheduler)


def test_scheduler_has_exactly_two_jobs(scheduler: BlockingScheduler) -> None:
    """The scheduler has exactly two registered jobs."""
    assert len(scheduler.get_jobs()) == 2


def test_job_ids(jobs: dict) -> None:
    """Both expected job IDs are registered."""
    assert "fetch_bulletins" in jobs
    assert "fetch_weather" in jobs


# ---------------------------------------------------------------------------
# Trigger field tests
# ---------------------------------------------------------------------------


def _get_field(trigger: CronTrigger, name: str) -> Any:
    """Return the field object for ``name`` from a CronTrigger.

    APScheduler does not expose a public type for trigger field objects, so the
    return type is ``Any``.
    """
    for field in trigger.fields:
        if field.name == name:
            return field
    raise KeyError(f"No field named {name!r} in trigger")


def test_fetch_bulletins_trigger_type(jobs: dict) -> None:
    """fetch_bulletins job uses a CronTrigger."""
    assert isinstance(jobs["fetch_bulletins"].trigger, CronTrigger)


def test_fetch_bulletins_minute_field(jobs: dict) -> None:
    """fetch_bulletins fires at minute 0 and 5 (two RangeExpressions)."""
    trigger: CronTrigger = jobs["fetch_bulletins"].trigger
    minute_field = _get_field(trigger, "minute")
    assert not minute_field.is_default
    # Two expressions: one for 0 and one for 5.
    assert len(minute_field.expressions) == 2
    expr_strs = {str(e) for e in minute_field.expressions}
    assert expr_strs == {"0", "5"}


def test_fetch_bulletins_hour_field_is_wildcard(jobs: dict) -> None:
    """fetch_bulletins hour field is the wildcard default (fires every hour)."""
    trigger: CronTrigger = jobs["fetch_bulletins"].trigger
    hour_field = _get_field(trigger, "hour")
    assert hour_field.is_default


def test_fetch_weather_trigger_type(jobs: dict) -> None:
    """fetch_weather job uses a CronTrigger."""
    assert isinstance(jobs["fetch_weather"].trigger, CronTrigger)


def test_fetch_weather_hour_field(jobs: dict) -> None:
    """fetch_weather fires at hours 0, 6, 12, and 18 (four RangeExpressions)."""
    trigger: CronTrigger = jobs["fetch_weather"].trigger
    hour_field = _get_field(trigger, "hour")
    assert not hour_field.is_default
    assert len(hour_field.expressions) == 4
    expr_strs = {str(e) for e in hour_field.expressions}
    assert expr_strs == {"0", "6", "12", "18"}


def test_fetch_weather_minute_field(jobs: dict) -> None:
    """fetch_weather fires at minute 0 (single explicit RangeExpression)."""
    trigger: CronTrigger = jobs["fetch_weather"].trigger
    minute_field = _get_field(trigger, "minute")
    assert not minute_field.is_default
    assert len(minute_field.expressions) == 1
    assert str(minute_field.expressions[0]) == "0"


# ---------------------------------------------------------------------------
# Callable wiring tests
# ---------------------------------------------------------------------------


def test_fetch_bulletins_calls_call_command() -> None:
    """Firing the fetch_bulletins job invokes call_command with the correct args.

    ``call_command`` is imported lazily inside ``_run_fetch_bulletins``, so the
    patch target is ``django.core.management.call_command`` rather than a local
    name inside the schedule module.
    """
    with mock.patch("django.core.management.call_command", autospec=True) as mock_cc:
        # Call the private job function directly so we don't need to start the
        # scheduler.
        schedule_module._run_fetch_bulletins()

    mock_cc.assert_called_once_with(
        "fetch_bulletins",
        "--source",
        "slf",
        "albina",
        "meteofrance",
        "--commit",
    )


def test_fetch_weather_calls_call_command() -> None:
    """Firing the fetch_weather job invokes call_command with the correct args.

    Patch target is ``django.core.management.call_command`` because the import
    is deferred to inside the job function.
    """
    with mock.patch("django.core.management.call_command", autospec=True) as mock_cc:
        schedule_module._run_fetch_weather()

    mock_cc.assert_called_once_with("fetch_weather", "--commit")
