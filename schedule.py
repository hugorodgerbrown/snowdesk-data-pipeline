"""
schedule.py — APScheduler job registry for Snowdesk.

Defines ``build_scheduler()``, which constructs a :class:`BlockingScheduler`
pre-loaded with the recurring data-pipeline job:

- **fetch_bulletins** — fires at ``:00`` and ``:05`` of every hour,
  running ``fetch_bulletins --source SLF ALBINA METEOFRANCE --commit``.
- **fetch_weather** — fires at 00:00, 06:00, 12:00 and 18:00 UTC, running
  ``fetch_weather --commit``. Four times a day because a location has no
  live on-demand fetch behind its page render the way a bulletin region
  does: the scheduled batch is the only thing that keeps today's row
  current, and today's row is the one every surface reads.
- **purge_request_logs** — fires at 03:30 UTC, running
  ``purge_request_logs --commit``. Enforces the ``RequestLog`` retention
  window the Privacy Policy states (SNOW-775); until this job existed
  nothing deleted a row, while the page claimed fourteen days.

Every job carries guard settings (``coalesce=True``, ``max_instances=1``,
``misfire_grace_time=300``) so a slow run does not stack up duplicate
executions.

Entry point
-----------
The scheduler is started by the ``run_scheduler`` management command::

    python manage.py run_scheduler

which is the ``startCommand`` for the ``snowdesk-scheduler`` Render
Background Worker (see ``render.yaml``).

Import safety
-------------
This module is imported before ``django.setup()`` completes in some test
contexts, so **no Django symbols are imported at module level**.
``call_command`` is imported lazily inside each job function, which is safe
because Django's app registry is always fully loaded by the time
APScheduler fires a job (the entry point is a management command, so
``setup()`` runs first).

Platform notes
--------------
APScheduler 3.10+ requires the ``tzdata`` package on Windows for timezone
resolution. Snowdesk runs on Linux (Render) and macOS (development), so no
extra package is needed.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def _run_fetch_bulletins() -> None:
    """Invoke the ``fetch_bulletins`` management command for all sources."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing fetch_bulletins")
    call_command(
        "fetch_bulletins",
        "--source",
        "SLF",
        "ALBINA",
        "METEOFRANCE",
        "--commit",
    )


def _run_fetch_weather() -> None:
    """Invoke the ``fetch_weather`` management command for every active location."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing fetch_weather")
    call_command("fetch_weather", "--commit")


def _run_purge_request_logs() -> None:
    """Invoke ``purge_request_logs`` to enforce the RequestLog retention window."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing purge_request_logs")
    call_command("purge_request_logs", "--commit")


def build_scheduler() -> BlockingScheduler:
    """Build and return a configured :class:`BlockingScheduler`.

    The scheduler is *not* started here — call ``.start()`` on the
    returned instance to begin blocking execution.

    Returns
    -------
    BlockingScheduler
        A scheduler with three jobs pre-registered:

        ``fetch_bulletins``
            Cron: ``minute=0,5`` (every hour at :00 and :05 UTC).
        ``fetch_weather``
            Cron: ``hour=0,6,12,18`` (four times a day, on the hour UTC).
        ``purge_request_logs``
            Cron: ``hour=3, minute=30`` (once a day, off the fetch hours).

    """
    scheduler = BlockingScheduler(timezone="UTC")

    _common = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 300,
    }

    scheduler.add_job(
        _run_fetch_bulletins,
        trigger=CronTrigger(minute="0,5", timezone="UTC"),
        id="fetch_bulletins",
        **_common,
    )

    scheduler.add_job(
        _run_fetch_weather,
        trigger=CronTrigger(hour="0,6,12,18", minute=0, timezone="UTC"),
        id="fetch_weather",
        **_common,
    )

    # SNOW-775: deliberately at :30 on an hour no fetch job runs. The purge
    # holds a delete transaction over a table the request path writes to, so
    # overlapping it with an ingest run would contend for no reason.
    scheduler.add_job(
        _run_purge_request_logs,
        trigger=CronTrigger(hour=3, minute=30, timezone="UTC"),
        id="purge_request_logs",
        **_common,
    )

    logger.info(
        "schedule: built scheduler with %d jobs: %s",
        len(scheduler.get_jobs()),
        [job.id for job in scheduler.get_jobs()],
    )

    return scheduler
