"""
apps/core/command_tasks.py — the one shape every bulk command enqueues in (SNOW-1397).

CLAUDE.md command rule 7: a command whose work is thousands of independent
row updates enqueues one ``django_tasks`` task per row and returns, rather
than walking them in one process. SNOW-1345 (``render_ski_tours``) and
SNOW-1346 (the two render-model rebuilds) each wrote that loop by hand;
``enqueue_each`` is it, written once, so every bulk command keeps Hugo's
five rules (3 Nov 2026) the same way:

1. **Async.** ``enqueue_each`` streams the candidates newest-id-first
   (``iterate_rows``, so stdout is the countdown) and enqueues one task
   per row. A read-only run counts and enqueues nothing.
2. **Idempotent per row.** The task, not this module, holds that half of
   the contract: it takes a primary key, re-reads its row, returns early
   when the row no longer needs work, and writes in one transaction.
3. **Recoverable.** A command's candidates are the rows still needing
   work, so a run that stops early leaves the rest as candidates for the
   next. A row that already has a task waiting (``READY``) is not enqueued
   again: the ops server's one-at-a-time guard sees a queued *command*,
   never the row tasks it left behind.
4. **Paced.** A task that calls an outside service is registered with
   ``calls_outside`` and must be given ``pace``. On a backend that defers
   (``DatabaseBackend`` in production), task *i* is enqueued with
   ``run_after = start + i × pace``, which spreads the queue out without a
   worker sleeping; under ``ImmediateBackend`` (dev, test, staging) the
   tasks run inline, so the helper sleeps between them. The stagger alone
   is not a rate limit: tasks whose start times passed while the worker
   was busy or down become due together. So an outside task also calls
   ``wait_for_slot(service, pace)`` before it calls out, which reserves the
   next free start for that service in the shared cache and sleeps until
   it: whatever the queue does, two calls are never closer than ``pace``.
   On a 429 or an outage a task calls ``back_off``; every later task sees
   ``backing_off`` and returns at once, leaving its row a candidate.
5. **MCP runnable.** ``tests/core/test_command_tasks_guard.py`` fails a
   command that calls ``enqueue_each`` without ``mcp_runnable = True``.

Full rationale: docs/decisions/bulk-commands-enqueue-each-row.md.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone
from django_tasks import TaskResultStatus
from django_tasks.base import Task
from django_tasks_db import DatabaseBackend
from django_tasks_db.models import DBTaskResult

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)

# The highest priority a bulk row task may run at: below the default (0),
# so alert emails and pushes on the same queue are never stuck behind it.
BULK_TASK_PRIORITY = -50

# The module paths of the tasks that call an outside service; enqueue_each
# refuses to enqueue one of them without a pace.
_OUTSIDE_TASKS: set[str] = set()

_BACKOFF_KEY = "command-tasks:back-off:{}"
_SLOT_KEY = "command-tasks:next-slot:{}"


def calls_outside(task: Task[Any, Any]) -> Task[Any, Any]:
    """Record that ``task`` calls an outside service, so it must be paced.

    Applied once, where the task is defined:
    ``render_tour_task = calls_outside(render_tour_task)``.

    Args:
        task: The task.

    Returns:
        The same task, unchanged.

    """
    _OUTSIDE_TASKS.add(task.module_path)
    return task


def is_outside(task: Task[Any, Any]) -> bool:
    """Return whether ``task`` was registered with ``calls_outside``."""
    return task.module_path in _OUTSIDE_TASKS


def back_off(service: str, seconds: float) -> None:
    """Open the breaker for ``service``: tasks calling it stop for ``seconds``.

    Called by a task that met a 429 or an outage. The mark lives in the
    shared cache, so every worker sees it.

    Args:
        service: A short name for the outside service, e.g. ``"terrain-tiles"``.
        seconds: How long later tasks should leave it alone.

    """
    until = timezone.now() + timedelta(seconds=seconds)
    cache.set(_BACKOFF_KEY.format(service), until.isoformat(), timeout=seconds)
    logger.warning("command tasks: backing off %s until %s", service, until)


def backing_off(service: str) -> bool:
    """Return whether the breaker for ``service`` is open.

    A task checks this before it calls the service, and returns without
    writing when it is: its row stays a candidate for the next run.

    Args:
        service: The name ``back_off`` was called with.

    """
    raw = cache.get(_BACKOFF_KEY.format(service))
    return raw is not None and datetime.fromisoformat(raw) > timezone.now()


def clear_back_off(service: str) -> None:
    """Close the breaker for ``service`` now, for tests and an operator.

    Args:
        service: The name ``back_off`` was called with.

    """
    cache.delete(_BACKOFF_KEY.format(service))


def wait_for_slot(service: str, pace: float) -> float:
    """Wait until ``pace`` seconds after the last call to ``service``.

    The execution-time half of the pacing: a task calls this just before it
    calls the outside service. The next free start is a millisecond count
    in the shared cache, reserved with ``incr``, so each caller gets its
    own slot ``pace`` after the one before. The reservation is exact on a
    backend whose ``incr`` is atomic and for one worker on any backend
    (production runs one ``db_worker``); two workers on the database cache
    can, rarely, share a slot.

    Args:
        service: A short name for the outside service.
        pace: Seconds between calls; 0 or less returns at once.

    Returns:
        How long it waited, in seconds.

    """
    if pace <= 0:
        return 0.0
    key = _SLOT_KEY.format(service)
    step = int(pace * 1000)
    now = int(time.time() * 1000)
    # Seed the counter with "now" when it is absent; add is a no-op when
    # another task got there first. An expired counter is just absent.
    cache.add(key, now - step, timeout=None)
    try:
        slot = cache.incr(key, step)
    except ValueError:
        # It expired between add and incr: start from now.
        cache.set(key, now, timeout=None)
        slot = now
    if slot < now:
        # The last reservation is in the past: bring the counter up to now,
        # so an idle spell does not bank a burst of instant slots.
        cache.set(key, now, timeout=None)
        slot = now
    wait = (slot - now) / 1000
    if wait > 0:
        time.sleep(wait)
    return wait


@dataclass(frozen=True)
class EnqueueCounts:
    """What one ``enqueue_each`` call did.

    Attributes:
        enqueued: Tasks enqueued (or, read-only, that would be).
        already_queued: Rows skipped because a task for them was waiting.
        failed: Tasks an inline backend ran that failed.

    """

    enqueued: int
    already_queued: int
    failed: int


def _args_key(args: tuple[Any, ...]) -> str:
    """Return a task's arguments as the database backend stores them, as text.

    Args:
        args: The positional arguments the task is enqueued with.

    """
    stored = json.loads(json.dumps({"args": list(args), "kwargs": {}}))
    return json.dumps(stored, sort_keys=True)


def _defers(task: Task[Any, Any]) -> bool:
    """Return whether ``task``'s backend holds a task until its ``run_after``.

    The database backend (production) does; ``ImmediateBackend`` runs a
    task as it is enqueued and refuses a ``run_after``.

    Args:
        task: The task.

    """
    return isinstance(task.get_backend(), DatabaseBackend)


def _waiting(task: Task[Any, Any]) -> set[str]:
    """Return the argument keys of every ``READY`` row of ``task``.

    Only the database backend can be asked; any other answers an empty set.

    Args:
        task: The task.

    """
    if not _defers(task):
        return set()
    rows = DBTaskResult.objects.filter(
        task_path=task.module_path, status=TaskResultStatus.READY
    ).values_list("args_kwargs", flat=True)
    return {json.dumps(stored, sort_keys=True) for stored in rows.iterator()}


def _refuse_unsafe(task: Task[Any, Any], pace: float | None) -> None:
    """Refuse a task above the bulk priority, or an outside task unpaced.

    Args:
        task: The row task.
        pace: The pace it was given.

    Raises:
        ValueError: Either rule is broken.

    """
    # A test's stand-in task carries no integer priority; only a real one
    # is held to the rule.
    priority = task.priority
    if isinstance(priority, int) and priority > BULK_TASK_PRIORITY:
        raise ValueError(
            f"{task.module_path} runs at priority {priority}; a bulk "
            f"row task runs at {BULK_TASK_PRIORITY} or lower"
        )
    if is_outside(task) and pace is None:
        raise ValueError(f"{task.module_path} calls an outside service: pace it")


def _enqueue_one(
    task: Task[Any, Any],
    row_args: tuple[Any, ...],
    pace: float | None,
    defers: bool,
    start: datetime,
    n: int,
) -> bool:
    """Enqueue one row's task, paced; return whether it ran inline and failed.

    Args:
        task: The row task.
        row_args: Its arguments.
        pace: Seconds between task starts; None or 0 for none.
        defers: Whether the backend honours ``run_after``.
        start: When the first task starts.
        n: How many tasks were enqueued before this one.

    """
    if pace and defers:
        run_after = start + timedelta(seconds=pace * n)
        result = task.using(run_after=run_after).enqueue(*row_args)
    else:
        if pace and n:
            time.sleep(pace)
        result = task.enqueue(*row_args)
    # Only an inline backend has run the task by now; a queued one
    # reports its own failure on the task result.
    if result.status == TaskResultStatus.FAILED:
        logger.error("%s: task for row %s failed", task.name, row_args)
        return True
    return False


def enqueue_each(
    command: RegisteredCommand,
    rows: Iterable[Any],
    task: Task[Any, Any],
    *,
    args: Callable[[Any], tuple[Any, ...]],
    describe: Callable[[Any], object],
    commit: bool,
    verbosity: int,
    tally_as: str = "enqueued",
    limit: int = 0,
    pace: float | None = None,
    chunk_size: int | None = None,
) -> EnqueueCounts:
    """Enqueue ``task`` once per row of ``rows``; the shape of a bulk command.

    Tallies ``tally_as``, ``already_queued`` and ``failed`` on ``command``
    (zeros included), so a command calling this twice adds to one register
    row.

    Args:
        command: The calling command, for its stdout and its tally.
        rows: The candidates: a queryset of rows still needing work, slim
            (only what ``args`` and ``describe`` read). Streamed newest id
            first.
        task: The row task. Its priority must be ``BULK_TASK_PRIORITY``
            or lower.
        args: Returns a row's task arguments, JSON-serialisable (a primary
            key, never the row).
        describe: Returns a row's countdown label from loaded fields only.
        commit: Enqueue; without it, count only.
        verbosity: Django's ``--verbosity``.
        tally_as: The count the enqueued tasks are tallied under.
        limit: Enqueue at most this many; 0 for every candidate.
        pace: Seconds between one task's start and the next. Required for
            a task registered with ``calls_outside``; 0 disables it.
        chunk_size: Forwarded to the queryset iterator.

    Returns:
        What was enqueued, skipped and failed.

    Raises:
        ValueError: An outside task with no pace, or a task above
            ``BULK_TASK_PRIORITY``.

    """
    _refuse_unsafe(task, pace)
    defers = _defers(task)
    waiting = _waiting(task) if commit else set()
    start = timezone.now()
    enqueued = already_queued = failed = 0
    for row in iterate_rows(
        command, rows, verbosity=verbosity, describe=describe, chunk_size=chunk_size
    ):
        if limit and enqueued >= limit:
            break
        row_args = args(row)
        if commit and _args_key(row_args) in waiting:
            already_queued += 1
            continue
        if commit and _enqueue_one(task, row_args, pace, defers, start, enqueued):
            failed += 1
        enqueued += 1

    command.tally(tally_as, enqueued)
    command.tally("already_queued", already_queued)
    command.tally("failed", failed)
    return EnqueueCounts(enqueued, already_queued, failed)
