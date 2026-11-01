"""
apps/core/remote_commands.py — running a management command from the ops MCP server.

The ops MCP server's ``run_command`` tool lets a staff account start a
deploy step or a backfill without opening a Render shell. This module is
everything about that which is not MCP: which commands are offered, how a
request is checked, and the task that runs one.

Five rules shape it:

1. **Opt-in.** Only a ``RegisteredCommand`` with ``mcp_runnable = True`` is
   offered (``runnable_commands``). The attribute is the allow-list; a
   guard test (``tests/core/test_command_register_guard.py``) refuses it on
   a command that is not recorded or prints a credential.
2. **The request returns at once.** ``enqueue_command`` puts one
   ``run_command_task`` on the task queue and returns its id; the command
   runs on the task worker (production's ``db_worker``). A command run
   inside the web request would be cut off by gunicorn's 30-second timeout
   and leave its register row ``RUNNING`` forever. Where the backend runs
   tasks inline — dev, test and staging, which has no worker — nothing is
   enqueued and the caller is told to use a shell (``worker_available``).
3. **A dry run unless asked.** ``--commit`` is never accepted inside the
   arguments; the caller passes ``commit=True``, so a write is always an
   explicit, separate choice. The arguments are parsed with the command's
   own parser before anything is queued, so a typo is an error now rather
   than a failed task later.
4. **No reach outside the command.** Django's ``--settings`` and
   ``--pythonpath``, and every option that names a file on the server
   (``DENIED_OPTIONS``), are refused.
5. **One at a time.** A command already queued or running — from
   ``run_command``, the scheduler or a shell — is not started again
   (``_already_running``).

The run is recorded in the ``CommandRun`` register like any other, as
``invoked_via=MCP`` with the staff account as its operator, the reason
given and the task id (``remote_invocation``).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.utils import timezone
from django_tasks import TaskContext, TaskResultStatus, task
from django_tasks.backends.immediate import ImmediateBackend
from django_tasks_db import DatabaseBackend
from django_tasks_db.models import DBTaskResult

from apps.core.command_register import (
    RegisteredCommand,
    RemoteInvocation,
    remote_invocation,
)
from apps.core.models import CommandRun

logger = logging.getLogger(__name__)

# Option destinations run_command refuses: Django's own reach into the
# process (which settings module, which import path), and every project
# option that names a file on the server to read or write. A command is
# run against this environment's database and nothing else.
DENIED_OPTIONS: frozenset[str] = frozenset(
    {
        "settings",
        "pythonpath",
        "archive",
        "file",
        "links_file",
        "local_mirror",
        "manifest",
        "output",
        "passes_file",
        "stash",
    }
)

# Options run_command does not list: Django's plumbing, which says how a
# command is wired rather than what it does.
_HIDDEN_OPTIONS: frozenset[str] = frozenset(
    {"help", "version", "no_color", "force_color", "skip_checks", "traceback"}
)

# The most arguments, and the longest one, run_command accepts. Every
# runnable command takes a handful of short flags.
MAX_ARGUMENTS: int = 20
MAX_ARGUMENT_LENGTH: int = 200

# A RUNNING register row older than this is a run whose process died, not
# one in progress — prune_command_runs closes such rows at the same age.
STALE_AFTER: timedelta = timedelta(hours=6)


class RemoteCommandError(Exception):
    """A run_command request that cannot be queued, with the reason why."""


@dataclass(frozen=True)
class QueuedCommand:
    """A command run_command has put on the task queue."""

    task_id: str
    command: str
    argv: list[str]
    commit: bool


def runnable_commands() -> dict[str, RegisteredCommand]:
    """Return ``{name: command}`` for every command marked ``mcp_runnable``.

    Returns:
        The opted-in project commands, by name, in name order.

    """
    runnable: dict[str, RegisteredCommand] = {}
    for name, app in sorted(get_commands().items()):
        if not app.startswith("apps."):
            continue
        command = load_command_class(app, name)
        if isinstance(command, RegisteredCommand) and command.mcp_runnable:
            runnable[name] = command
    return runnable


def _command(name: str) -> RegisteredCommand:
    """Return the runnable command called ``name``.

    Raises:
        RemoteCommandError: No runnable command has that name.

    """
    command = runnable_commands().get(name)
    if command is None:
        raise RemoteCommandError(
            f"{name!r} is not a command run_command can start; "
            "list_runnable_commands lists them."
        )
    return command


def describe_options(command: RegisteredCommand) -> list[dict[str, Any]]:
    """Return the options run_command accepts for ``command``.

    Args:
        command: A runnable command.

    Returns:
        One dict per option — ``flags``, ``help``, ``default``, and
        ``takes_value`` — leaving out ``--commit`` (the tool's ``commit``
        argument), Django's plumbing and the refused options.

    """
    parser = command.create_parser("manage.py", command.command_name)
    options: list[dict[str, Any]] = []
    for action in parser._actions:  # argparse exposes no public action list
        if action.dest in _HIDDEN_OPTIONS | DENIED_OPTIONS or action.dest == "commit":
            continue
        options.append(
            {
                "flags": list(action.option_strings) or [action.dest],
                "help": action.help or "",
                "default": action.default,
                "takes_value": action.nargs != 0,
            }
        )
    return options


def accepts_commit(command: RegisteredCommand) -> bool:
    """Return whether ``command`` has a ``--commit`` flag."""
    parser = command.create_parser("manage.py", command.command_name)
    return any(action.dest == "commit" for action in parser._actions)


def _parsed(command: RegisteredCommand, argv: list[str]) -> dict[str, Any]:
    """Parse ``argv`` with the command's own parser.

    Args:
        command: The command.
        argv: Its arguments.

    Returns:
        The parsed options, by destination.

    Raises:
        RemoteCommandError: The arguments do not parse, or ask for help.

    """
    parser = command.create_parser("manage.py", command.command_name)
    try:
        return vars(parser.parse_args(argv))
    except CommandError as exc:
        raise RemoteCommandError(f"{command.command_name}: {exc}") from exc
    except SystemExit as exc:
        # argparse exits after printing --help or --version.
        raise RemoteCommandError(
            "--help and --version are not arguments; list_runnable_commands "
            "describes each command's options."
        ) from exc


def _refused_options(command: RegisteredCommand, options: dict[str, Any]) -> list[str]:
    """Return the ``DENIED_OPTIONS`` that ``options`` sets.

    Compared as parsed, not as typed, so an abbreviation argparse expands
    (``--local`` for ``--local-mirror``) is caught as well.
    """
    parser = command.create_parser("manage.py", command.command_name)
    defaults = {
        action.dest: action.default
        for action in parser._actions  # argparse exposes no public action list
    }
    return sorted(
        dest
        for dest in DENIED_OPTIONS
        if dest in options and options[dest] != defaults.get(dest)
    )


def check_arguments(name: str, arguments: list[str], *, commit: bool) -> list[str]:
    """Validate a run_command request and return the argv to run.

    Args:
        name: The command's name.
        arguments: Its arguments, one token each, without ``--commit``.
        commit: Whether to add ``--commit``.

    Returns:
        ``arguments``, plus ``--commit`` when ``commit``.

    Raises:
        RemoteCommandError: The command is not runnable, an argument is
            refused or malformed, or ``commit`` was asked of a command with
            no ``--commit`` flag.

    """
    command = _command(name)
    if len(arguments) > MAX_ARGUMENTS or any(
        len(token) > MAX_ARGUMENT_LENGTH for token in arguments
    ):
        raise RemoteCommandError(
            f"At most {MAX_ARGUMENTS} arguments, each at most "
            f"{MAX_ARGUMENT_LENGTH} characters."
        )
    options = _parsed(command, list(arguments))
    if options.get("commit"):
        raise RemoteCommandError(
            "Pass commit true rather than --commit in the arguments, so a "
            "write is always a separate, explicit choice."
        )
    refused = _refused_options(command, options)
    if refused:
        raise RemoteCommandError(
            f"run_command does not accept {', '.join(refused)}: it runs against "
            "this environment's settings and never reads or writes a file on "
            "the server. Use a shell for that."
        )
    if not commit:
        return list(arguments)
    if not accepts_commit(command):
        raise RemoteCommandError(
            f"{name} has no --commit flag; it is read-only, so run it with "
            "commit false."
        )
    return [*arguments, "--commit"]


def worker_available() -> bool:
    """Return whether enqueued tasks run on a worker rather than inline.

    False under ``ImmediateBackend`` (dev, test and staging): there an
    enqueue runs the command inside the web request, which is exactly what
    run_command exists to avoid.
    """
    return not isinstance(run_command_task.get_backend(), ImmediateBackend)


def _already_running(name: str) -> bool:
    """Return whether ``name`` is queued or running now.

    A run_command task still waiting on the queue has no register row yet,
    so the task queue is checked as well as the register (a RUNNING row from
    any caller, newer than ``STALE_AFTER``).
    """
    running = CommandRun.objects.filter(
        command=name,
        status=CommandRun.STATUS.RUNNING,
        started_at__gte=timezone.now() - STALE_AFTER,
    ).exists()
    return running or _queued(name)


def _queued(name: str) -> bool:
    """Return whether a run_command task for ``name`` is waiting or running.

    Only the database backend can be asked; any other answers False.
    """
    if not isinstance(run_command_task.get_backend(), DatabaseBackend):
        return False
    return DBTaskResult.objects.filter(
        task_path=run_command_task.module_path,
        status__in=[TaskResultStatus.READY, TaskResultStatus.RUNNING],
        args_kwargs__kwargs__command=name,
    ).exists()


def enqueue_command(
    name: str,
    arguments: list[str],
    *,
    commit: bool,
    operator: str,
    reason: str,
) -> QueuedCommand:
    """Check a run_command request and put the command on the task queue.

    Args:
        name: The command's name.
        arguments: Its arguments, without ``--commit``.
        commit: Whether to run it with ``--commit``.
        operator: The staff account asking, recorded as the run's operator.
        reason: Why, recorded on the run.

    Returns:
        The queued command, with its task id.

    Raises:
        RemoteCommandError: The request is refused (see ``check_arguments``),
            there is no task worker, or the command is already running.

    """
    argv = check_arguments(name, arguments, commit=commit)
    if not worker_available():
        raise RemoteCommandError(
            "This environment runs background tasks inline (it has no task "
            "worker), so the command would run inside this request and could "
            "be cut off part-way. Run it from a shell here."
        )
    if _already_running(name):
        raise RemoteCommandError(
            f"{name} is already queued or running; list_command_runs shows "
            "the run. Wait for it to finish."
        )
    result = run_command_task.enqueue(
        command=name, argv=argv, operator=operator, reason=reason
    )
    logger.warning(
        "remote command: %s queued %s %s as task %s: %s",
        operator,
        name,
        " ".join(argv),
        result.id,
        reason,
    )
    return QueuedCommand(task_id=result.id, command=name, argv=argv, commit=commit)


@task(takes_context=True)
def run_command_task(
    context: TaskContext,
    command: str,
    argv: list[str],
    operator: str,
    reason: str,
) -> dict[str, Any]:
    """Background worker: run one management command queued by run_command.

    Re-checks the command is still runnable (the attribute may have been
    removed by a deploy between queueing and running), then runs it under
    ``remote_invocation`` so its register row is attributed to the staff
    account and carries the task id.

    Args:
        context: The task context, for this task's own id.
        command: The command's name.
        argv: The checked arguments, ``--commit`` included when asked.
        operator: The staff account that queued it.
        reason: Why.

    Returns:
        ``run_id`` — the register row's primary key — and ``output_tail``,
        the last of what it printed, kept on the task result.

    Raises:
        RemoteCommandError: The command is no longer runnable.
        RuntimeError: The command called ``sys.exit`` with a failure code;
            re-raised as an ordinary error so the worker process survives.

    """
    instance = _command(command)
    out = io.StringIO()
    invocation = RemoteInvocation(
        operator=operator, reason=reason, task_id=str(context.task_result.id)
    )
    # Shown on the register row as the command line would have read.
    instance._argv = list(argv)
    with remote_invocation(invocation):
        try:
            call_command(instance, *argv, stdout=out, stderr=out)
        except SystemExit as exc:
            # A worker must not exit because one command did.
            if exc.code not in (0, None):
                raise RuntimeError(f"{command} exited with {exc.code}") from exc
    return {"run_id": instance.current_run_id, "output_tail": out.getvalue()[-2000:]}
