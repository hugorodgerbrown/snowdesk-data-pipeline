"""
tests/core/test_remote_commands.py — running a command from the ops MCP server.

Pins the five rules in ``apps.core.remote_commands``' docstring: only an
``mcp_runnable`` command is offered; nothing runs inside the request (an
inline task backend is refused); a dry run unless ``commit`` is passed and
never ``--commit`` in the arguments; no settings, import path or server
file; and one run of a command at a time. Then the task itself: the run it
records is attributed to the staff account, carries the reason and the task
id, and a command's ``sys.exit`` never takes the worker down.
"""

from __future__ import annotations

import datetime as dt
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone
from django_tasks import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from apps.core import remote_commands
from apps.core.models import CommandRun
from apps.core.remote_commands import RemoteCommandError, check_arguments
from tests.factories import CommandRunFactory

pytestmark = pytest.mark.django_db

DUMMY = {"default": {"BACKEND": "django_tasks.backends.dummy.DummyBackend"}}
DATABASE = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


def _enqueue(name: str = "prune_command_runs", **kwargs: object) -> object:
    """Call ``enqueue_command`` with the test operator and reason."""
    options = {"commit": False, "operator": "hugo", "reason": "testing"}
    options.update(kwargs)
    return remote_commands.enqueue_command(name, [], **options)  # type: ignore[arg-type]


class TestRunnableCommands:
    """Only commands marked ``mcp_runnable`` are offered."""

    def test_offers_opted_in_commands(self) -> None:
        """A marked command is offered."""
        assert "rebuild_render_models" in remote_commands.runnable_commands()

    def test_leaves_out_unmarked_commands(self) -> None:
        """An unmarked command — a seed, a credential printer — is not."""
        runnable = remote_commands.runnable_commands()
        assert "seed_test_data" not in runnable
        assert "mint_feed_token" not in runnable

    def test_describes_options_without_commit_or_refused_ones(self) -> None:
        """The listed options leave out --commit, plumbing and server files."""
        command = remote_commands.runnable_commands()["sync_waffle_flags"]
        flags = {
            flag
            for option in remote_commands.describe_options(command)
            for flag in option["flags"]
        }
        assert "--verbosity" in flags
        assert "--commit" not in flags
        assert "--manifest" not in flags
        assert "--settings" not in flags
        assert "--traceback" not in flags

    def test_accepts_commit(self) -> None:
        """A --commit command says so; a read-only one does not."""
        runnable = remote_commands.runnable_commands()
        assert remote_commands.accepts_commit(runnable["prune_command_runs"])
        assert not remote_commands.accepts_commit(runnable["diagnose_region_coverage"])


class TestCheckArguments:
    """A request is checked against the command's own parser before queueing."""

    def test_dry_run_is_the_arguments_as_given(self) -> None:
        """commit false runs exactly the arguments."""
        argv = check_arguments("prune_command_runs", ["--days", "30"], commit=False)
        assert argv == ["--days", "30"]

    def test_commit_appends_the_flag(self) -> None:
        """commit true adds --commit."""
        argv = check_arguments("prune_command_runs", [], commit=True)
        assert argv == ["--commit"]

    def test_refuses_an_unrunnable_command(self) -> None:
        """A command without the attribute is refused by name."""
        with pytest.raises(RemoteCommandError, match="list_runnable_commands"):
            check_arguments("seed_test_data", [], commit=False)

    def test_refuses_commit_in_the_arguments(self) -> None:
        """--commit, or an abbreviation of it, is never an argument."""
        for token in ("--commit", "--com"):
            with pytest.raises(RemoteCommandError, match="commit true"):
                check_arguments("prune_command_runs", [token], commit=False)

    def test_refuses_commit_on_a_read_only_command(self) -> None:
        """commit true needs a --commit flag to add."""
        with pytest.raises(RemoteCommandError, match="no --commit flag"):
            check_arguments("diagnose_region_coverage", [], commit=True)

    def test_refuses_an_unknown_option(self) -> None:
        """A typo is an error now, not a failed task later."""
        with pytest.raises(RemoteCommandError, match="prune_command_runs"):
            check_arguments("prune_command_runs", ["--dayz", "3"], commit=False)

    def test_refuses_help(self) -> None:
        """--help would print and exit; it is refused instead."""
        with pytest.raises(RemoteCommandError, match="--help"):
            check_arguments("prune_command_runs", ["--help"], commit=False)

    def test_refuses_settings_and_pythonpath(self) -> None:
        """Django's reach into the process is refused."""
        for argv in (["--settings", "config.settings.base"], ["--pythonpath=/tmp"]):
            with pytest.raises(RemoteCommandError, match="does not accept"):
                check_arguments("prune_command_runs", argv, commit=False)

    def test_refuses_a_server_file_even_abbreviated(self) -> None:
        """An option naming a server file is refused, typed in full or not."""
        with pytest.raises(RemoteCommandError, match="manifest"):
            check_arguments(
                "sync_waffle_flags", ["--manifest", "/etc/passwd"], commit=False
            )
        with pytest.raises(RemoteCommandError, match="local_mirror"):
            check_arguments(
                "fetch_bulletins", ["--source", "slf", "--local"], commit=False
            )

    def test_refuses_too_many_or_too_long_arguments(self) -> None:
        """The arguments are bounded."""
        with pytest.raises(RemoteCommandError, match="At most"):
            check_arguments("prune_command_runs", ["-v"] * 21, commit=False)
        with pytest.raises(RemoteCommandError, match="At most"):
            check_arguments("prune_command_runs", ["x" * 201], commit=False)


