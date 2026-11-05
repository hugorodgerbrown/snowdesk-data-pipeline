"""
apps/public/ingest_report.py — what the scheduled jobs pulled in (SNOW-1423).

``build_report(window, now=...)`` answers one question for staff: over the
last hour, day or week, what did each scheduled feed bring into the
platform, and is any of them stuck? Two readers share the dict it returns:
the staff page at ``/_ingest/`` (``apps.public.debug_views``) and the ops MCP
server's ``ingest_report`` tool. ``check_ingest`` runs hourly and raises for
every feed ``red_feeds`` names, which is how a quiet feed reaches Sentry.

Each ``Feed`` in ``FEEDS`` is one row of the report. Runs come from the
command register (``CommandRun``, scheduler runs only). What landed comes
from the data tables where a command only enqueues (``fetch_weather``) or
where its counts would mislead, and from the run's own ``counts`` where
those are the only record (the estate mirror, the ski-tour import).

A feed is in one of four states:

* ``ok`` — nothing to look at.
* ``warn`` — a run failed in the window, but the latest one succeeded.
* ``red`` — the feed's own staleness rule holds, or its latest run failed.
  ``check_ingest`` alerts on these.
* ``off`` — a bulletin provider with no issue in a week: its season has not
  started or has ended. Never red, so never alerts.

Dependencies run one way: ``apps.mcp_server`` imports this module, and this
module imports nothing from ``apps.mcp_server``.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Max, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import Bulletin
from apps.core.models import CommandRun
from apps.core.task_queue import task_queue_health
from apps.ingest.public import SourceBulletin, SourceRestZone
from apps.locations.models import Location
from apps.routes.services.ski_tour_render import render_tour_task
from apps.weather.models import Weather
from apps.weather.services.station_tasks import backfill_unit_task

logger = logging.getLogger(__name__)

#: The windows the report covers, by key: (length, run-strip cells).
WINDOWS: dict[str, tuple[dt.timedelta, int]] = {
    "1h": (dt.timedelta(hours=1), 12),
    "24h": (dt.timedelta(hours=24), 24),
    "7d": (dt.timedelta(days=7), 42),
}
DEFAULT_WINDOW = "24h"

#: How many entries the Changes list keeps.
CHANGES_SHOWN = 20

OK, WARN, RED, OFF = "ok", "warn", "red", "off"

#: A provider with no issue for this long is off-season.
OFF_SEASON_AFTER = dt.timedelta(days=7)
#: A publishing provider whose last issue is older than this is red.
BULLETIN_STALE_AFTER = dt.timedelta(hours=36)

_FAILED_STATUSES = frozenset(
    {
        CommandRun.STATUS.FAILED,
        CommandRun.STATUS.PARTIAL,
        CommandRun.STATUS.INTERRUPTED,
    }
)
#: A run that stopped as a whole. ``PARTIAL`` is left out: it means some
#: records failed, and a bulletin fetch says which provider's they were.
_WHOLE_RUN_FAILED = frozenset({CommandRun.STATUS.FAILED, CommandRun.STATUS.INTERRUPTED})

_DB_TASK_BACKEND = "django_tasks_db.DatabaseBackend"


@dataclass(frozen=True)
class Context:
    """What every feed's readers share for one report."""

    now: dt.datetime
    start: dt.datetime
    runs: list[CommandRun]
    last_run: CommandRun | None


