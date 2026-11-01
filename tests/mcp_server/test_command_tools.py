"""
tests/mcp_server/test_command_tools.py — the ops MCP server's command runner.

``list_runnable_commands``, ``run_command`` and ``get_command_task``, called
through the JSON-RPC dispatcher the way ``ops_mcp_endpoint`` reaches them.
The rules behind them (opt-in, dry-run default, refused options, one at a
time) are pinned in ``tests/core/test_remote_commands.py``; these tests pin
the MCP side: the arguments, the write annotations, the actor, and what a
caller reads back.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from apps.core import remote_commands
from apps.core.models import CommandRun
from apps.mcp_server import ops_tools, protocol
from tests.factories import CommandRunFactory, UserFactory

pytestmark = pytest.mark.django_db

DUMMY = {"default": {"BACKEND": "django_tasks.backends.dummy.DummyBackend"}}


@pytest.fixture
def worker(settings: Any) -> None:
    """Give the environment a task backend that queues rather than runs inline."""
    settings.TASKS = DUMMY


def _server(actor: Any = None) -> protocol.ToolServer:
    """Return the ops server, with ``actor`` bound."""
    return protocol.ToolServer(
        name="snowdesk-ops", tools=ops_tools.OPS_TOOLS, resources={}, actor=actor
    )


def _call(
    name: str, arguments: dict[str, Any] | None = None, actor: Any = None
) -> dict[str, Any]:
    """Call a tool through the dispatcher and return its CallToolResult."""
    response = protocol.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        _server(actor),
    )
    assert response is not None
    result: dict[str, Any] = response["result"]
    return result


def _staff() -> Any:
    """Return a staff user."""
    return UserFactory.create(username="hugo", is_staff=True)


def _run_args(**overrides: Any) -> dict[str, Any]:
    """Return valid run_command arguments, overridden as given."""
    arguments = {
        "command": "prune_command_runs",
        "arguments": ["--days", "30"],
        "commit": False,
        "reason": "checking what would be pruned",
    }
    arguments.update(overrides)
    return arguments


class TestCatalogue:
    """The three tools are on the ops server, and only run_command writes."""

    def test_tools_are_listed_with_write_annotations(self) -> None:
        """run_command is advertised as destructive; the readers are not."""
        response = protocol.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, _server()
        )
        assert response is not None
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        assert {"list_runnable_commands", "run_command", "get_command_task"} <= set(
            tools
        )
        assert tools["run_command"]["annotations"]["destructiveHint"] is True
        assert "annotations" not in tools["get_command_task"]


class TestListRunnableCommands:
    """list_runnable_commands describes what can be run."""

    def test_lists_the_commands_with_options_and_last_run(self) -> None:
        """Each command carries its help, options and last run."""
        CommandRunFactory.create(command="prune_command_runs")
        result = _call("list_runnable_commands")["structuredContent"]
        rows = {row["command"]: row for row in result["commands"]}
        assert "seed_test_data" not in rows
        prune = rows["prune_command_runs"]
        assert prune["accepts_commit"] is True
        assert prune["app_label"] == "core"
        assert prune["last_run"]["status"] == CommandRun.STATUS.SUCCEEDED
        assert any("--days" in option["flags"] for option in prune["options"])
        assert rows["diagnose_region_coverage"]["accepts_commit"] is False

    def test_says_when_there_is_no_worker(self) -> None:
        """Under an inline backend the summary warns run_command will refuse."""
        result = _call("list_runnable_commands")["structuredContent"]
        assert result["worker_available"] is False
        assert "no task worker" in result["summary"]

    @override_settings(TASKS=DUMMY)
    def test_with_a_worker_no_warning(self) -> None:
        """With a worker the summary is just the count."""
        result = _call("list_runnable_commands")["structuredContent"]
        assert result["worker_available"] is True
        assert "no task worker" not in result["summary"]


@pytest.mark.usefixtures("worker")
class TestRunCommand:
    """run_command queues the command and returns its task id."""

    def test_queues_a_dry_run(self) -> None:
        """A dry run is queued with the operator and reason."""
        result = _call("run_command", _run_args(), actor=_staff())
        assert result["isError"] is False
        body = result["structuredContent"]
        assert body["mode"] == CommandRun.MODE.DRY_RUN
        assert body["argv"] == "prune_command_runs --days 30"
        queued = remote_commands.run_command_task.get_result(body["task_id"])
        assert queued.kwargs["operator"] == "hugo"
        assert queued.kwargs["reason"] == "checking what would be pruned"

    def test_queues_a_commit_run(self) -> None:
        """commit true adds --commit."""
        body = _call("run_command", _run_args(commit=True), actor=_staff())[
            "structuredContent"
        ]
        assert body["mode"] == CommandRun.MODE.COMMIT
        assert body["argv"].endswith("--commit")

    def test_refuses_without_an_actor(self) -> None:
        """A server with no actor bound refuses the write."""
        assert _call("run_command", _run_args())["isError"] is True

    def test_a_refusal_is_a_tool_error(self) -> None:
        """A refused request reads back as the reason, not a crash."""
        result = _call("run_command", _run_args(command="seed_test_data"), _staff())
        assert result["isError"] is True
        assert "list_runnable_commands" in result["content"][0]["text"]

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"arguments": "--days 30"}, "list of strings"),
            ({"arguments": [30]}, "list of strings"),
            ({"commit": "yes"}, "true or false"),
            ({"reason": ""}, "reason"),
            ({"reason": "x" * 501}, "at most 500"),
            ({"command": ""}, "command"),
        ],
    )
    def test_validates_its_arguments(
        self, overrides: dict[str, Any], message: str
    ) -> None:
        """Every argument is checked before anything is queued."""
        result = _call("run_command", _run_args(**overrides), actor=_staff())
        assert result["isError"] is True
        assert message in result["content"][0]["text"]

    def test_commit_is_required(self) -> None:
        """There is no default for commit: the caller always chooses."""
        arguments = _run_args()
        del arguments["commit"]
        assert _call("run_command", arguments, actor=_staff())["isError"] is True


@pytest.mark.usefixtures("worker")
class TestGetCommandTask:
    """get_command_task follows a queued command."""

    def _queue(self) -> str:
        """Queue a dry run and return its task id."""
        body = _call("run_command", _run_args(), actor=_staff())["structuredContent"]
        task_id: str = body["task_id"]
        return task_id

    def test_a_waiting_task_has_no_run(self) -> None:
        """Before the worker picks it up there is no register row."""
        body = _call("get_command_task", {"task_id": self._queue()})[
            "structuredContent"
        ]
        assert body["status"] == "READY"
        assert body["run"] is None
        assert "has not started" in body["summary"]

    def test_a_started_task_shows_its_run(self) -> None:
        """Once the command has a register row, it is returned."""
        task_id = self._queue()
        CommandRunFactory.create(
            command="prune_command_runs",
            task_id=task_id,
            invoked_via=CommandRun.INVOKED_VIA.MCP,
            reason="checking",
            counts={"closed": 2},
            output_tail="closed 2 stale runs",
        )
        body = _call("get_command_task", {"task_id": task_id})["structuredContent"]
        assert body["run"]["counts"] == {"closed": 2}
        assert body["run"]["reason"] == "checking"
        assert body["run"]["output_tail"] == "closed 2 stale runs"
        assert body["run"]["duration_s"] == 0
        assert "the run is SUCCEEDED" in body["summary"]

    def test_an_unknown_task_is_an_error(self) -> None:
        """An id no task has is a tool error."""
        result = _call(
            "get_command_task", {"task_id": "00000000-0000-0000-0000-000000000000"}
        )
        assert result["isError"] is True
        assert "No command task" in result["content"][0]["text"]
