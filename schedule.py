"""
schedule.py — APScheduler job registry for Snowdesk.

Defines ``build_scheduler()``, which constructs a :class:`BlockingScheduler`
pre-loaded with the recurring data-pipeline jobs:

- **fetch_bulletins** — fires at ``:00`` and ``:05`` of every hour,
  running ``fetch_bulletins --source SLF ALBINA METEOFRANCE --commit``.
- **fetch_weather** — fires at 00:00, 06:00, 12:00 and 18:00 UTC, running
  ``fetch_weather --commit``. Four times a day because a location has no
  live on-demand fetch behind its page render the way a bulletin region
  does: the scheduled batch is the only thing that keeps today's row
  current, and today's row is the one every surface reads.
- **backfill_weather** — fires at :20 every hour, running
  ``backfill_weather --commit``. One budgeted sweep of the missing past
  ``Weather`` days, newest fortnight first (SNOW-1245): the budget is this
  hour's share of what ``fetch_weather`` leaves of Open-Meteo's free daily
  quota, so the two never throttle each other, and each run resumes where
  the last one stopped. At :20 so it never starts beside ``fetch_weather``
  or ``fetch_bulletins``.
- **mirror_reference_data** — fires at :40 every hour, running
  ``mirror_reference_data --commit``. Copies the curated places, passes
  and resorts into the reference-data service's tables, which the v2 data
  feeds serve (SNOW-1271). Hourly because an edit in the resort sheet or
  the map-admin overlays should reach the feed the same hour; a run with
  nothing changed writes nothing. At :40, clear of every other hourly job.
- **import_ski_tours** — fires on Mondays at 02:30 UTC, running
  ``import_ski_tours --commit``. Imports swisstopo's ski-touring network
  and SAC's tours into the reference-data service and applies them here
  (SNOW-1276). Weekly because swisstopo releases the data about once a
  year; a run against an unchanged release writes nothing.
- **purge_request_logs** — fires at 03:30 UTC, running
  ``purge_request_logs --commit``. Enforces the ``RequestLog`` retention
  window the Privacy Policy states (SNOW-775); until this job existed
  nothing deleted a row, while the page claimed fourteen days.
- **fill_what3words** — fires at 04:00 UTC, running
  ``fill_what3words --commit``. Sweeps up locations minted without an
  address: a field observation's, and any row where a mint-time conversion
  failed. A no-op in an environment with no ``WHAT3WORDS_API_KEY``, which
  is why it can be registered unconditionally (SNOW-881). Daily rather than
  hourly because the estate grows with user activity, not with the clock,
  and an address that arrives a few hours late costs nothing — the surface
  renders a coordinate pair until it does.
- **reconcile_slope_samples** — fires at 04:30 UTC, running
  ``backfill_route_slope_samples --commit``, then
  ``backfill_trip_slope_samples --commit``. The safety net for
  a sampling task whose enqueue failed after its route or trip had
  committed (SNOW-1228): the enqueue helpers log such a failure rather
  than raise it, which leaves ``slope_samples`` null, and both commands
  select exactly those rows. Deliberately NOT capped with ``--limit``
  (SNOW-1243): both commands walk newest-first and leave an unsampleable
  row (a route outside terrain coverage) eligible, so a cap would let the
  newest few such rows crowd out older ones whose enqueue really failed.
  The commands' own one-second ``--delay`` paces the tile requests. The
  trip pass runs even if the route pass raises, and the first failure is
  re-raised afterwards, so it still reaches the worker log.
- **render_ski_tours** — fires at 05:30 UTC, running
  ``render_ski_tours --commit`` (SNOW-1316, replacing SNOW-1288's tour pass
  in ``reconcile_slope_samples``). The reference-data service walks the
  terrain under every SAC tour not yet walked from its current line and
  terrain model, stores the walk and the drawing beside the document for
  the v2 ``ski-tours`` feed, and applies them to this host's ``SkiTour``
  rows. Its own job, not part of the weekly import, so a terrain outage
  never fails an import and a builder change is redrawn within a day; on
  a day with nothing new, every tour's fingerprint matches and it writes
  nothing.
- **check_resort_locations** — fires at 05:00 UTC, running
  ``link_resort_locations --check``. The only job here that writes nothing:
  it asks whether any geocoded resort has no ``ResortLocation``, which means
  that resort's page renders no Forecasts section while nothing anywhere
  says so (SNOW-885). It raises on a non-empty answer, so the failure shows
  in the worker log rather than needing somebody to read a count.
- **purge_expired_oauth_tokens** — fires at 03:45 UTC, running
  ``purge_expired_oauth_tokens --commit``. Deletes expired MCP OAuth
  tokens and codes with django-oauth-toolkit's ``clear_expired()``
  (SNOW-1193); nothing else removes them, and a Claude connector
  refreshing hourly writes two rows an hour.

Every job carries guard settings (``coalesce=True``, ``max_instances=1``,
``misfire_grace_time=300``) so a slow run does not stack up duplicate
executions.

Switches (SNOW-1201)
--------------------
Each job checks a waffle switch, ``schedule_<job id>`` (for example
``schedule_fetch_bulletins``), when it fires, and skips that run when the
switch is off, so an operator can pause a job from ``/admin/waffle/switch/``
without a deploy. Each switch is declared in
``apps/core/fixtures/waffle_switches.json`` (``tests/test_schedule.py``
fails a job without one) and created by ``sync_waffle_switches --commit``
on every deploy. A job whose switch row is missing runs and logs a
warning: a deploy that races the sync must never silently stop ingest.

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

import functools
import logging
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# The prefix of every job's waffle switch (SNOW-1201).
SWITCH_PREFIX = "schedule_"


def switch_name(job_id: str) -> str:
    """Return the waffle switch that turns job ``job_id`` on and off."""
    return f"{SWITCH_PREFIX}{job_id}"


def switched(job_id: str, func: Callable[[], None]) -> Callable[[], None]:
    """Wrap ``func`` so it runs only while its job's switch is on (SNOW-1201).

    The switch is read when the job fires, so a change in the admin applies
    from the next run without restarting the worker.

    Args:
        job_id: The job's APScheduler id.
        func: The job function.

    Returns:
        The wrapped job function.

    """

    @functools.wraps(func)
    def run() -> None:
        """Run the job unless its switch is off; run it if the switch is missing."""
        from waffle.models import (  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
            Switch,
        )

        name = switch_name(job_id)
        switch = Switch.objects.filter(name=name).only("active").first()
        if switch is None:
            logger.warning(
                "schedule: switch %s is missing; running %s (sync_waffle_switches)",
                name,
                job_id,
            )
        elif not switch.active:
            logger.info("schedule: skipping %s, switch %s is off", job_id, name)
            return
        func()

    return run


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


def _run_backfill_weather() -> None:
    """Invoke ``backfill_weather`` for one budgeted sweep (SNOW-1245)."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing backfill_weather")
    call_command("backfill_weather", "--commit")


