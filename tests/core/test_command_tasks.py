"""
tests/core/test_command_tasks.py — enqueue_each, the bulk-command shape (SNOW-1397).

Covers:
  - A read-only run counts and enqueues nothing; tallies zeros included.
  - --commit enqueues one task per row with the row's arguments.
  - ``limit`` caps what is enqueued.
  - Inline (ImmediateBackend), ``pace`` is a sleep between tasks.
  - On the database backend, ``pace`` staggers ``run_after``; no sleep.
  - A row whose task is already waiting (READY) is not enqueued again.
  - An inline task that fails is counted.
  - An outside task with no pace, or one above the bulk priority, is refused.
  - The breaker: ``back_off`` opens it, ``backing_off`` reads it, it closes.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from django_tasks import task
from django_tasks_db.models import DBTaskResult

from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import (
    _OUTSIDE_TASKS,
    BULK_TASK_PRIORITY,
    back_off,
    backing_off,
    calls_outside,
    clear_back_off,
    enqueue_each,
)
from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory

pytestmark = pytest.mark.django_db

DATABASE = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

_SLEEP = "apps.core.command_tasks.time.sleep"

SEEN: list[int] = []


@task(priority=BULK_TASK_PRIORITY)
def record_task(pk: int) -> int:
    """Record the pk it was handed; the stand-in row task."""
    SEEN.append(pk)
    return pk


@task(priority=BULK_TASK_PRIORITY)
def failing_task(pk: int) -> int:
    """Fail every time."""
    raise RuntimeError(f"boom {pk}")


@task()
def default_priority_task(pk: int) -> int:
    """A task at the default priority, which a bulk command may not use."""
    return pk


@pytest.fixture(autouse=True)
def _reset() -> Any:
    """Each test starts with nothing seen and the breaker closed."""
    SEEN.clear()
    clear_back_off("test-service")
    yield
    clear_back_off("test-service")


def _command() -> RegisteredCommand:
    """Return a bare registered command with a captured stdout."""
    command = RegisteredCommand(stdout=StringIO())
    return command


def _run(command: RegisteredCommand, the_task: Any = record_task, **kwargs: Any) -> Any:
    """Enqueue ``the_task`` over every region, by pk, with ``kwargs``."""
    options: dict[str, Any] = {"commit": True, "verbosity": 1}
    options.update(kwargs)
    return enqueue_each(
        command,
        MicroRegion.objects.only("pk", "region_id"),
        the_task,
        args=lambda region: (region.pk,),
        describe=lambda region: region.region_id,
        **options,
    )


def test_a_read_only_run_counts_and_enqueues_nothing() -> None:
    """Without commit nothing runs; the count and the tally still say how many."""
    MicroRegionFactory.create_batch(2)
    command = _command()
    counts = _run(command, commit=False)
    assert counts.enqueued == 2
    assert SEEN == []
    assert command.counts == {"enqueued": 2, "already_queued": 0, "failed": 0}


def test_commit_enqueues_one_task_per_row_newest_first() -> None:
    """Each row is one task, handed its arguments; the countdown names it."""
    regions = MicroRegionFactory.create_batch(2)
    command = _command()
    _run(command)
    assert SEEN == sorted((r.pk for r in regions), reverse=True)
    out = command.stdout.getvalue()
    assert all(f"{r.pk} {r.region_id}" in out for r in regions)


def test_tally_as_names_the_count() -> None:
    """A second call adds its own count and sums the shared ones."""
    MicroRegionFactory.create()
    command = _command()
    _run(command)
    _run(command, tally_as="day_ratings_enqueued")
    assert command.counts == {
        "enqueued": 1,
        "day_ratings_enqueued": 1,
        "already_queued": 0,
        "failed": 0,
    }


def test_limit_caps_what_is_enqueued() -> None:
    """limit=1 enqueues one row of three."""
    MicroRegionFactory.create_batch(3)
    assert _run(_command(), limit=1).enqueued == 1
    assert len(SEEN) == 1


def test_inline_pace_sleeps_between_tasks() -> None:
    """Three inline tasks, two pauses: none before the first."""
    MicroRegionFactory.create_batch(3)
    with patch(_SLEEP) as sleep:
        _run(_command(), pace=1.5)
    assert [call.args for call in sleep.call_args_list] == [(1.5,), (1.5,)]


def test_no_pace_never_sleeps() -> None:
    """A pace of 0 runs the tasks back to back."""
    MicroRegionFactory.create_batch(2)
    with patch(_SLEEP) as sleep:
        _run(_command(), pace=0)
    sleep.assert_not_called()


def test_a_deferring_backend_staggers_run_after() -> None:
    """Task i runs i × pace after the first, and nothing sleeps."""
    MicroRegionFactory.create_batch(3)
    fake = MagicMock()
    before = timezone.now()
    with (
        patch("apps.core.command_tasks._defers", return_value=True),
        patch("apps.core.command_tasks._waiting", return_value=set()),
        patch(_SLEEP) as sleep,
    ):
        _run(_command(), fake, pace=10)
    sleep.assert_not_called()
    fake.enqueue.assert_not_called()
    after = [call.kwargs["run_after"] for call in fake.using.call_args_list]
    assert len(after) == 3
    gaps = [b - a for a, b in zip(after, after[1:], strict=False)]
    assert gaps == [timedelta(seconds=10), timedelta(seconds=10)]
    assert after[0] >= before
    assert fake.using.return_value.enqueue.call_count == 3


def _waiting_row(region_pk: int, status: str = "READY") -> None:
    """Store a task row for ``region_pk`` as the database backend would.

    Written directly rather than through enqueue, as
    tests/core/test_remote_commands.py does: what is under test is the
    query, not django-tasks-db's enqueue.
    """
    DBTaskResult.objects.create(
        task_path=record_task.module_path,
        args_kwargs={"args": [region_pk], "kwargs": {}},
        status=status,
        run_after=timezone.now(),
        backend_name="default",
    )


@override_settings(TASKS=DATABASE)
def test_a_row_already_waiting_is_not_enqueued_again() -> None:
    """A row whose task is READY is skipped; the others are enqueued."""
    waiting, fresh = MicroRegionFactory.create_batch(2)
    _waiting_row(waiting.pk)
    command = _command()
    fake = MagicMock(module_path=record_task.module_path)
    fake.get_backend.return_value = record_task.get_backend()
    counts = _run(command, fake)
    assert (counts.enqueued, counts.already_queued) == (1, 1)
    assert command.counts["already_queued"] == 1
    assert [call.args for call in fake.enqueue.call_args_list] == [(fresh.pk,)]


@override_settings(TASKS=DATABASE)
def test_a_finished_task_does_not_block_a_new_one() -> None:
    """Only a waiting task counts: a row whose task ran is enqueued again."""
    region = MicroRegionFactory.create()
    _waiting_row(region.pk, status="SUCCESSFUL")
    fake = MagicMock(module_path=record_task.module_path)
    fake.get_backend.return_value = record_task.get_backend()
    assert _run(_command(), fake).enqueued == 1


def test_a_failed_inline_task_is_counted() -> None:
    """Every failure is counted; the batch carries on."""
    MicroRegionFactory.create_batch(2)
    command = _command()
    counts = _run(command, failing_task)
    assert (counts.enqueued, counts.failed) == (2, 2)
    assert command.counts["failed"] == 2


def test_an_outside_task_must_be_paced() -> None:
    """A task registered with calls_outside is refused without a pace."""
    calls_outside(record_task)
    try:
        with pytest.raises(ValueError, match="pace it"):
            _run(_command())
        assert _run(_command(), pace=0).enqueued == 0
    finally:
        _OUTSIDE_TASKS.discard(record_task.module_path)


def test_a_task_above_the_bulk_priority_is_refused() -> None:
    """User-facing work on the queue must never wait behind a bulk run."""
    with pytest.raises(ValueError, match="priority"):
        _run(_command(), default_priority_task)


def test_the_breaker_opens_and_closes() -> None:
    """back_off opens it for a while; clear_back_off closes it at once."""
    assert not backing_off("test-service")
    back_off("test-service", 60)
    assert backing_off("test-service")
    assert not backing_off("another-service")
    clear_back_off("test-service")
    assert not backing_off("test-service")


def test_the_breaker_closes_when_its_time_is_up() -> None:
    """A mark whose time has passed reads as closed."""
    back_off("test-service", 60)
    later = timezone.now() + timedelta(seconds=61)
    with patch("apps.core.command_tasks.timezone.now", return_value=later):
        assert not backing_off("test-service")
