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
  - A list of derived units is walked in order, with a countdown.
  - ``wait_for_slot`` spaces calls ``pace`` apart even when several tasks
    are due at once, and banks no burst across an idle spell.
  - ``wait_for_slot`` waits for another caller's lock, keeps the larger
    gap when callers share a service at different paces, takes a stale
    lock over only once it has expired, and leaves no lock behind
    (SNOW-1477). The lock is a database ``Lease`` (SNOW-1490).
  - ``task_queue_health`` is in tests/core/test_task_queue.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from django_tasks import task
from django_tasks_db.models import DBTaskResult

from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import (
    _OUTSIDE_TASKS,
    _SLOT_KEY,
    _SLOT_LOCK_KEY,
    BULK_TASK_PRIORITY,
    back_off,
    backing_off,
    calls_outside,
    clear_back_off,
    enqueue_each,
    wait_for_slot,
)
from apps.core.models import Lease, LeaseManager
from apps.regions.models import MicroRegion
from tests.factories import LeaseFactory, MicroRegionFactory

pytestmark = pytest.mark.django_db

DATABASE = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

_SLEEP = "apps.core.command_tasks.time.sleep"

_LOCK = _SLOT_LOCK_KEY.format("test-service")

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
    cache.delete(_SLOT_KEY.format("test-service"))
    yield
    clear_back_off("test-service")
    cache.delete(_SLOT_KEY.format("test-service"))


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


def test_a_list_of_units_is_walked_in_order() -> None:
    """Derived units keep the order given and count down by their label."""
    command = _command()
    counts = enqueue_each(
        command,
        [3, 1, 2],
        record_task,
        args=lambda unit: (unit,),
        describe=lambda unit: f"unit {unit}",
        commit=True,
        verbosity=1,
    )
    assert counts.enqueued == 3
    assert SEEN == [3, 1, 2]
    out = command.stdout.getvalue()
    assert "3 unit(s) remaining" in out
    assert "unit 2" in out


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


class _Clock:
    """A clock the slot tests move by hand; sleeping moves it on."""

    def __init__(self) -> None:
        """Start at an arbitrary second."""
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def time(self) -> float:
        """Return the current second."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the pause and move the clock past it."""
        self.slept.append(seconds)
        self.now += seconds


def _with_clock(clock: _Clock) -> Any:
    """Patch command_tasks' time module with ``clock``."""
    return patch("apps.core.command_tasks.time", clock)


def test_the_first_call_does_not_wait() -> None:
    """Nothing has called the service yet, so the slot is now."""
    clock = _Clock()
    with _with_clock(clock):
        assert wait_for_slot("test-service", 2.0) == 0.0
    assert clock.slept == []


def test_overdue_tasks_due_together_are_spaced_out() -> None:
    """Three tasks starting in the same instant start 2 s apart."""
    clock = _Clock()
    with _with_clock(clock):
        waits = []
        for _ in range(3):
            start = clock.now
            waits.append(wait_for_slot("test-service", 2.0))
            clock.now = start  # the next task started at the same moment
    assert waits == [0.0, 2.0, 4.0]


def test_an_idle_spell_banks_no_burst() -> None:
    """After a long quiet spell the next two calls are still a pace apart."""
    clock = _Clock()
    with _with_clock(clock):
        wait_for_slot("test-service", 2.0)
        clock.now += 3600
        assert wait_for_slot("test-service", 2.0) == 0.0
        assert wait_for_slot("test-service", 2.0) == 2.0


def test_no_pace_never_waits() -> None:
    """A pace of 0 returns at once and touches nothing."""
    clock = _Clock()
    with _with_clock(clock):
        assert wait_for_slot("test-service", 0) == 0.0
    assert cache.get(_SLOT_KEY.format("test-service")) is None


