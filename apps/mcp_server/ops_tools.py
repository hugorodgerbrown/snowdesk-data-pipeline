"""
apps/mcp_server/ops_tools.py — the staff ops MCP tool registry (SNOW-1191).

The tools Claude calls at ``POST /api/mcp/ops/`` to answer an operator's
questions about one environment: what ran, on which build, with what
result, and whether the estate looks healthy. Every tool is read-only
except the two waffle setters, ``set_switch`` and ``set_flag``.
The endpoint is staff-only; the public avalanche tools live in
``apps.mcp_server.tools`` and share only the envelope, ``ToolSpec`` and
``ToolError``.

Two families:

* **The command run register** (``CommandRun``, SNOW-1189) —
  ``list_commands``, ``list_command_runs``, ``get_command_run`` and
  ``command_history``.
* **Deploy steps** (SNOW-1192) — ``deploy_steps_status`` checks the
  ``DEPLOY_STEPS`` file ``bin/cut-release`` ships with each release against
  the register.
* **Health** — ``build_info``, ``ingest_status``, ``bulletin_coverage``,
  ``task_queue_health``, ``weather_freshness``, ``location_estate_health``
  and ``feature_flags``.
* **Waffle writes** — ``set_switch`` and ``set_flag`` turn an existing
  switch or flag on or off. Each needs the name, the state and a reason,
  changes nothing when the row is already in that state, never creates a
  row, and records every change as an admin ``LogEntry`` against the staff
  account that made it (so it shows in the row's admin History) and as a
  WARNING log line.

Each tool is a plain function returning a JSON-safe dict with a one-line
``summary`` (the text Claude reads first), wrapped by a ``_handle_*``
adapter that validates the raw ``arguments``.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import platform
import re
import shlex
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import django
from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management import get_commands
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.bulletins.models import Bulletin, PipelineRun, RegionDayRating
from apps.core.command_register import _environment
from apps.core.models import CommandRun
from apps.locations.models import Location
from apps.mcp_server.resolvers import _PROVIDER_BY_COUNTRY
from apps.mcp_server.tools import (
    ToolError,
    ToolSpec,
    _optional_iso_date,
    _require_str,
)
from apps.regions.models import MicroRegion
from apps.weather.models import Weather

logger = logging.getLogger(__name__)

# Default and ceiling for every list a tool returns. A register lookup is a
# question about a handful of runs; 100 rows is already more than a reply
# can usefully carry.
DEFAULT_LIMIT: int = 20
MAX_LIMIT: int = 100

# How many region ids bulletin_coverage names per provider before it stops
# listing and only counts.
MISSING_REGIONS_SHOWN: int = 50

# The window the health tools call "recent".
RECENT: dt.timedelta = dt.timedelta(hours=24)

# How many recent waffle changes feature_flags returns from the admin log.
RECENT_WAFFLE_CHANGES: int = 10

# The longest reason a waffle setter accepts; LogEntry.change_message is a
# TextField, so this is about a readable History page, not storage.
MAX_REASON_LENGTH: int = 500

# A flag's ``everyone`` per state: on and off override every other rule,
# targeted hands the decision back to its percent, groups and users.
FLAG_STATES: dict[str, bool | None] = {"on": True, "off": False, "targeted": None}
SWITCH_STATES: dict[str, bool] = {"on": True, "off": False}


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    """Return ``arguments[key]`` as a stripped string, or None when absent.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.
        key: The optional argument name.

    Returns:
        The value, or None when absent or blank.

    Raises:
        ToolError: The value is present but not a string.

    """
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string when supplied.")
    return value.strip() or None


def _limit(arguments: dict[str, Any]) -> int:
    """Return the ``limit`` argument, defaulted and clamped to ``MAX_LIMIT``.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.

    Returns:
        A limit between 1 and ``MAX_LIMIT``.

    Raises:
        ToolError: ``limit`` is present but not a positive integer.

    """
    value = arguments.get("limit", DEFAULT_LIMIT)
    # bool is an int subclass; True must not read as a limit of one.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError("'limit' must be a positive integer.")
    return min(value, MAX_LIMIT)


def _choice(arguments: dict[str, Any], key: str, choices: Any) -> str | None:
    """Return an optional TextChoices argument, upper-cased and validated.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.
        key: The optional argument name.
        choices: The ``TextChoices`` class the value must belong to.

    Returns:
        The canonical value, or None when absent.

    Raises:
        ToolError: The value is not one of the choices.

    """
    value = _optional_str(arguments, key)
    if value is None:
        return None
    value = value.upper()
    if value not in choices.values:
        raise ToolError(f"'{key}' must be one of {', '.join(choices.values)}.")
    return value


def _iso(value: dt.datetime | dt.date | None) -> str | None:
    """Return an ISO-8601 string for a date or datetime, or None.

    Args:
        value: The value to format.

    Returns:
        The ISO string, or None for None.

    """
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# The command run register
# ---------------------------------------------------------------------------


def _run_row(run: CommandRun) -> dict[str, Any]:
    """Return the slim shape of one run used by every list.

    Leaves out the output tail and the traceback; ``get_command_run``
    returns those.

    Args:
        run: The run to serialise.

    Returns:
        A JSON-safe dict.

    """
    duration = run.duration
    return {
        "id": str(run.uuid),
        "command": run.command,
        "mode": run.mode,
        "status": run.status,
        "exit_code": run.exit_code,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "duration_seconds": round(duration.total_seconds(), 1) if duration else None,
        "counts": run.counts,
        "arguments": run.arguments,
        "invoked_via": run.invoked_via,
        "operator": run.operator,
        "git_sha": run.git_sha,
        "release": run.release,
    }


def list_commands() -> dict[str, Any]:
    """Return every project command with its run count and last run.

    A command with no row is listed under ``never_run``: on a fresh
    environment that is the answer to "has the backfill been run here?".

    Returns:
        ``commands`` (one entry per command that has run, newest first),
        ``never_run`` (project commands with no row) and ``summary``.

    """
    stats = {
        row["command"]: row
        for row in CommandRun.objects.values("command").annotate(
            runs=Count("id"),
            commit_runs=Count("id", filter=Q(mode=CommandRun.MODE.COMMIT)),
            last_started_at=Max("started_at"),
        )
    }
    latest: dict[str, CommandRun] = {}
    # One pass over the newest row per command: order newest first and keep
    # the first seen. The register is small (scheduled rows are pruned at 90
    # days), and only the slim columns are loaded.
    for run in CommandRun.objects.only(
        "uuid", "command", "mode", "status", "started_at", "git_sha", "release"
    ).order_by("-started_at"):
        latest.setdefault(run.command, run)

    commands = [
        {
            "command": name,
            "runs": stats[name]["runs"],
            "commit_runs": stats[name]["commit_runs"],
            "last_run": {
                "id": str(latest[name].uuid),
                "mode": latest[name].mode,
                "status": latest[name].status,
                "started_at": _iso(latest[name].started_at),
                "git_sha": latest[name].git_sha,
                "release": latest[name].release,
            },
        }
        for name in sorted(latest, key=lambda n: latest[n].started_at, reverse=True)
    ]
    project = {name for name, app in get_commands().items() if app.startswith("apps.")}
    never_run = sorted(project - set(latest))
    return {
        "commands": commands,
        "never_run": never_run,
        "summary": (
            f"{len(commands)} command(s) have run here; "
            f"{len(never_run)} project command(s) never have."
        ),
    }


def list_command_runs(
    *,
    command: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    invoked_via: str | None = None,
    release: str | None = None,
    git_sha: str | None = None,
    since: dt.date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return runs matching every filter given, newest first.

    Args:
        command: Exact command name, e.g. ``relink_bulletin_regions``.
        status: A ``CommandRun.STATUS`` value.
        mode: A ``CommandRun.MODE`` value.
        invoked_via: A ``CommandRun.INVOKED_VIA`` value.
        release: The release number the run was stamped with, e.g. ``45``.
        git_sha: A git SHA or a prefix of one (at least 7 characters).
        since: Only runs started on or after this day (UTC).
        limit: The most rows to return.

    Returns:
        ``runs``, ``total`` (matches before the limit) and ``summary``.

    """
    runs = CommandRun.objects.all()
    if command:
        runs = runs.for_command(command)
    if status:
        runs = runs.filter(status=status)
    if mode:
        runs = runs.filter(mode=mode)
    if invoked_via:
        runs = runs.filter(invoked_via=invoked_via)
    if release:
        runs = runs.filter(release=release)
    if git_sha:
        runs = runs.filter(git_sha__startswith=git_sha)
    if since:
        start = dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC)
        runs = runs.filter(started_at__gte=start)
    total = runs.count()
    rows = [_run_row(run) for run in runs.order_by("-started_at")[:limit]]
    return {
        "runs": rows,
        "total": total,
        "summary": f"{total} matching run(s); showing {len(rows)}.",
    }