@dataclass(frozen=True)
class Feed:
    """One row of the report: a scheduled job and what it brings in."""

    key: str
    group: str
    label: str
    job_id: str
    command: str
    #: The row's figures, as ordered (label, value) pairs.
    metrics: Callable[[Context], list[tuple[str, int]]]
    #: A run's non-zero changes, as (label, n) pairs; empty when it changed
    #: nothing. Only these put a run on the Changes list.
    changes: Callable[[dict[str, Any]], list[tuple[str, int]]]
    #: Why the feed is red, or None.
    red_reason: Callable[[Context], str | None]
    #: Whether this run failed, from this feed's point of view.
    run_failed: Callable[[CommandRun], bool] = field(
        default=lambda run: run.status in _FAILED_STATUSES
    )
    #: The newest data the feed holds, when it has a meaningful one.
    latest: Callable[[Context], dt.datetime | None] = field(default=lambda ctx: None)
    #: Whether the feed is off-season (bulletins only).
    off: Callable[[Context], bool] = field(default=lambda ctx: False)
    #: Runs weekly: the strip of a short window says so instead of drawing.
    weekly: bool = False


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------


def _count(counts: dict[str, Any], key: str) -> int:
    """Return ``counts[key]`` as an int, 0 when absent or not a number."""
    value = counts.get(key, 0)
    return value if isinstance(value, int) else 0


def _outcome_total(counts: dict[str, Any], outcome: str) -> int:
    """Sum the host-applied ``{kind}_{outcome}`` counts of one run.

    ``service_*`` keys count the reference-data service's own copy, not what
    reached this host, so they are left out.
    """
    return sum(
        value
        for key, value in counts.items()
        if key.endswith(f"_{outcome}")
        and not key.startswith("service_")
        and isinstance(value, int)
    )


def _no_changes(counts: dict[str, Any]) -> list[tuple[str, int]]:  # noqa: ARG001 — the Feed.changes signature
    """Return no changes: an enqueue-only run lands nothing by itself.

    Its tasks do the writing, so "queued" on every hourly run would bury the
    Changes list in routine. Only its failures are listed.
    """
    return []