def _run_mirror_reference_data() -> None:
    """Invoke ``mirror_reference_data``: the estate into the service (SNOW-1271)."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing mirror_reference_data")
    call_command("mirror_reference_data", "--commit")


def _run_import_ski_tours() -> None:
    """Invoke ``import_ski_tours``: swisstopo's lines into the service (SNOW-1276)."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing import_ski_tours")
    call_command("import_ski_tours", "--commit")


def _run_purge_request_logs() -> None:
    """Invoke ``purge_request_logs`` to enforce the RequestLog retention window."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing purge_request_logs")
    call_command("purge_request_logs", "--commit")


def _run_fill_what3words() -> None:
    """Invoke ``fill_what3words`` to address any location still without one."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing fill_what3words")
    call_command("fill_what3words", "--commit")


def _run_reconcile_slope_samples() -> None:
    """Sample any route or trip whose terrain sampling never ran (SNOW-1228).

    Runs the two slope backfill commands — routes, then trips. SAC's
    tours are the service's own job since SNOW-1316
    (``_run_render_ski_tours``). Each raises ``CommandError`` when any row failed;
    every later pass must still run after a failed one, so the first error
    is held and re-raised once all have run.
    """
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing reconcile_slope_samples")
    first_error: Exception | None = None
    for command in (
        "backfill_route_slope_samples",
        "backfill_trip_slope_samples",
    ):
        try:
            call_command(command, "--commit")
        except Exception as exc:  # noqa: BLE001 — broad catch intentional: one pass failing must not skip the other; re-raised below
            logger.exception("schedule: %s failed", command)
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _run_render_ski_tours() -> None:
    """Invoke ``render_ski_tours --commit``: walk and draw tours once (SNOW-1316)."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing render_ski_tours")
    call_command("render_ski_tours", "--commit")


def _run_check_resort_locations() -> None:
    """Invoke ``link_resort_locations --check`` to detect unlinked resorts.

    SNOW-885. Writes nothing — it only asks whether any geocoded resort has
    no location link, which means a resort page rendering no Forecasts
    section with nothing anywhere reporting it. A non-zero exit is the point:
    the ``CommandError`` it raises on a non-empty answer surfaces in the
    worker log, which is the alarm.
    """
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing link_resort_locations --check")
    call_command("link_resort_locations", "--check")


def _run_purge_expired_oauth_tokens() -> None:
    """Invoke ``purge_expired_oauth_tokens`` to delete expired OAuth tokens."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing purge_expired_oauth_tokens")
    call_command("purge_expired_oauth_tokens", "--commit")


