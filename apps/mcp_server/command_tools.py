"""
apps/mcp_server/command_tools.py — the ops MCP server's command runner tools.

Three tools that let a staff account start a deploy step or a backfill on
this environment and follow it, without a Render shell:

* ``list_runnable_commands`` — the commands marked ``mcp_runnable``, with
  their help, options and last run.
* ``run_command`` — **writes.** Checks the request and puts the command on
  the task queue, returning the task id at once; the task worker runs it.
  A dry run unless ``commit`` is true; every argument required to be
  deliberate, including the ``reason``.
* ``get_command_task`` — the task's state and, once it has started, the
  command's register row: status, counts and the tail of its output.

The rules — the opt-in, the dry-run default, the refused options, one run
at a time, no inline runs — live in ``apps.core.remote_commands``; this
module only adapts them to MCP. Merged into the ops catalogue by
``apps.mcp_server.ops_tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.tasks.exceptions import TaskResultDoesNotExist as CoreResultMissing
from django_tasks.exceptions import TaskException

from apps.core import remote_commands
from apps.core.models import CommandRun
from apps.mcp_server.tools import ToolError, ToolSpec, _require_str

logger = logging.getLogger(__name__)

# Matches the waffle setters: a reason is a sentence for the History page.
MAX_REASON_LENGTH: int = 500

# How much of a run's output get_command_task returns; get_command_run
# returns the whole 16 KB tail.
OUTPUT_TAIL_SHOWN: int = 4000


def _last_run(command: str) -> dict[str, Any] | None:
    """Return the newest register row for ``command``, briefly, or None."""
    run = CommandRun.objects.filter(command=command).order_by("-started_at").first()
    if run is None:
        return None
    return {
        "run_id": str(run.pk),
        "status": run.status,
        "mode": run.mode,
        "started_at": run.started_at.isoformat(),
        "invoked_via": run.invoked_via,
        "release": run.release,
    }


def list_runnable_commands() -> dict[str, Any]:
    """Return every command run_command can start, with its options.

    Returns:
        ``worker_available``, ``commands`` (name, app, help, whether it has
        ``--commit``, options and last run) and ``summary``.

    """
    rows = []
    for name, command in remote_commands.runnable_commands().items():
        rows.append(
            {
                "command": name,
                "app_label": command.app_label,
                "help": str(command.help),
                "accepts_commit": remote_commands.accepts_commit(command),
                "options": remote_commands.describe_options(command),
                "last_run": _last_run(name),
            }
        )
    worker = remote_commands.worker_available()
    summary = f"{len(rows)} commands can be run from here."
    if not worker:
        summary += (
            " This environment has no task worker, so run_command will refuse "
            "them; use a shell."
        )
    return {"worker_available": worker, "commands": rows, "summary": summary}


def run_command(
    command: str,
    arguments: list[str],
    commit: bool,
    reason: str,
    actor: Any,
) -> dict[str, Any]:
    """Queue one runnable command on the task worker.

    Args:
        command: The command's name.
        arguments: Its arguments, one token each, without ``--commit``.
        commit: Whether to run it with ``--commit``.
        reason: Why, recorded on its register row.
        actor: The staff user asking.

    Returns:
        ``task_id``, ``command``, ``argv``, ``mode``, ``status`` and
        ``summary``.

    Raises:
        ToolError: The request was refused; the message says why.

    """
    try:
        queued = remote_commands.enqueue_command(
            command,
            arguments,
            commit=commit,
            operator=actor.get_username(),
            reason=reason,
        )
    except remote_commands.RemoteCommandError as exc:
        raise ToolError(str(exc)) from exc
    mode = "commit" if queued.commit else "dry run"
    typed = " ".join([command, *queued.argv])
    return {
        "task_id": queued.task_id,
        "command": command,
        "argv": typed,
        "mode": CommandRun.MODE.COMMIT if queued.commit else CommandRun.MODE.DRY_RUN,
        "status": "READY",
        "summary": (
            f"Queued `{typed}` ({mode}) as task {queued.task_id}. Call "
            "get_command_task with that id to follow it."
        ),
    }


def get_command_task(task_id: str) -> dict[str, Any]:
    """Return a run_command task's state and the run it started.

    Args:
        task_id: The id run_command returned.

    Returns:
        ``task_id``, ``status``, ``enqueued_at``, ``started_at``,
        ``finished_at``, ``errors`` (the exception class and traceback of a
        failed task), ``run`` (the register row once the command has
        started: status, mode, counts, output tail, error) and ``summary``.

    Raises:
        ToolError: No task has that id.

    """
    try:
        result = remote_commands.run_command_task.get_result(task_id)
    except (TaskException, CoreResultMissing, ValueError) as exc:
        # TaskException covers a missing id and one belonging to another
        # task. django-tasks-db raises Django's own TaskResultDoesNotExist
        # (it is built on django.tasks), not the backport's. A malformed id
        # can be a ValueError from the UUID primary key lookup.
        raise ToolError(f"No command task {task_id!r}.") from exc
    row = CommandRun.objects.filter(task_id=task_id).order_by("-started_at").first()
    run = None
    if row is not None:
        run = {
            "run_id": str(row.pk),
            "status": row.status,
            "mode": row.mode,
            "argv": row.argv,
            "counts": row.counts,
            "operator": row.operator,
            "reason": row.reason,
            "duration_s": (
                row.duration.total_seconds() if row.duration is not None else None
            ),
            "output_tail": row.output_tail[-OUTPUT_TAIL_SHOWN:],
            "error": row.error[-OUTPUT_TAIL_SHOWN:],
        }
    status = str(result.status)
    if run is not None:
        summary = f"Task {status}; the run is {run['status']}."
    elif status == "READY":
        summary = "Task waiting for the worker; the command has not started."
    else:
        summary = f"Task {status}; no register row was written."
    return {
        "task_id": task_id,
        "status": status,
        "enqueued_at": result.enqueued_at.isoformat() if result.enqueued_at else None,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "errors": [
            {"exception": error.exception_class_path, "traceback": error.traceback}
            for error in result.errors
        ],
        "run": run,
        "summary": summary,
    }


def _handle_run_command(arguments: dict[str, Any], actor: Any) -> dict[str, Any]:
    """Validate ``run_command`` arguments and call it."""
    tokens = arguments.get("arguments", [])
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        raise ToolError("'arguments' must be a list of strings, one per token.")
    commit = arguments.get("commit")
    if not isinstance(commit, bool):
        raise ToolError("'commit' is required and must be true or false.")
    reason = _require_str(arguments, "reason").strip()
    if len(reason) > MAX_REASON_LENGTH:
        raise ToolError(f"'reason' must be at most {MAX_REASON_LENGTH} characters.")
    return run_command(
        _require_str(arguments, "command").strip(), tokens, commit, reason, actor
    )


def _handle_get_command_task(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``get_command_task`` arguments and call it."""
    return get_command_task(_require_str(arguments, "task_id").strip())