def _handle_list_command_runs(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``list_command_runs`` arguments and call it."""
    git_sha = _optional_str(arguments, "git_sha")
    if git_sha is not None and len(git_sha) < 7:
        raise ToolError("'git_sha' must be at least 7 characters.")
    return list_command_runs(
        command=_optional_str(arguments, "command"),
        status=_choice(arguments, "status", CommandRun.STATUS),
        mode=_choice(arguments, "mode", CommandRun.MODE),
        invoked_via=_choice(arguments, "invoked_via", CommandRun.INVOKED_VIA),
        release=_optional_str(arguments, "release"),
        git_sha=git_sha,
        since=_optional_iso_date(arguments, "since"),
        limit=_limit(arguments),
    )


def get_command_run(run_id: str) -> dict[str, Any]:
    """Return one run in full: output tail, traceback, host and children.

    Args:
        run_id: The run's ``id`` as the list tools return it (a UUID).

    Returns:
        The run's full record plus ``children`` (runs it started through
        ``call_command``) and ``summary``.

    Raises:
        ToolError: ``run_id`` is not a UUID or names no run.

    """
    try:
        key = uuid.UUID(run_id)
    except ValueError as exc:
        raise ToolError(f"'run_id' is not a run id: {run_id!r}.") from exc
    run = CommandRun.objects.select_related("parent").filter(uuid=key).first()
    if run is None:
        raise ToolError(f"No run with id {run_id!r}.")
    record = _run_row(run)
    record.update(
        {
            "app_label": run.app_label,
            "argv": run.argv,
            "output_tail": run.output_tail,
            "error": run.error,
            "host": run.host,
            "service": run.service,
            "instance": run.instance,
            "environment": run.environment,
            "parent_id": str(run.parent.uuid) if run.parent else None,
            "children": [
                _run_row(child)
                for child in CommandRun.objects.filter(parent=run).order_by(
                    "started_at"
                )
            ],
        }
    )
    record["summary"] = (
        f"{run.command} [{run.mode}] {run.status} on release "
        f"{run.release or '?'} ({run.git_sha[:7] or '?'})."
    )
    return record


def _handle_get_command_run(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``get_command_run`` arguments and call it."""
    run_id = _optional_str(arguments, "run_id")
    if run_id is None:
        raise ToolError("'run_id' is required.")
    return get_command_run(run_id)