def _run_prune_command_runs() -> None:
    """Invoke ``prune_command_runs`` to keep the run register small (SNOW-1189)."""
    from django.core.management import (
        call_command,  # noqa: PLC0415 — lazy import; module is import-safe before django.setup(), see docstring
    )

    logger.info("schedule: firing prune_command_runs")
    call_command("prune_command_runs", "--commit")


def build_scheduler() -> BlockingScheduler:
    """Build and return a configured :class:`BlockingScheduler`.

    The scheduler is *not* started here — call ``.start()`` on the
    returned instance to begin blocking execution.

    Returns
    -------
    BlockingScheduler
        A scheduler with eleven jobs pre-registered:

        ``fetch_bulletins``
            Cron: ``minute=0,5`` (every hour at :00 and :05 UTC).
        ``fetch_weather``
            Cron: ``hour=0,6,12,18`` (four times a day, on the hour UTC).
        ``backfill_weather``
            Cron: ``minute=20`` (every hour, SNOW-1245).
        ``mirror_reference_data``
            Cron: ``minute=40`` (every hour, SNOW-1271).
        ``import_ski_tours``
            Cron: ``day_of_week=mon, hour=2, minute=30`` (weekly, SNOW-1276).
        ``purge_request_logs``
            Cron: ``hour=3, minute=30`` (once a day, off the fetch hours).
        ``fill_what3words``
            Cron: ``hour=4`` (once a day, off the fetch hours).
        ``reconcile_slope_samples``
            Cron: ``hour=4, minute=30`` (once a day, SNOW-1228).
        ``check_resort_locations``
            Cron: ``hour=5`` (once a day; read-only detector, SNOW-885).
        ``render_ski_tours``
            Cron: ``hour=5, minute=30`` (once a day, SNOW-1316).
        ``purge_expired_oauth_tokens``
            Cron: ``hour=3, minute=45`` (once a day, SNOW-1035).
        ``prune_command_runs``
            Cron: ``hour=3, minute=50`` (once a day, SNOW-1189).

    """
    scheduler = BlockingScheduler(timezone="UTC")

    _common = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 300,
    }

    scheduler.add_job(
        switched("fetch_bulletins", _run_fetch_bulletins),
        trigger=CronTrigger(minute="0,5", timezone="UTC"),
        id="fetch_bulletins",
        **_common,
    )

    scheduler.add_job(
        switched("fetch_weather", _run_fetch_weather),
        trigger=CronTrigger(hour="0,6,12,18", minute=0, timezone="UTC"),
        id="fetch_weather",
        **_common,
    )

    # SNOW-1245: at :20, clear of fetch_weather and fetch_bulletins at :00
    # and :05. The two weather jobs share Open-Meteo's per-IP quota; the
    # sweep's default budget is derived from fetch_weather's share of it
    # (backfill.LIVE_FETCHES_PER_DAY must match the trigger above).
    scheduler.add_job(
        switched("backfill_weather", _run_backfill_weather),
        trigger=CronTrigger(minute=20, timezone="UTC"),
        id="backfill_weather",
        **_common,
    )

    # SNOW-1271: at :40, clear of the :00/:05 fetches and the :20 sweep.
    # Database-only — no upstream call — so it competes for nothing else.
    scheduler.add_job(
        switched("mirror_reference_data", _run_mirror_reference_data),
        trigger=CronTrigger(minute=40, timezone="UTC"),
        id="mirror_reference_data",
        **_common,
    )

    # SNOW-1276: weekly, on the quiet stretch before the 03:xx purges. One
    # 17 MB download and a database reconcile; nothing else competes.
    scheduler.add_job(
        switched("import_ski_tours", _run_import_ski_tours),
        trigger=CronTrigger(day_of_week="mon", hour=2, minute=30, timezone="UTC"),
        id="import_ski_tours",
        **_common,
    )

    # SNOW-775: deliberately at :30 on an hour no fetch job runs. The purge
    # holds a delete transaction over a table the request path writes to, so
    # overlapping it with an ingest run would contend for no reason.
    scheduler.add_job(
        switched("purge_request_logs", _run_purge_request_logs),
        trigger=CronTrigger(hour=3, minute=30, timezone="UTC"),
        id="purge_request_logs",
        **_common,
    )

    # SNOW-881: after the purge, on an hour no fetch job runs. Nothing
    # renders a three word address on a timetable, so the only thing this
    # competes for is the outbound connection pool.
    scheduler.add_job(
        switched("fill_what3words", _run_fill_what3words),
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="fill_what3words",
        **_common,
    )

    # SNOW-1228: after fill_what3words and before the 05:00 detector, on an
    # hour no fetch job runs. Like fill_what3words it competes only for the
    # outbound connection pool, and on most days it has nothing to do.
    scheduler.add_job(
        switched("reconcile_slope_samples", _run_reconcile_slope_samples),
        trigger=CronTrigger(hour=4, minute=30, timezone="UTC"),
        id="reconcile_slope_samples",
        **_common,
    )

    # SNOW-885: a read-only detector, so it competes for nothing — placed on
    # the same quiet stretch as the other two daily jobs, after them, because
    # `link_resort_locations` is what an operator runs in response and there
    # is no reason for the answer to be fresher than daily.
    scheduler.add_job(
        switched("purge_expired_oauth_tokens", _run_purge_expired_oauth_tokens),
        trigger=CronTrigger(hour=3, minute=45, timezone="UTC"),
        id="purge_expired_oauth_tokens",
        **_common,
    )

    # SNOW-1189: closes run-register rows a killed process left RUNNING and
    # deletes scheduled runs past their retention. One small UPDATE and one
    # DELETE, on the quiet stretch with the other two purges.
    scheduler.add_job(
        switched("prune_command_runs", _run_prune_command_runs),
        trigger=CronTrigger(hour=3, minute=50, timezone="UTC"),
        id="prune_command_runs",
        **_common,
    )

    scheduler.add_job(
        switched("check_resort_locations", _run_check_resort_locations),
        trigger=CronTrigger(hour=5, minute=0, timezone="UTC"),
        id="check_resort_locations",
        **_common,
    )

    # SNOW-1316: after the 04:30 reconcile (which used to walk the tours)
    # and the 05:00 detector, on an hour no fetch job runs. It reaches the
    # tile origin only for a tour not yet walked, so most days it asks
    # nothing.
    scheduler.add_job(
        switched("render_ski_tours", _run_render_ski_tours),
        trigger=CronTrigger(hour=5, minute=30, timezone="UTC"),
        id="render_ski_tours",
        **_common,
    )

    logger.info(
        "schedule: built scheduler with %d jobs: %s",
        len(scheduler.get_jobs()),
        [job.id for job in scheduler.get_jobs()],
    )

    return scheduler