class TestEnqueueCommand:
    """The command goes on the task queue, never into the request."""

    def test_refuses_an_inline_backend(self) -> None:
        """Under ImmediateBackend (the test default, and staging) nothing runs."""
        assert not remote_commands.worker_available()
        with pytest.raises(RemoteCommandError, match="no task worker"):
            _enqueue()
        assert not CommandRun.objects.exists()

    @override_settings(TASKS=DUMMY)
    def test_queues_the_task(self) -> None:
        """With a worker the task is queued and its id returned."""
        queued = _enqueue(commit=True)
        assert queued.task_id  # type: ignore[attr-defined]
        assert queued.argv == ["--commit"]  # type: ignore[attr-defined]
        result = remote_commands.run_command_task.get_result(queued.task_id)  # type: ignore[attr-defined]
        assert result.status == TaskResultStatus.READY
        assert result.kwargs == {
            "command": "prune_command_runs",
            "argv": ["--commit"],
            "operator": "hugo",
            "reason": "testing",
        }

    @override_settings(TASKS=DUMMY)
    def test_refuses_a_command_already_running(self) -> None:
        """A RUNNING register row from any caller blocks a second run."""
        CommandRunFactory.create(
            command="prune_command_runs",
            status=CommandRun.STATUS.RUNNING,
            finished_at=None,
        )
        with pytest.raises(RemoteCommandError, match="already queued or running"):
            _enqueue()

    @override_settings(TASKS=DUMMY)
    def test_a_stale_running_row_does_not_block(self) -> None:
        """A RUNNING row older than STALE_AFTER is a dead process, not a run."""
        CommandRunFactory.create(
            command="prune_command_runs",
            status=CommandRun.STATUS.RUNNING,
            started_at=timezone.now() - dt.timedelta(hours=7),
            finished_at=None,
        )
        assert _enqueue().task_id  # type: ignore[attr-defined]

    @override_settings(TASKS=DATABASE)
    def test_refuses_a_command_already_queued(self) -> None:
        """A queued task with no register row yet also blocks a second run.

        The queued row is written directly rather than through enqueue:
        what is under test is the query, not django-tasks-db's enqueue.
        """
        DBTaskResult.objects.create(
            task_path=remote_commands.run_command_task.module_path,
            status=TaskResultStatus.READY,
            args_kwargs={"args": [], "kwargs": {"command": "prune_command_runs"}},
            backend_name="default",
            run_after=timezone.now(),
        )
        with pytest.raises(RemoteCommandError, match="already queued or running"):
            _enqueue()
        # Another command is not blocked by it.
        assert not remote_commands._queued("purge_request_logs")

    @override_settings(TASKS=DUMMY)
    def test_only_the_database_backend_is_asked_about_its_queue(self) -> None:
        """Another backend has no queue to ask, so nothing counts as queued."""
        assert not remote_commands._queued("prune_command_runs")


class TestRunCommandTask:
    """The task runs the command and attributes the run to the operator."""

    def _run(self, command: str = "prune_command_runs") -> object:
        """Enqueue the task directly; ImmediateBackend runs it at once."""
        return remote_commands.run_command_task.enqueue(
            command=command, argv=["--commit"], operator="hugo", reason="post-deploy"
        )

    def test_records_the_run_as_mcp(self) -> None:
        """The register row names MCP, the operator, the reason and the task."""
        result = self._run()
        assert result.status == TaskResultStatus.SUCCESSFUL  # type: ignore[attr-defined]
        run = CommandRun.objects.get()
        assert run.command == "prune_command_runs"
        assert run.invoked_via == CommandRun.INVOKED_VIA.MCP
        assert run.operator == "hugo"
        assert run.reason == "post-deploy"
        assert run.task_id == str(result.id)  # type: ignore[attr-defined]
        assert run.mode == CommandRun.MODE.COMMIT
        assert run.argv == "prune_command_runs --commit"
        assert result.return_value["run_id"] == run.pk  # type: ignore[attr-defined]

    def test_refuses_a_command_no_longer_runnable(self) -> None:
        """A command that lost the attribute since queueing does not run."""
        result = self._run("seed_test_data")
        assert result.status == TaskResultStatus.FAILED  # type: ignore[attr-defined]
        assert not CommandRun.objects.exists()

    def test_a_failing_exit_fails_the_task_not_the_worker(self) -> None:
        """sys.exit(2) inside a command becomes an ordinary task failure."""
        with mock.patch.object(
            remote_commands, "call_command", side_effect=SystemExit(2)
        ):
            result = self._run()
        assert result.status == TaskResultStatus.FAILED  # type: ignore[attr-defined]
        assert result.errors[0].exception_class_path == "builtins.RuntimeError"  # type: ignore[attr-defined]

    def test_a_clean_exit_succeeds(self) -> None:
        """sys.exit(0) is a success."""
        with mock.patch.object(
            remote_commands, "call_command", side_effect=SystemExit(0)
        ):
            result = self._run()
        assert result.status == TaskResultStatus.SUCCESSFUL  # type: ignore[attr-defined]