def test_a_held_lock_is_waited_for_and_its_slot_respected() -> None:
    """Worker B holds the lease while it reserves; A waits, then starts after B."""
    clock = _Clock()
    held = LeaseFactory.create(key=_LOCK)
    polls: list[float] = []
    clock_sleep = clock.sleep

    def b_finishes(seconds: float) -> None:
        """On A's first poll, B writes its slot (now) and lets the lease go."""
        polls.append(seconds)
        if len(polls) == 1:
            cache.set(_SLOT_KEY.format("test-service"), (int(clock.now * 1000), 2000))
            held.release()
        clock_sleep(seconds)

    clock.sleep = b_finishes  # type: ignore[method-assign]
    with _with_clock(clock):
        b_start = clock.now
        wait = wait_for_slot("test-service", 2.0)
    a_start = clock.now
    assert len(polls) >= 2  # one poll for the lock, then the wait
    assert a_start - b_start >= 2.0
    assert wait > 0


def test_callers_at_different_paces_keep_the_larger_gap() -> None:
    """A 0.5 s caller after a 2 s one, and the reverse, both wait 2 s."""
    clock = _Clock()
    with _with_clock(clock):
        start = clock.now
        assert wait_for_slot("test-service", 2.0) == 0.0
        clock.now = start
        assert wait_for_slot("test-service", 0.5) == 2.0
    cache.delete(_SLOT_KEY.format("test-service"))
    with _with_clock(clock):
        start = clock.now
        assert wait_for_slot("test-service", 0.5) == 0.0
        clock.now = start
        assert wait_for_slot("test-service", 2.0) == 2.0


def test_a_stale_lock_is_waited_out_never_bypassed() -> None:
    """A crashed holder's lease is waited out until it expires, then taken over."""
    clock = _Clock()
    wall = [datetime(2026, 11, 12, 9, 0, tzinfo=UTC)]
    # Left by a holder that crashed: it expires 0.2 s from now.
    stale = LeaseFactory.create(key=_LOCK, expires_at=wall[0] + timedelta(seconds=0.2))
    attempts: list[bool] = []
    real_acquire = LeaseManager.acquire

    def counting_acquire(self: LeaseManager, *args: Any, **kwargs: Any) -> Any:
        """Record each try at the lease."""
        lease = real_acquire(self, *args, **kwargs)
        attempts.append(lease is not None)
        return lease

    def poll(seconds: float) -> None:
        """Move the database's clock on, so the stale lease can expire."""
        wall[0] += timedelta(seconds=0.05)
        clock.now += seconds

    clock.sleep = poll  # type: ignore[method-assign]
    with (
        _with_clock(clock),
        patch("apps.core.models.timezone.now", side_effect=lambda: wall[0]),
        patch.object(LeaseManager, "acquire", counting_acquire),
    ):
        assert wait_for_slot("test-service", 2.0) == 0.0
    # It kept trying while the lease was live and took it only once expired.
    assert attempts[-1] is True
    assert not any(attempts[:-1])
    assert len(attempts) == 5  # live at +0, +0.05, +0.1, +0.15; expired at +0.2
    # The holder that crashed cannot free what was taken over from it, and
    # the taker released its own: nothing is left.
    assert stale.release() is False
    assert not Lease.objects.filter(key=_LOCK).exists()


def test_no_lock_is_left_behind() -> None:
    """After many calls only the slot key remains; the lease is gone."""
    clock = _Clock()
    with _with_clock(clock):
        for _ in range(5):
            wait_for_slot("test-service", 1.0)
    assert not Lease.objects.filter(key=_LOCK).exists()
    assert cache.get(_SLOT_KEY.format("test-service")) is not None


def test_an_unexpected_slot_value_is_treated_as_absent() -> None:
    """A bare int (the old shape) under the key does not break the call."""
    clock = _Clock()
    cache.set(_SLOT_KEY.format("test-service"), 123456, timeout=None)
    with _with_clock(clock):
        assert wait_for_slot("test-service", 2.0) == 0.0
    assert cache.get(_SLOT_KEY.format("test-service")) == (
        int(clock.now * 1000),
        2000,
    )