def _nonzero(pairs: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    """Return the pairs whose value is not zero."""
    return [(label, n) for label, n in pairs if n]


def _sum_over_runs(ctx: Context, read: Callable[[dict[str, Any]], int]) -> int:
    """Sum one figure read from every run's counts in the window."""
    return sum(read(run.counts or {}) for run in ctx.runs)


def _hours(delta: dt.timedelta) -> str:
    """Format a duration as whole hours or days, for a red reason."""
    hours = delta.total_seconds() / 3600
    return f"{hours / 24:.0f} d" if hours >= 48 else f"{hours:.0f} h"


def _last_run_older_than(limit: dt.timedelta) -> Callable[[Context], str | None]:
    """Return a red rule: no run at all, or the last one too long ago."""

    def rule(ctx: Context) -> str | None:
        """Name the gap when the last run is older than ``limit``."""
        if ctx.last_run is None:
            return "never run"
        age = ctx.now - ctx.last_run.started_at
        return f"last run {_hours(age)} ago" if age > limit else None

    return rule


def _failed_tasks(task_path: str, ctx: Context) -> int:
    """Count the failed tasks of one path that finished in the window.

    Only the database backend keeps task rows; elsewhere tasks run inline
    and a failure raises in the command itself.
    """
    if settings.TASKS["default"]["BACKEND"] != _DB_TASK_BACKEND:
        return 0
    # Imported here: only this branch reads the table (as task_queue does).
    from django_tasks_db.models import DBTaskResult  # noqa: PLC0415 — see above

    return (
        DBTaskResult.objects.failed()
        .filter(task_path=task_path, finished_at__gte=ctx.start)
        .count()
    )


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


def _bulletin_feed(source: str, label: str) -> Feed:
    """Return the feed for one bulletin provider's share of ``fetch_bulletins``.

    New and changed come from ingest's ``SourceBulletin``, whose
    ``updated_at`` moves only when the content does (a host ``Bulletin`` is
    rewritten on every re-store).
    """
    prefix = source.lower()

    def latest(ctx: Context) -> dt.datetime | None:
        """Return the provider's newest issue time."""
        issued: dt.datetime | None = Bulletin.objects.filter(source=source).aggregate(
            m=Max("issued_at")
        )["m"]
        return issued

    def off(ctx: Context) -> bool:
        """Off-season: nothing issued in the last week."""
        issued = latest(ctx)
        return issued is None or ctx.now - issued > OFF_SEASON_AFTER

    def metrics(ctx: Context) -> list[tuple[str, int]]:
        """Return the new and changed bulletins in the window."""
        rows = SourceBulletin.objects.filter(provider=source)
        new = rows.filter(created_at__gte=ctx.start).count()
        changed = rows.filter(
            updated_at__gte=ctx.start, created_at__lt=ctx.start
        ).count()
        return [("new", new), ("changed", changed)]

    def red_reason(ctx: Context) -> str | None:
        """Red when publishing and the last issue is too old."""
        issued = latest(ctx)
        if issued is None or off(ctx):
            return None
        age = ctx.now - issued
        return f"last issue {_hours(age)} ago" if age > BULLETIN_STALE_AFTER else None

    def run_failed(run: CommandRun) -> bool:
        """Return whether the run, or this provider's slots in it, failed."""
        return (
            run.status in _WHOLE_RUN_FAILED
            or _count(run.counts or {}, f"{prefix}_failed") > 0
        )

    return Feed(
        key=prefix,
        group="Bulletins",
        label=label,
        job_id="fetch_bulletins",
        command="fetch_bulletins",
        metrics=metrics,
        changes=lambda counts: _nonzero(
            [
                ("new", _count(counts, f"{prefix}_created")),
                ("changed", _count(counts, f"{prefix}_updated")),
            ]
        ),
        red_reason=red_reason,
        run_failed=run_failed,
        latest=latest,
        off=off,
    )


def _forecast_metrics(ctx: Context) -> list[tuple[str, int]]:
    """Active places whose weather was refreshed in the window, of all active.

    Counted from the rows that landed: ``fetch_weather`` only enqueues.
    """
    active = Location.objects.active()
    refreshed = (
        _forecast_rows(ctx)
        .filter(fetched_at__gte=ctx.start, location__in=active)
        .values("location")
        .distinct()
        .count()
    )
    return [("refreshed", refreshed), ("places", active.count())]


def _forecast_rows(ctx: Context) -> QuerySet[Weather]:
    """Return the forecast's rows: today and the days ahead.

    The backfill writes past days with a fresh ``fetched_at``, so counting
    them would keep the forecast green after its fetches had stopped.
    """
    return Weather.objects.filter(observed_on__gte=timezone.localtime(ctx.now).date())


def _forecast_latest(ctx: Context) -> dt.datetime | None:
    """Return when a forecast row (today or later) was last written."""
    fetched: dt.datetime | None = _forecast_rows(ctx).aggregate(m=Max("fetched_at"))[
        "m"
    ]
    return fetched


#: The longest gap between forecast fetches before the feed is red: the
#: job runs every six hours, and its tasks take a while to land.
FORECAST_STALE_AFTER = dt.timedelta(hours=7)


def _forecast_red(ctx: Context) -> str | None:
    """Red when a place has no row for today, or nothing landed lately."""
    today = timezone.localtime(ctx.now).date()
    active = Location.objects.active()
    missing = active.count() - active.filter(weather__observed_on=today).count()
    if missing > 0:
        return f"{missing} place(s) without today"
    latest = _forecast_latest(ctx)
    if latest is None:
        return "never fetched"
    age = ctx.now - latest
    return f"last fetched {_hours(age)} ago" if age > FORECAST_STALE_AFTER else None


def _enqueue_feed(
    *,
    key: str,
    group: str,
    label: str,
    command: str,
    task_path: str,
    stale_after: dt.timedelta,
) -> Feed:
    """Return a feed whose command only enqueues tasks (backfill, drawings).

    Its figures are what it queued and how many of those tasks failed.
    """

    def metrics(ctx: Context) -> list[tuple[str, int]]:
        """Queued in the window, and failed tasks of this path."""
        queued = _sum_over_runs(ctx, lambda counts: _count(counts, "enqueued"))
        return [("queued", queued), ("failed", _failed_tasks(task_path, ctx))]

    stale = _last_run_older_than(stale_after)

    def red_reason(ctx: Context) -> str | None:
        """Red when the job stopped, or one of its tasks failed."""
        reason = stale(ctx)
        if reason:
            return reason
        failed = _failed_tasks(task_path, ctx)
        return f"{failed} failed task(s)" if failed else None

    return Feed(
        key=key,
        group=group,
        label=label,
        job_id=command,
        command=command,
        metrics=metrics,
        changes=_no_changes,
        red_reason=red_reason,
    )


def _outcome_changes(counts: dict[str, Any]) -> list[tuple[str, int]]:
    """Return a document import's host-applied created, updated and deleted rows."""
    return _nonzero(
        [
            ("new", _outcome_total(counts, "created")),
            ("changed", _outcome_total(counts, "updated")),
            ("removed", _outcome_total(counts, "deleted")),
        ]
    )


def _outcome_metrics(ctx: Context) -> list[tuple[str, int]]:
    """Sum ``_outcome_changes`` over every run in the window, zeros kept."""
    totals = {"new": 0, "changed": 0, "removed": 0}
    for run in ctx.runs:
        for label, n in _outcome_changes(run.counts or {}):
            totals[label] += n
    return list(totals.items())


def _rest_zone_metrics(ctx: Context) -> list[tuple[str, int]]:
    """Return the new and changed rest zones in the window, from ingest's table."""
    new = SourceRestZone.objects.filter(created_at__gte=ctx.start).count()
    changed = SourceRestZone.objects.filter(
        updated_at__gte=ctx.start, created_at__lt=ctx.start
    ).count()
    return [("new", new), ("changed", changed)]


def _build_feeds() -> tuple[Feed, ...]:
    """Return every feed, in the order the report shows them."""
    return (
        _bulletin_feed(Bulletin.Source.SLF, "SLF"),
        _bulletin_feed(Bulletin.Source.ALBINA, "ALBINA"),
        _bulletin_feed(Bulletin.Source.METEOFRANCE, "Météo-France"),
        Feed(
            key="forecast",
            group="Weather",
            label="Forecast",
            job_id="fetch_weather",
            command="fetch_weather",
            metrics=_forecast_metrics,
            changes=_no_changes,
            red_reason=_forecast_red,
            latest=_forecast_latest,
        ),
        _enqueue_feed(
            key="backfill",
            group="Weather",
            label="Backfill",
            command="backfill_weather",
            task_path=backfill_unit_task.module_path,
            stale_after=dt.timedelta(hours=2),
        ),
        Feed(
            key="mirror",
            group="Reference data",
            label="Estate mirror",
            job_id="mirror_reference_data",
            command="mirror_reference_data",
            metrics=_outcome_metrics,
            changes=_outcome_changes,
            red_reason=_last_run_older_than(dt.timedelta(hours=2)),
        ),
        Feed(
            key="ski_tours",
            group="Reference data",
            label="SAC tours",
            job_id="import_ski_tours",
            command="import_ski_tours",
            # From the run's counts, not SourceSkiTour.updated_at: the
            # daily drawing job rewrites that row too.
            metrics=_outcome_metrics,
            changes=_outcome_changes,
            red_reason=_last_run_older_than(dt.timedelta(days=8)),
            weekly=True,
        ),
        _enqueue_feed(
            key="tour_drawings",
            group="Reference data",
            label="Tour drawings",
            command="render_ski_tours",
            task_path=render_tour_task.module_path,
            stale_after=dt.timedelta(hours=26),
        ),
        Feed(
            key="rest_zones",
            group="Reference data",
            label="Rest zones",
            job_id="import_rest_zones",
            command="import_rest_zones",
            metrics=_rest_zone_metrics,
            changes=_outcome_changes,
            red_reason=_last_run_older_than(dt.timedelta(days=8)),
            weekly=True,
        ),
    )


FEEDS: tuple[Feed, ...] = _build_feeds()


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@functools.cache
def _triggers() -> dict[str, Any]:
    """Return each scheduler job's trigger, by job id.

    Builds the scheduler once per process and never starts it.
    """
    from schedule import (  # noqa: PLC0415 — the root module builds APScheduler jobs; load it only when a report asks
        build_scheduler,
    )

    return {job.id: job.trigger for job in build_scheduler().get_jobs()}


def next_run(job_id: str, now: dt.datetime) -> dt.datetime | None:
    """Return when the scheduler next fires ``job_id`` after ``now``."""
    trigger = _triggers().get(job_id)
    return trigger.get_next_fire_time(None, now) if trigger else None


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def parse_window(value: str | None) -> str:
    """Return ``value`` when it names a window, else the default.

    Raises:
        ValueError: ``value`` is given and names no window.

    """
    if not value:
        return DEFAULT_WINDOW
    if value not in WINDOWS:
        raise ValueError(f"window must be one of {', '.join(WINDOWS)}")
    return value


def _admin_url(run: CommandRun) -> str:
    """Return the run's admin change page, by its integer primary key."""
    return reverse("admin:core_commandrun_change", args=[run.pk])


def _strip(
    feed: Feed, ctx: Context, length: dt.timedelta, cells: int
) -> list[dict[str, Any]]:
    """Bucket the window's runs into ``cells`` cells, worst status winning.

    A cell is ``none`` (nothing ran), ``ok``, ``running`` or ``failed``, and
    names the run it shows: the failed one if any, else the latest.
    """
    width = length / cells
    strip: list[dict[str, Any]] = [{"state": "none", "url": ""} for _ in range(cells)]
    rank = {"none": 0, "ok": 1, "running": 2, "failed": 3}
    for run in ctx.runs:
        index = min(int((run.started_at - ctx.start) / width), cells - 1)
        if run.status == CommandRun.STATUS.RUNNING:
            state = "running"
        elif feed.run_failed(run):
            state = "failed"
        else:
            state = "ok"
        if rank[state] >= rank[strip[index]["state"]]:
            strip[index] = {
                "state": state,
                "url": _admin_url(run),
                "started_at": run.started_at,
            }
    return strip


def _status(feed: Feed, ctx: Context) -> tuple[str, str | None]:
    """Return the feed's state and, when not ok, why."""
    if feed.off(ctx):
        return OFF, "off-season"
    reason = feed.red_reason(ctx)
    if reason:
        return RED, reason
    if ctx.last_run is not None and feed.run_failed(ctx.last_run):
        return RED, "last run failed"
    failures = sum(1 for run in ctx.runs if feed.run_failed(run))
    if failures:
        return WARN, f"{failures} failed run(s)"
    return OK, None


def _row(feed: Feed, ctx: Context, length: dt.timedelta, cells: int) -> dict[str, Any]:
    """Return one feed's row of the report."""
    status, reason = _status(feed, ctx)
    last = ctx.last_run
    return {
        "key": feed.key,
        "group": feed.group,
        "label": feed.label,
        "command": feed.command,
        "status": status,
        "reason": reason,
        "metrics": [{"label": label, "value": n} for label, n in feed.metrics(ctx)],
        "last_run_at": last.started_at if last else None,
        "last_run_url": _admin_url(last) if last else "",
        "latest_at": feed.latest(ctx),
        "next_run_at": next_run(feed.job_id, ctx.now),
        "weekly": feed.weekly,
        "strip": _strip(feed, ctx, length, cells),
    }


def _changes(feed: Feed, ctx: Context) -> list[dict[str, Any]]:
    """Return the window's runs of ``feed`` that changed something or failed.

    A run that failed outright is listed under its group, so a failed
    ``fetch_bulletins`` is one entry, not one per provider; ``build_report``
    drops the repeats.
    """
    entries = []
    for run in ctx.runs:
        failed = feed.run_failed(run)
        parts = feed.changes(run.counts or {})
        if not failed and not parts:
            continue
        whole_run = run.status in _WHOLE_RUN_FAILED
        entries.append(
            {
                "feed": feed.group
                if whole_run and feed.group == "Bulletins"
                else feed.label,
                "group": feed.group,
                "started_at": run.started_at,
                "failed": failed,
                "parts": [f"{n} {label}" for label, n in parts],
                "url": _admin_url(run),
            }
        )
    return entries


def _dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop entries repeating one already listed (same run, same text)."""
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    kept = []
    for entry in entries:
        key = (entry["url"], entry["feed"], tuple(entry["parts"]))
        if key not in seen:
            seen.add(key)
            kept.append(entry)
    return kept


def _summary(rows: list[dict[str, Any]]) -> str:
    """Return the one line Claude reads first."""
    red = [row["label"] for row in rows if row["status"] == RED]
    warn = [row["label"] for row in rows if row["status"] == WARN]
    if not red and not warn:
        return "All feeds green."
    parts = []
    if red:
        parts.append(f"{len(red)} red: {', '.join(red)}")
    if warn:
        parts.append(f"{len(warn)} with a failed run: {', '.join(warn)}")
    return "; ".join(parts) + "."


def build_report(window: str = DEFAULT_WINDOW, *, now: dt.datetime) -> dict[str, Any]:
    """Return what every scheduled feed brought in over ``window``.

    Args:
        window: A key of ``WINDOWS``.
        now: The report's reference time.

    Returns:
        ``window``, ``now``, ``start``, ``groups`` (each a name and its feed
        rows), ``changes`` (newest first, at most ``CHANGES_SHOWN``),
        ``task_queue``, ``red`` (the red feeds' keys) and ``summary``.

    """
    length, cells = WINDOWS[parse_window(window)]
    start = now - length
    commands = {feed.command for feed in FEEDS}
    in_window: dict[str, list[CommandRun]] = {command: [] for command in commands}
    for run in (
        CommandRun.objects.filter(
            command__in=commands,
            invoked_via=CommandRun.INVOKED_VIA.SCHEDULER,
            started_at__gte=start,
            started_at__lte=now,
        )
        .only("pk", "command", "status", "started_at", "counts")
        .order_by("started_at")
    ):
        in_window[run.command].append(run)
    last_runs = {
        command: CommandRun.objects.for_command(command)
        .filter(invoked_via=CommandRun.INVOKED_VIA.SCHEDULER, started_at__lte=now)
        .only("pk", "command", "status", "started_at", "counts")
        .order_by("-started_at")
        .first()
        for command in commands
    }

    rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for feed in FEEDS:
        ctx = Context(
            now=now,
            start=start,
            runs=in_window[feed.command],
            last_run=last_runs[feed.command],
        )
        rows.append(_row(feed, ctx, length, cells))
        changes.extend(_changes(feed, ctx))

    groups: list[dict[str, Any]] = []
    for row in rows:
        if not groups or groups[-1]["name"] != row["group"]:
            groups.append({"name": row["group"], "feeds": []})
        groups[-1]["feeds"].append(row)

    changes = _dedupe(changes)
    changes.sort(key=lambda entry: entry["started_at"], reverse=True)
    return {
        "window": window,
        "now": now,
        "start": start,
        "groups": groups,
        "changes": changes[:CHANGES_SHOWN],
        "task_queue": task_queue_health(now=now),
        "red": [row["key"] for row in rows if row["status"] == RED],
        "summary": _summary(rows),
    }


def red_feeds(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the report's red feed rows: what ``check_ingest`` alerts on."""
    return [
        row
        for group in report["groups"]
        for row in group["feeds"]
        if row["status"] == RED
    ]


def as_json(report: dict[str, Any]) -> dict[str, Any]:
    """Return the report with every datetime as an ISO string, for MCP."""
    safe: dict[str, Any] = json.loads(json.dumps(report, cls=DjangoJSONEncoder))
    return safe