def command_history(command: str) -> dict[str, Any]:
    """Return one command's history grouped by the build it ran on.

    Answers "was this committed on the current deploy?": for each git SHA,
    newest first, the latest dry run and the latest commit run, so their
    counts can be compared side by side.

    Args:
        command: Exact command name.

    Returns:
        ``builds`` (one entry per SHA), ``current_git_sha``,
        ``committed_on_current_build`` and ``summary``.

    Raises:
        ToolError: The command has never run here.

    """
    runs = list(CommandRun.objects.for_command(command).order_by("-started_at"))
    if not runs:
        raise ToolError(
            f"{command!r} has never run here. list_commands lists those that have."
        )
    builds: dict[str, dict[str, Any]] = {}
    for run in runs:
        build = builds.setdefault(
            run.git_sha,
            {
                "git_sha": run.git_sha,
                "release": run.release,
                "runs": 0,
                "last_dry_run": None,
                "last_commit_run": None,
            },
        )
        build["runs"] += 1
        slot = {
            CommandRun.MODE.COMMIT: "last_commit_run",
            CommandRun.MODE.DRY_RUN: "last_dry_run",
        }.get(CommandRun.MODE(run.mode))
        if slot and build[slot] is None:
            build[slot] = _run_row(run)

    current = settings.RELEASE_VERSION
    on_current = builds.get(current, {}).get("last_commit_run")
    committed = bool(on_current and on_current["status"] == CommandRun.STATUS.SUCCEEDED)
    return {
        "command": command,
        "current_git_sha": current,
        "committed_on_current_build": committed,
        "builds": list(builds.values()),
        "summary": (
            f"{command}: {len(runs)} run(s) across {len(builds)} build(s); "
            + (
                "committed successfully on the current build."
                if committed
                else "no successful commit run on the current build."
            )
        ),
    }