def _handle_list_runnable_commands(arguments: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — the tool takes none
    """Call ``list_runnable_commands``."""
    return list_runnable_commands()


COMMAND_TOOLS: dict[str, ToolSpec] = {
    "list_runnable_commands": ToolSpec(
        name="list_runnable_commands",
        description=(
            "List the management commands run_command can start on this "
            "environment, each with its help text, its options, whether it "
            "has a --commit flag, and its last run. Says whether the "
            "environment has a task worker to run them."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_handle_list_runnable_commands,
    ),
    "run_command": ToolSpec(
        name="run_command",
        description=(
            "Start a management command on this environment's task worker "
            "and return its task id at once (it never runs in the request). "
            "A dry run unless commit is true; run the dry run first and "
            "show the operator its counts. Confirm the command, arguments "
            "and commit with the operator before calling; the run and the "
            "reason are recorded against their account in the command "
            "register. Refused for a command already running, and on an "
            "environment with no task worker. Follow it with "
            "get_command_task."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Exact name, from list_runnable_commands.",
                },
                "arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Arguments as command-line tokens, e.g. "
                        "['--limit', '20']. Never --commit: use commit."
                    ),
                },
                "commit": {
                    "type": "boolean",
                    "description": "True adds --commit; false is a dry run.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why, in a sentence; recorded with the run.",
                },
            },
            "required": ["command", "commit", "reason"],
        },
        handler=_handle_run_command,
        writes=True,
    ),
    "get_command_task": ToolSpec(
        name="get_command_task",
        description=(
            "Return the state of a command task run_command queued — "
            "waiting, running, successful or failed — and, once the command "
            "has started, its register row: status, counts, duration and "
            "the tail of its output."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task id run_command returned.",
                }
            },
            "required": ["task_id"],
        },
        handler=_handle_get_command_task,
    ),
}