def _handle_command_history(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``command_history`` arguments and call it."""
    command = _optional_str(arguments, "command")
    if command is None:
        raise ToolError("'command' is required.")
    return command_history(command)


# ---------------------------------------------------------------------------
# Deploy steps
# ---------------------------------------------------------------------------

# The release ordinal in the header bin/cut-release writes.
_STEPS_RELEASE = re.compile(r"release v(\d+)")


def _read_deploy_steps(path: Path) -> tuple[str | None, list[str]] | None:
    """Read a ``DEPLOY_STEPS`` file into its release and its steps.

    Args:
        path: The file to read.

    Returns:
        ``(release, steps)`` — the release named in the header (or None)
        and the non-comment lines — or None when the file is absent.

    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    match = _STEPS_RELEASE.search(text)
    steps = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return (match.group(1) if match else None), steps


def _step_status(step: str, release: str, known: set[str]) -> dict[str, Any]:
    """Return one step's state on this release from the register.

    A step that passes ``--commit`` is done only by a successful commit
    run; any other step by any successful run. Only runs stamped with the
    release now deployed count — a run on an earlier build is the step
    rehearsed, not the step done.

    Args:
        step: The step as written, e.g. ``backfill_resort_slugs --commit``.
        release: The release now deployed.
        known: The project's command names.

    Returns:
        ``step``, ``command``, ``state`` (``DONE``, ``FAILED``,
        ``DRY_RUN_ONLY``, ``PENDING`` or ``UNKNOWN_COMMAND``) and the
        deciding run, if any.

    """
    try:
        tokens = shlex.split(step)
    except ValueError:
        tokens = step.split()
    command = tokens[0] if tokens else ""
    entry: dict[str, Any] = {"step": step, "command": command, "run": None}
    if command not in known:
        entry["state"] = "UNKNOWN_COMMAND"
        return entry
    runs = CommandRun.objects.for_command(command).filter(release=release)
    if "--commit" in tokens:
        runs = runs.filter(mode=CommandRun.MODE.COMMIT)
    runs = runs.order_by("-started_at")
    done = runs.filter(status=CommandRun.STATUS.SUCCEEDED).first()
    latest = done or runs.first()
    if done:
        entry["state"] = "DONE"
    elif latest:
        entry["state"] = "FAILED"
    elif (
        CommandRun.objects.for_command(command)
        .filter(release=release, mode=CommandRun.MODE.DRY_RUN)
        .exists()
    ):
        entry["state"] = "DRY_RUN_ONLY"
    else:
        entry["state"] = "PENDING"
    entry["run"] = _run_row(latest) if latest else None
    return entry


def deploy_steps_status(path: Path | None = None) -> dict[str, Any]:
    """Return which of this release's deploy steps have run here.

    Reads ``DEPLOY_STEPS`` — written into the release commit by
    ``bin/cut-release`` from the release's ``Deploy-Step:`` trailers — and
    checks each step against the register for the release now deployed.

    Args:
        path: The steps file; defaults to ``DEPLOY_STEPS`` at the repo root.
            A test seam.

    Returns:
        ``release`` (deployed), ``file_release`` (named in the file),
        ``steps`` (one entry per step, see ``_step_status``), ``outstanding``
        and ``summary``.

    """
    release = settings.APP_RELEASE
    parsed = _read_deploy_steps(path or Path(settings.BASE_DIR) / "DEPLOY_STEPS")
    if parsed is None:
        return {
            "release": release,
            "file_release": None,
            "steps": [],
            "outstanding": 0,
            "summary": (
                "This build has no DEPLOY_STEPS file; it predates SNOW-1192 or "
                "was not cut by bin/cut-release."
            ),
        }
    file_release, steps = parsed
    known = {name for name, app in get_commands().items() if app.startswith("apps.")}
    entries = [_step_status(step, release, known) for step in steps]
    outstanding = sum(1 for entry in entries if entry["state"] != "DONE")
    summary = (
        f"Release {release}: {len(entries) - outstanding}/{len(entries)} deploy "
        "step(s) done."
        if entries
        else f"Release {release} ships no deploy steps."
    )
    if file_release and file_release != release:
        # Staging runs main, whose VERSION lags the release being prepared.
        summary += f" The file names release {file_release}, not {release}."
    return {
        "release": release,
        "file_release": file_release,
        "steps": entries,
        "outstanding": outstanding,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def _migration_state() -> dict[str, Any]:
    """Return the unapplied migrations on the default database.

    Returns:
        ``unapplied`` (``app.name`` strings) and ``unapplied_count``.

    """
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    unapplied = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
    return {"unapplied": unapplied, "unapplied_count": len(unapplied)}


def build_info() -> dict[str, Any]:
    """Return the build this process serves and when it went live.

    ``released_at`` is the time the build ran (``BUILT_AT``, written by
    ``bin/build.sh``), falling back to boot time where no build wrote it;
    ``booted_at`` is when this process started. A recent boot on an old
    release is a restart, not a deploy.

    Returns:
        Identity, timing, runtime and migration state, plus ``summary``.

    """
    migrations = _migration_state()
    release = settings.APP_RELEASE
    return {
        "git_sha": settings.RELEASE_VERSION,
        "release": release,
        "released_at": settings.APP_RELEASED_AT,
        "booted_at": settings.APP_BOOTED_AT,
        "site_environment": settings.SITE_ENVIRONMENT,
        "settings_overlay": _environment(),
        "render_service": os.environ.get("RENDER_SERVICE_NAME", ""),
        "render_instance": os.environ.get("RENDER_INSTANCE_ID", ""),
        "python": platform.python_version(),
        "django": django.get_version(),
        "debug": settings.DEBUG,
        **migrations,
        "summary": (
            f"Release {release or '?'} ({settings.RELEASE_VERSION[:7]}) released "
            f"{settings.APP_RELEASED_AT}; {migrations['unapplied_count']} "
            "unapplied migration(s)."
        ),
    }


def ingest_status(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """Return how fresh each provider's bulletins are, and the fetch's health.

    Args:
        now: The reference time; defaults to now. A test seam.

    Returns:
        ``providers`` (per source: bulletin count, latest issue and target
        date, last stored), ``last_fetch`` (the newest ``fetch_bulletins``
        run), ``pipeline_runs_24h`` (by status) and ``summary``.

    """
    now = now or timezone.now()
    providers = []
    for source in Bulletin.Source:
        latest = Bulletin.objects.filter(source=source).aggregate(
            bulletins=Count("id"),
            latest_issued_at=Max("issued_at"),
            latest_target_date=Max("target_date"),
            last_stored_at=Max("updated_at"),
        )
        issued = latest["latest_issued_at"]
        providers.append(
            {
                "source": source.value,
                "bulletins": latest["bulletins"],
                "latest_issued_at": _iso(issued),
                "hours_since_issue": (
                    round((now - issued).total_seconds() / 3600, 1) if issued else None
                ),
                "latest_target_date": _iso(latest["latest_target_date"]),
                "last_stored_at": _iso(latest["last_stored_at"]),
            }
        )
    last_fetch = (
        CommandRun.objects.for_command("fetch_bulletins")
        .order_by("-started_at")
        .first()
    )
    recent = Counter(
        PipelineRun.objects.filter(started_at__gte=now - RECENT).values_list(
            "status", flat=True
        )
    )
    stalest = max(
        (p for p in providers if p["hours_since_issue"] is not None),
        key=lambda p: p["hours_since_issue"],
        default=None,
    )
    return {
        "providers": providers,
        "last_fetch": _run_row(last_fetch) if last_fetch else None,
        "pipeline_runs_24h": dict(recent),
        "summary": (
            "No bulletins stored."
            if stalest is None
            else (
                f"Stalest provider {stalest['source']}: last issue "
                f"{stalest['hours_since_issue']}h ago; "
                f"{recent.get(PipelineRun.Status.FAILED, 0)} failed and "
                f"{recent.get(PipelineRun.Status.PARTIAL, 0)} partial pipeline "
                "run(s) in 24h."
            )
        ),
    }


def bulletin_coverage(day: dt.date) -> dict[str, Any]:
    """Return, per provider, which of its regions have a rating for a day.

    A region counts as rated when its ``RegionDayRating`` for the day
    carries a rating other than ``no_rating``.

    Args:
        day: The day to check.

    Returns:
        ``providers`` (per source: expected, rated, and up to
        ``MISSING_REGIONS_SHOWN`` missing region ids), ``date`` and
        ``summary``.

    """
    rated = set(
        RegionDayRating.objects.filter(date=day)
        .exclude(max_rating=RegionDayRating.Rating.NO_RATING)
        .values_list("region__region_id", flat=True)
    )
    providers = []
    for source in Bulletin.Source:
        countries = [c for c, p in _PROVIDER_BY_COUNTRY.items() if p == source.value]
        expected = set(
            MicroRegion.objects.filter(
                subregion__major__country__in=countries
            ).values_list("region_id", flat=True)
        )
        missing = sorted(expected - rated)
        providers.append(
            {
                "source": source.value,
                "expected": len(expected),
                "rated": len(expected & rated),
                "missing_count": len(missing),
                "missing": missing[:MISSING_REGIONS_SHOWN],
            }
        )
    gaps = [p for p in providers if p["missing_count"]]
    return {
        "date": day.isoformat(),
        "providers": providers,
        "summary": (
            f"{day}: every provider's regions are rated."
            if not gaps
            else f"{day}: "
            + "; ".join(
                f"{p['source']} {p['rated']}/{p['expected']} rated" for p in gaps
            )
            + "."
        ),
    }


def _handle_bulletin_coverage(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``bulletin_coverage`` arguments and call it."""
    return bulletin_coverage(
        _optional_iso_date(arguments, "date") or timezone.localdate()
    )


def task_queue_health(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """Return the background task queue's backlog and recent failures.

    Only the database backend (production) keeps rows. Elsewhere tasks run
    inline in the request, so there is no queue to report.

    Args:
        now: The reference time; defaults to now. A test seam.

    Returns:
        ``backend``, and for the database backend ``by_status``,
        ``oldest_ready_age_seconds`` and ``recent_failures``; plus
        ``summary``.

    """
    backend = settings.TASKS["default"]["BACKEND"]
    if backend != "django_tasks_db.DatabaseBackend":
        return {
            "backend": backend,
            "summary": f"Tasks run inline ({backend.rsplit('.', 1)[-1]}); no queue.",
        }
    # Imported here: the app is installed everywhere, but only this branch
    # reads its table.
    from django_tasks_db.models import DBTaskResult

    now = now or timezone.now()
    by_status = dict(DBTaskResult.objects.values_list("status").annotate(n=Count("id")))
    oldest = (
        DBTaskResult.objects.ready()
        .order_by("enqueued_at")
        .values_list("enqueued_at", flat=True)
    ).first()
    failures = (
        DBTaskResult.objects.failed()
        .filter(finished_at__gte=now - RECENT)
        .values("task_path", "exception_class_path")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    oldest_age = round((now - oldest).total_seconds()) if oldest else None
    failed_24h = sum(row["n"] for row in failures)
    return {
        "backend": backend,
        "by_status": by_status,
        "oldest_ready_age_seconds": oldest_age,
        "recent_failures": list(failures),
        "summary": (
            f"{by_status.get('READY', 0)} ready, oldest "
            f"{oldest_age if oldest_age is not None else 0}s; "
            f"{failed_24h} failure(s) in 24h."
        ),
    }


def weather_freshness(*, today: dt.date | None = None) -> dict[str, Any]:
    """Return whether every active location has today's weather row.

    Args:
        today: The day to check; defaults to today. A test seam.

    Returns:
        ``active_locations``, ``with_today``, ``missing_today``,
        ``last_fetched_at``, ``last_fetch`` (the newest ``fetch_weather``
        run) and ``summary``.

    """
    today = today or timezone.localdate()
    active = Location.objects.active()
    total = active.count()
    with_today = active.filter(weather__observed_on=today).count()
    last_fetched = Weather.objects.aggregate(last=Max("fetched_at"))["last"]
    last_fetch = (
        CommandRun.objects.for_command("fetch_weather").order_by("-started_at").first()
    )
    return {
        "date": today.isoformat(),
        "active_locations": total,
        "with_today": with_today,
        "missing_today": total - with_today,
        "last_fetched_at": _iso(last_fetched),
        "last_fetch": _run_row(last_fetch) if last_fetch else None,
        "summary": (
            f"{with_today}/{total} active location(s) have weather for {today}; "
            f"last fetched {_iso(last_fetched) or 'never'}."
        ),
    }


def location_estate_health() -> dict[str, Any]:
    """Return the gaps in the Location estate the fill commands close.

    Returns:
        ``total``, ``by_kind``, ``named``, ``active``, and the work left for
        ``fill_location_elevations`` (``missing_elevation``),
        ``fill_what3words`` (``missing_what3words``) and
        ``prune_orphan_locations`` (``orphaned``), plus ``summary``.

    """
    locations = Location.objects.all()
    by_kind = dict(locations.order_by().values_list("kind").annotate(n=Count("id")))
    report = {
        "total": locations.count(),
        "by_kind": by_kind,
        "named": Location.objects.named().count(),
        "active": Location.objects.active().count(),
        "missing_elevation": Location.objects.unresolved().count(),
        "missing_what3words": Location.objects.unaddressed().count(),
        "orphaned": Location.objects.orphaned().count(),
    }
    report["summary"] = (
        f"{report['total']} location(s): {report['missing_elevation']} without "
        f"elevation, {report['missing_what3words']} without a what3words "
        f"address, {report['orphaned']} orphaned."
    )
    return report


def feature_flags() -> dict[str, Any]:
    """Return every waffle flag, switch and sample, and drift from the manifest.

    ``sync_waffle_flags`` creates and deletes rows to match
    ``apps/core/fixtures/waffle_flags.json`` but never edits one, so a
    flag's live targeting is whatever the admin (or ``set_flag``) last set.

    Returns:
        ``flags``, ``switches``, ``samples``, ``not_in_manifest``,
        ``missing_from_db``, ``recent_changes`` and ``summary``.

    """
    # Imported here so this module loads without waffle's models in a
    # context that never calls the tool.
    from waffle.models import Flag, Sample, Switch

    from apps.core.management.commands.sync_waffle_flags import (
        DEFAULT_MANIFEST_PATH,
    )
    from apps.core.services.waffle_sync import load_manifest

    flags = [
        {
            "name": flag.name,
            "everyone": flag.everyone,
            "percent": float(flag.percent) if flag.percent is not None else None,
            "superusers": flag.superusers,
            "staff": flag.staff,
            "authenticated": flag.authenticated,
            "groups": sorted(group.name for group in flag.groups.all()),
            "users": flag.users.count(),
            "note": flag.note,
        }
        # A query per flag for its groups: a handful of flags, and
        # django-stubs cannot type a prefetch through waffle's abstract base.
        for flag in Flag.objects.order_by("name")
    ]
    switches = [
        {"name": switch.name, "active": switch.active, "note": switch.note}
        for switch in Switch.objects.order_by("name")
    ]
    samples = [
        {"name": sample.name, "percent": float(sample.percent), "note": sample.note}
        for sample in Sample.objects.order_by("name")
    ]
    manifest = {spec.name for spec in load_manifest(DEFAULT_MANIFEST_PATH)}
    live = {flag["name"] for flag in flags}
    on_for_everyone = sum(1 for flag in flags if flag["everyone"])
    switches_on = sum(1 for switch in switches if switch["active"])
    return {
        "flags": flags,
        "switches": switches,
        "samples": samples,
        "not_in_manifest": sorted(live - manifest),
        "missing_from_db": sorted(manifest - live),
        "recent_changes": _recent_waffle_changes(),
        "summary": (
            f"{len(flags)} flag(s), {on_for_everyone} on for everyone; "
            f"{switches_on}/{len(switches)} switch(es) on; "
            f"{len(samples)} sample(s); "
            f"{len(live ^ manifest)} flag(s) differ from the manifest."
        ),
    }


def _recent_waffle_changes() -> list[dict[str, Any]]:
    """Return the latest admin-log entries on any waffle flag, switch or sample.

    The same rows the admin's History page shows, whether the change came
    from the admin or from ``set_switch`` / ``set_flag``.

    Returns:
        Up to ``RECENT_WAFFLE_CHANGES`` entries, newest first.

    """
    from waffle.models import Flag, Sample, Switch

    content_types = ContentType.objects.get_for_models(Flag, Switch, Sample)
    kinds = {ct.pk: model.__name__.lower() for model, ct in content_types.items()}
    entries = (
        LogEntry.objects.filter(content_type_id__in=kinds)
        .select_related("user")
        .order_by("-action_time")[:RECENT_WAFFLE_CHANGES]
    )
    return [
        {
            "at": _iso(entry.action_time),
            # content_type is nullable on LogEntry; the filter rules out None.
            "kind": kinds.get(entry.content_type_id or 0, ""),
            "name": entry.object_repr,
            "by": entry.user.get_username(),
            "change": entry.get_change_message(),
        }
        for entry in entries
    ]


def _reason(arguments: dict[str, Any]) -> str:
    """Return the required ``reason`` argument, bounded in length.

    Args:
        arguments: The raw ``arguments`` dict.

    Returns:
        The stripped reason.

    Raises:
        ToolError: The reason is missing, blank or too long.

    """
    reason = _require_str(arguments, "reason").strip()
    if len(reason) > MAX_REASON_LENGTH:
        raise ToolError(f"'reason' must be at most {MAX_REASON_LENGTH} characters.")
    return reason


def _state(arguments: dict[str, Any], states: dict[str, Any]) -> str:
    """Return the required ``state`` argument, validated against ``states``.

    Args:
        arguments: The raw ``arguments`` dict.
        states: The accepted states.

    Returns:
        The state, lower-cased.

    Raises:
        ToolError: The state is missing or not one of ``states``.

    """
    state = _require_str(arguments, "state").strip().lower()
    if state not in states:
        raise ToolError(f"'state' must be one of: {', '.join(states)}.")
    return state


def _flag_state(everyone: bool | None) -> str:
    """Return the state name for a flag's ``everyone`` value.

    Args:
        everyone: ``Flag.everyone``.

    Returns:
        ``on``, ``off`` or ``targeted``.

    """
    return next(state for state, value in FLAG_STATES.items() if value is everyone)


def _audit(actor: Any, row: Any, change: str) -> LogEntry:
    """Record one waffle change in the admin log and the application log.

    Args:
        actor: The staff user who made the change.
        row: The flag or switch changed (already saved).
        change: What changed and why, as the History page should read it.

    Returns:
        The new ``LogEntry``.

    """
    entry: LogEntry = LogEntry.objects.log_actions(
        user_id=actor.pk,
        queryset=[row],
        action_flag=CHANGE,
        change_message=change,
        single_object=True,
    )
    logger.warning(
        "ops_mcp: %s changed waffle %s %r: %s",
        actor.get_username(),
        type(row).__name__.lower(),
        row.name,
        change,
    )
    return entry


def set_switch(name: str, state: str, reason: str, actor: Any) -> dict[str, Any]:
    """Turn an existing waffle switch on or off, and audit the change.

    A switch already in ``state`` is left alone and nothing is logged. A
    name with no row is an error: the switch manifest creates rows, this
    never does.

    Args:
        name: The switch's exact name.
        state: ``on`` or ``off``.
        reason: Why, recorded with the change.
        actor: The staff user making the change.

    Returns:
        ``name``, ``previous``, ``state``, ``changed``, ``log_entry_id``
        and ``summary``.

    Raises:
        ToolError: No switch has that name.

    """
    from waffle.models import Switch

    active = SWITCH_STATES[state]
    with transaction.atomic():
        switch = Switch.objects.select_for_update().filter(name=name).first()
        if switch is None:
            raise ToolError(f"No switch named {name!r}; feature_flags lists them.")
        previous = "on" if switch.active else "off"
        entry = None
        if switch.active is not active:
            switch.active = active
            # save() also flushes waffle's cache of the switch.
            switch.save()
            entry = _audit(
                actor,
                switch,
                f"Switch turned {state} (was {previous}) via the ops MCP "
                f"server: {reason}",
            )
    changed = entry is not None
    return {
        "name": name,
        "previous": previous,
        "state": state,
        "changed": changed,
        "log_entry_id": entry.pk if entry else None,
        "summary": (
            f"Switch {name} turned {state} (was {previous})."
            if changed
            else f"Switch {name} was already {state}; nothing changed."
        ),
    }


def set_flag(name: str, state: str, reason: str, actor: Any) -> dict[str, Any]:
    """Set an existing waffle flag on, off or back to its targeting.

    Only ``Flag.everyone`` is written: ``on`` and ``off`` override the
    flag's other rules for every request, ``targeted`` clears the override
    so its percent, staff, groups and users decide again. A flag already in
    ``state`` is left alone and nothing is logged; an unknown name is an
    error, never a new row.

    Args:
        name: The flag's exact name.
        state: ``on``, ``off`` or ``targeted``.
        reason: Why, recorded with the change.
        actor: The staff user making the change.

    Returns:
        ``name``, ``previous``, ``state``, ``changed``, ``log_entry_id``
        and ``summary``.

    Raises:
        ToolError: No flag has that name.

    """
    from waffle.models import Flag

    everyone = FLAG_STATES[state]
    with transaction.atomic():
        flag = Flag.objects.select_for_update().filter(name=name).first()
        if flag is None:
            raise ToolError(f"No flag named {name!r}; feature_flags lists them.")
        previous = _flag_state(flag.everyone)
        entry = None
        if flag.everyone is not everyone:
            flag.everyone = everyone
            # save() also flushes waffle's cache of the flag.
            flag.save()
            entry = _audit(
                actor,
                flag,
                f"Flag set {state} (was {previous}) via the ops MCP server: {reason}",
            )
    changed = entry is not None
    return {
        "name": name,
        "previous": previous,
        "state": state,
        "changed": changed,
        "log_entry_id": entry.pk if entry else None,
        "summary": (
            f"Flag {name} set {state} (was {previous})."
            if changed
            else f"Flag {name} was already {state}; nothing changed."
        ),
    }


def _handle_set_switch(arguments: dict[str, Any], actor: Any) -> dict[str, Any]:
    """Validate ``set_switch`` arguments and call it."""
    return set_switch(
        _require_str(arguments, "name").strip(),
        _state(arguments, SWITCH_STATES),
        _reason(arguments),
        actor,
    )


def _handle_set_flag(arguments: dict[str, Any], actor: Any) -> dict[str, Any]:
    """Validate ``set_flag`` arguments and call it."""
    return set_flag(
        _require_str(arguments, "name").strip(),
        _state(arguments, FLAG_STATES),
        _reason(arguments),
        actor,
    )


def _no_arguments(tool: Any) -> Any:
    """Wrap a no-argument tool as a ``ToolSpec`` handler.

    Args:
        tool: The tool function.

    Returns:
        A handler that ignores ``arguments`` and calls the tool.

    """

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — the tool takes none
        """Call the tool."""
        result: dict[str, Any] = tool()
        return result

    return handler


_NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {}}

_LIMIT_SCHEMA: dict[str, Any] = {
    "type": "integer",
    "description": f"Most rows to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
}


OPS_TOOLS: dict[str, ToolSpec] = {
    "list_commands": ToolSpec(
        name="list_commands",
        description=(
            "List every management command that has run on this environment "
            "with its run count and last run (mode, status, release), and "
            "the project commands that never have. Start here for 'has X "
            "been run?'."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(list_commands),
    ),
    "list_command_runs": ToolSpec(
        name="list_command_runs",
        description=(
            "List management-command runs from the register, newest first, "
            "filtered by any of command, status, mode, how it was invoked, "
            "release, git SHA and start day. Each row carries its counts "
            "(records changed), arguments, operator and build."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Exact command name, e.g. 'fetch_bulletins'.",
                },
                "status": {
                    "type": "string",
                    "enum": list(CommandRun.STATUS.values),
                },
                "mode": {"type": "string", "enum": list(CommandRun.MODE.values)},
                "invoked_via": {
                    "type": "string",
                    "enum": list(CommandRun.INVOKED_VIA.values),
                },
                "release": {
                    "type": "string",
                    "description": "Release number, e.g. '45'.",
                },
                "git_sha": {
                    "type": "string",
                    "description": "Git SHA or a prefix of at least 7 characters.",
                },
                "since": {
                    "type": "string",
                    "description": (
                        "ISO date YYYY-MM-DD; runs started that day or later."
                    ),
                },
                "limit": _LIMIT_SCHEMA,
            },
        },
        handler=_handle_list_command_runs,
    ),
    "get_command_run": ToolSpec(
        name="get_command_run",
        description=(
            "Return one run in full: arguments, argv, counts, the last 16 KB "
            "of output, the traceback of a failure, host and Render "
            "instance, and the runs it started."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "A run id from list_command_runs.",
                }
            },
            "required": ["run_id"],
        },
        handler=_handle_get_command_run,
    ),
    "command_history": ToolSpec(
        name="command_history",
        description=(
            "Return one command's runs grouped by the build they ran on, "
            "with the latest dry run and commit run per build side by side, "
            "and whether it has been committed successfully on the build "
            "now deployed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Exact command name.",
                }
            },
            "required": ["command"],
        },
        handler=_handle_command_history,
    ),
    "deploy_steps_status": ToolSpec(
        name="deploy_steps_status",
        description=(
            "Return the deploy steps the current release shipped with (its "
            "Deploy-Step trailers) and, for each, whether it has run "
            "successfully on this release: DONE, FAILED, DRY_RUN_ONLY, "
            "PENDING or UNKNOWN_COMMAND, with the deciding run."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(deploy_steps_status),
    ),
    "build_info": ToolSpec(
        name="build_info",
        description=(
            "Return the build this environment serves: git SHA, release "
            "number, when it was released and when the process booted, "
            "Python and Django versions, and any unapplied migrations."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(build_info),
    ),
    "ingest_status": ToolSpec(
        name="ingest_status",
        description=(
            "Return, per bulletin provider, the latest issue time, target "
            "date and hours since issue, plus the last fetch_bulletins run "
            "and the past 24 hours of pipeline runs by status."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(ingest_status),
    ),
    "bulletin_coverage": ToolSpec(
        name="bulletin_coverage",
        description=(
            "Return, per provider, how many of its regions have a danger "
            "rating for a day and which are missing. Defaults to today."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Defaults to today.",
                }
            },
        },
        handler=_handle_bulletin_coverage,
    ),
    "task_queue_health": ToolSpec(
        name="task_queue_health",
        description=(
            "Return the background task queue's backlog by status, the age "
            "of the oldest waiting task and the past 24 hours of failures."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(task_queue_health),
    ),
    "weather_freshness": ToolSpec(
        name="weather_freshness",
        description=(
            "Return how many active locations have today's weather row, "
            "when weather was last fetched, and the last fetch_weather run."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(weather_freshness),
    ),
    "location_estate_health": ToolSpec(
        name="location_estate_health",
        description=(
            "Return the Location estate's size by kind and the work left for "
            "the fill commands: rows without elevation, without a what3words "
            "address, and orphaned."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(location_estate_health),
    ),
    "feature_flags": ToolSpec(
        name="feature_flags",
        description=(
            "Return every waffle feature flag's live targeting, every "
            "switch's on/off state and note, every sample's percent, the "
            "flags that differ from the committed manifest, and the last "
            "few changes from the admin log (who, when, why)."
        ),
        input_schema=_NO_ARGUMENTS,
        handler=_no_arguments(feature_flags),
    ),
    "set_switch": ToolSpec(
        name="set_switch",
        description=(
            "Turn an existing waffle switch on or off on this environment, "
            "e.g. a schedule_<job> switch to pause or resume a scheduled "
            "job. Takes effect at once for every request. Confirm the name "
            "and state with the operator before calling; the change and "
            "reason are recorded against their account in the admin "
            "History. A switch already in that state is left alone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact switch name, from feature_flags.",
                },
                "state": {"type": "string", "enum": list(SWITCH_STATES)},
                "reason": {
                    "type": "string",
                    "description": "Why, in a sentence; recorded with the change.",
                },
            },
            "required": ["name", "state", "reason"],
        },
        handler=_handle_set_switch,
        writes=True,
    ),
    "set_flag": ToolSpec(
        name="set_flag",
        description=(
            "Set an existing waffle flag on or off for everyone on this "
            "environment, or 'targeted' to clear that override so its "
            "percent, staff, groups and users decide again. Takes effect at "
            "once. Confirm the name and state with the operator before "
            "calling; the change and reason are recorded against their "
            "account in the admin History. A flag already in that state is "
            "left alone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact flag name, from feature_flags.",
                },
                "state": {"type": "string", "enum": list(FLAG_STATES)},
                "reason": {
                    "type": "string",
                    "description": "Why, in a sentence; recorded with the change.",
                },
            },
            "required": ["name", "state", "reason"],
        },
        handler=_handle_set_flag,
        writes=True,
    ),
}
