"""
tests/mcp_server/test_ops_tools.py — Tests for apps.mcp_server.ops_tools.

One class per tool in the staff ops catalogue (SNOW-1191): the four command
run register tools and the seven health tools. Each tool's logic is called
directly; the ``_handle_*`` adapters are exercised through ``protocol``
for argument validation, the way the endpoint reaches them.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from waffle.models import Flag

from apps.bulletins.models import Bulletin, PipelineRun, RegionDayRating
from apps.core.models import CommandRun
from apps.mcp_server import ops_tools, protocol
from tests.factories import (
    BulletinFactory,
    CommandRunFactory,
    LocationFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    RegionDayRatingFactory,
    ResortLocationFactory,
    SubRegionFactory,
    WeatherFactory,
)

OPS = protocol.ToolServer(name="snowdesk-ops", tools=ops_tools.OPS_TOOLS, resources={})


def _call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an ops tool through the JSON-RPC dispatcher.

    Args:
        name: The tool name.
        arguments: The tool's arguments.

    Returns:
        The ``CallToolResult``.

    """
    response = protocol.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        OPS,
    )
    assert response is not None
    result: dict[str, Any] = response["result"]
    return result


@pytest.mark.django_db
class TestServer:
    """The ops server speaks the shared protocol under its own name."""

    def test_lists_only_ops_tools(self) -> None:
        """tools/list names the ops tools and none of the public ones."""
        response = protocol.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, OPS
        )
        assert response is not None
        names = {tool["name"] for tool in response["result"]["tools"]}
        assert names == set(ops_tools.OPS_TOOLS)
        assert "get_current_conditions" not in names

    def test_initialize_names_the_server(self) -> None:
        """serverInfo carries the ops server's name."""
        response = protocol.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, OPS
        )
        assert response is not None
        assert response["result"]["serverInfo"]["name"] == "snowdesk-ops"

    def test_has_no_views(self) -> None:
        """resources/list is empty: the ops server ships no MCP Apps views."""
        response = protocol.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, OPS
        )
        assert response is not None
        assert response["result"]["resources"] == []

    def test_public_server_cannot_call_an_ops_tool(self) -> None:
        """The public endpoint's dispatcher does not know the ops tools."""
        response = protocol.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_command_runs", "arguments": {}},
            }
        )
        assert response is not None
        assert response["error"]["code"] == protocol.INVALID_PARAMS


@pytest.mark.django_db
class TestListCommands:
    """list_commands: what has run, and what never has."""

    def test_counts_and_last_run(self) -> None:
        """Each command reports its runs, commit runs and newest run."""
        now = timezone.now()
        CommandRunFactory.create(started_at=now - dt.timedelta(hours=2))
        CommandRunFactory.create(
            started_at=now - dt.timedelta(hours=1), mode=CommandRun.MODE.DRY_RUN
        )

        result = ops_tools.list_commands()

        [entry] = result["commands"]
        assert entry["command"] == "relink_bulletin_regions"
        assert entry["runs"] == 2
        assert entry["commit_runs"] == 1
        assert entry["last_run"]["mode"] == CommandRun.MODE.DRY_RUN

    def test_never_run_lists_project_commands_only(self) -> None:
        """A project command with no row is never_run; Django's own are not."""
        CommandRunFactory.create()

        result = ops_tools.list_commands()

        assert "fetch_bulletins" in result["never_run"]
        assert "relink_bulletin_regions" not in result["never_run"]
        assert "migrate" not in result["never_run"]


@pytest.mark.django_db
class TestListCommandRuns:
    """list_command_runs: filters, ordering and the limit."""

    def test_filters_combine(self) -> None:
        """Only runs matching every filter are returned."""
        CommandRunFactory.create(status=CommandRun.STATUS.FAILED, release="44")
        wanted = CommandRunFactory.create(status=CommandRun.STATUS.FAILED, release="45")
        CommandRunFactory.create(release="45")

        result = _call("list_command_runs", {"status": "failed", "release": "45"})

        runs = result["structuredContent"]["runs"]
        assert [run["id"] for run in runs] == [str(wanted.uuid)]

    def test_git_sha_prefix_and_since(self) -> None:
        """A SHA prefix matches, and since drops earlier days."""
        old = timezone.now() - dt.timedelta(days=10)
        CommandRunFactory.create(git_sha="abcdef1234", started_at=old)
        recent = CommandRunFactory.create(git_sha="abcdef1234")

        since = (timezone.now() - dt.timedelta(days=1)).date().isoformat()
        result = ops_tools._handle_list_command_runs(
            {"git_sha": "abcdef1", "since": since}
        )

        assert [run["id"] for run in result["runs"]] == [str(recent.uuid)]

    def test_newest_first_and_limited(self) -> None:
        """Rows come newest first, capped by limit, with the full total."""
        now = timezone.now()
        runs = [
            CommandRunFactory.create(started_at=now - dt.timedelta(minutes=i))
            for i in range(3)
        ]

        result = ops_tools.list_command_runs(limit=2)

        assert result["total"] == 3
        assert [run["id"] for run in result["runs"]] == [
            str(runs[0].uuid),
            str(runs[1].uuid),
        ]

    @pytest.mark.parametrize(
        "arguments",
        [
            {"status": "BOGUS"},
            {"limit": 0},
            {"limit": True},
            {"git_sha": "abc"},
            {"command": 7},
            {"since": "yesterday"},
        ],
    )
    def test_bad_arguments_are_tool_errors(self, arguments: dict[str, Any]) -> None:
        """A bad argument is reported as a tool error, not a protocol one."""
        assert _call("list_command_runs", arguments)["isError"] is True

    def test_limit_is_clamped(self) -> None:
        """A limit over the ceiling is clamped rather than refused."""
        assert ops_tools._limit({"limit": 10_000}) == ops_tools.MAX_LIMIT


@pytest.mark.django_db
class TestGetCommandRun:
    """get_command_run: the full record, with children."""

    def test_full_record_with_children(self) -> None:
        """Output, traceback and the runs it started are returned."""
        parent = CommandRunFactory.create(
            output_tail="done\n", error="Traceback …", host="web-1"
        )
        child = CommandRunFactory.create(command="fetch_weather", parent=parent)

        result = _call("get_command_run", {"run_id": str(parent.uuid)})

        record = result["structuredContent"]
        assert record["output_tail"] == "done\n"
        assert record["error"] == "Traceback …"
        assert record["host"] == "web-1"
        assert [c["id"] for c in record["children"]] == [str(child.uuid)]
        assert record["parent_id"] is None

    def test_child_names_its_parent(self) -> None:
        """A nested run carries its parent's id."""
        parent = CommandRunFactory.create()
        child = CommandRunFactory.create(parent=parent)

        assert ops_tools.get_command_run(str(child.uuid))["parent_id"] == str(
            parent.uuid
        )

    @pytest.mark.parametrize("run_id", ["not-a-uuid", "", None])
    def test_bad_id(self, run_id: str | None) -> None:
        """A missing or malformed id is a tool error."""
        assert _call("get_command_run", {"run_id": run_id})["isError"] is True

    def test_unknown_id(self) -> None:
        """A well-formed id that names no run is a tool error."""
        result = _call(
            "get_command_run", {"run_id": str(CommandRunFactory.build().uuid)}
        )
        assert result["isError"] is True


@pytest.mark.django_db
class TestCommandHistory:
    """command_history: runs grouped by build."""

    @override_settings(RELEASE_VERSION="sha-current")
    def test_committed_on_current_build(self) -> None:
        """A successful commit run on the current SHA reads as committed."""
        now = timezone.now()
        CommandRunFactory.create(git_sha="sha-old", release="44")
        CommandRunFactory.create(
            git_sha="sha-current",
            mode=CommandRun.MODE.DRY_RUN,
            started_at=now - dt.timedelta(minutes=5),
            counts={"relinked": 3},
        )
        CommandRunFactory.create(git_sha="sha-current", counts={"relinked": 3})

        result = ops_tools.command_history("relink_bulletin_regions")

        assert result["committed_on_current_build"] is True
        current = next(b for b in result["builds"] if b["git_sha"] == "sha-current")
        assert current["runs"] == 2
        assert current["last_dry_run"]["counts"] == current["last_commit_run"]["counts"]

    @override_settings(RELEASE_VERSION="sha-current")
    def test_failed_commit_is_not_committed(self) -> None:
        """A failed commit run on the current build does not count."""
        CommandRunFactory.create(
            git_sha="sha-current", status=CommandRun.STATUS.FAILED, exit_code=1
        )

        result = ops_tools.command_history("relink_bulletin_regions")

        assert result["committed_on_current_build"] is False
        assert "no successful commit" in result["summary"]

    def test_never_run_is_a_tool_error(self) -> None:
        """A command with no rows is a tool error naming list_commands."""
        result = _call("command_history", {"command": "fetch_weather"})
        assert result["isError"] is True
        assert "list_commands" in result["content"][0]["text"]

    def test_command_is_required(self) -> None:
        """No command is a tool error."""
        assert _call("command_history", {})["isError"] is True


@pytest.mark.django_db
class TestBuildInfo:
    """build_info: identity, timing and migrations."""

    @override_settings(
        RELEASE_VERSION="abcdef1234567890",
        APP_RELEASE="45",
        APP_RELEASED_AT="2026-10-22T09:00:00+00:00",
        APP_BOOTED_AT="2026-10-22T09:05:00+00:00",
    )
    def test_reports_release_and_its_time(self) -> None:
        """The release, its timestamp and the boot time are all reported."""
        result = _call("build_info")["structuredContent"]

        assert result["release"] == "45"
        assert result["released_at"] == "2026-10-22T09:00:00+00:00"
        assert result["booted_at"] == "2026-10-22T09:05:00+00:00"
        assert result["unapplied_count"] == 0
        assert result["summary"].startswith("Release 45 (abcdef1) released")

    def test_reports_unapplied_migrations(self) -> None:
        """A pending migration is named."""
        with patch.object(
            ops_tools,
            "_migration_state",
            return_value={"unapplied": ["core.0010_x"], "unapplied_count": 1},
        ):
            result = ops_tools.build_info()

        assert result["unapplied"] == ["core.0010_x"]


@pytest.mark.django_db
class TestIngestStatus:
    """ingest_status: per-provider freshness and the fetch's health."""

    def test_per_provider_freshness(self) -> None:
        """Hours since issue is measured from the newest bulletin."""
        now = dt.datetime(2026, 10, 22, 12, tzinfo=UTC)
        BulletinFactory.create(
            source=Bulletin.Source.SLF, issued_at=now - dt.timedelta(hours=6)
        )
        PipelineRunFactory.create(status=PipelineRun.Status.FAILED, started_at=now)
        CommandRunFactory.create(command="fetch_bulletins")

        result = ops_tools.ingest_status(now=now)

        slf = next(p for p in result["providers"] if p["source"] == "SLF")
        assert slf["hours_since_issue"] == 6.0
        albina = next(p for p in result["providers"] if p["source"] == "ALBINA")
        assert albina["bulletins"] == 0
        assert albina["hours_since_issue"] is None
        assert result["last_fetch"]["command"] == "fetch_bulletins"
        assert result["pipeline_runs_24h"][PipelineRun.Status.FAILED] == 1
        assert result["summary"].startswith("Stalest provider SLF")

    def test_empty(self) -> None:
        """With nothing stored the summary says so."""
        result = ops_tools.ingest_status()
        assert result["summary"] == "No bulletins stored."
        assert result["last_fetch"] is None


@pytest.mark.django_db
class TestBulletinCoverage:
    """bulletin_coverage: rated versus expected regions per provider."""

    def test_missing_regions_are_named(self) -> None:
        """An unrated or no_rating region is missing; a rated one is not."""
        day = dt.date(2026, 1, 15)
        major = MajorRegionFactory.create(prefix="CH-4", country="CH")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        rated = MicroRegionFactory.create(region_id="CH-4111", subregion=sub)
        no_rating = MicroRegionFactory.create(region_id="CH-4112", subregion=sub)
        MicroRegionFactory.create(region_id="CH-4113", subregion=sub)
        RegionDayRatingFactory.create(region=rated, date=day)
        RegionDayRatingFactory.create(
            region=no_rating,
            date=day,
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
        )

        result = _call("bulletin_coverage", {"date": "2026-01-15"})["structuredContent"]

        slf = next(p for p in result["providers"] if p["source"] == "SLF")
        assert (slf["expected"], slf["rated"]) == (3, 1)
        assert slf["missing"] == ["CH-4112", "CH-4113"]
        assert "SLF 1/3 rated" in result["summary"]

    def test_full_coverage(self) -> None:
        """No regions means nothing is missing."""
        result = ops_tools.bulletin_coverage(dt.date(2026, 1, 15))
        assert "every provider" in result["summary"]


@pytest.mark.django_db
class TestTaskQueueHealth:
    """task_queue_health: the inline backends have no queue."""

    def test_inline_backend(self) -> None:
        """Under the test settings tasks run inline."""
        result = ops_tools.task_queue_health()
        assert "no queue" in result["summary"]
        assert "by_status" not in result

    def test_database_backend(self) -> None:
        """The database backend reports backlog and recent failures."""
        from django_tasks_db.models import DBTaskResult

        now = timezone.now()
        common = {
            "args_kwargs": {"args": [], "kwargs": {}},
            "backend_name": "default",
            "run_after": now,
            "exception_class_path": "",
            "traceback": "",
        }
        DBTaskResult.objects.create(status="READY", task_path="apps.x.send", **common)
        DBTaskResult.objects.create(
            status="FAILED",
            task_path="apps.x.send",
            finished_at=now,
            **{**common, "exception_class_path": "smtplib.SMTPException"},
        )
        tasks = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}
        with override_settings(TASKS=tasks):
            result = ops_tools.task_queue_health(now=now + dt.timedelta(seconds=30))

        assert result["by_status"] == {"READY": 1, "FAILED": 1}
        assert result["oldest_ready_age_seconds"] == 30
        assert result["recent_failures"][0]["exception_class_path"] == (
            "smtplib.SMTPException"
        )
        assert "1 failure(s)" in result["summary"]


@pytest.mark.django_db
class TestWeatherFreshness:
    """weather_freshness: today's row per active location."""

    def test_counts_missing(self) -> None:
        """An active location without today's row is missing."""
        today = timezone.localdate()
        fresh = ResortLocationFactory.create().location
        ResortLocationFactory.create()
        WeatherFactory.create(location=fresh, observed_on=today)

        result = ops_tools.weather_freshness(today=today)

        assert (result["active_locations"], result["with_today"]) == (2, 1)
        assert result["missing_today"] == 1


@pytest.mark.django_db
class TestLocationEstateHealth:
    """location_estate_health: the fill commands' outstanding work."""

    def test_gaps(self) -> None:
        """Missing elevation, missing address and orphans are counted."""
        LocationFactory.create(anonymous=True)
        LocationFactory.create(elevation_m=2000.0, what3words="a.b.c")

        result = _call("location_estate_health")["structuredContent"]

        assert result["total"] == 2
        assert result["missing_elevation"] == 1
        assert result["missing_what3words"] == 1
        assert result["orphaned"] == 1


@pytest.mark.django_db
class TestFeatureFlags:
    """feature_flags: live targeting and drift from the manifest."""

    def test_drift(self) -> None:
        """A rogue flag and a deleted manifest flag are both reported."""
        Flag.objects.filter(name="sync_log").delete()
        Flag.objects.create(name="rogue_flag", everyone=True, note="")

        result = _call("feature_flags")["structuredContent"]

        assert "rogue_flag" in result["not_in_manifest"]
        assert "sync_log" in result["missing_from_db"]
        rogue = next(f for f in result["flags"] if f["name"] == "rogue_flag")
        assert rogue["everyone"] is True


@pytest.mark.django_db
class TestDeployStepsStatus:
    """deploy_steps_status: the release's steps against the register."""

    @pytest.fixture(autouse=True)
    def _release_46(self, settings: Any) -> None:
        """Deploy release 46 for every test in the class."""
        settings.APP_RELEASE = "46"

    @pytest.fixture
    def steps_file(self, tmp_path: Path) -> Path:
        """A DEPLOY_STEPS file as bin/cut-release writes it for v46."""
        path = tmp_path / "DEPLOY_STEPS"
        path.write_text(
            "# Deploy steps for release v46: run each once after production "
            "deploys.\n"
            "# Written by bin/cut-release from the Deploy-Step: trailers.\n"
            "relink_bulletin_regions --commit\n"
            "fill_what3words --commit\n"
            "fetch_weather --commit\n"
            "backfill_resort_slugs --commit\n"
            "no_such_command --commit\n"
        )
        return path

    def test_each_state(self, steps_file: Path) -> None:
        """Done, failed, rehearsed, pending and unknown are told apart."""
        CommandRunFactory.create(release="46")
        CommandRunFactory.create(
            command="fill_what3words",
            release="46",
            status=CommandRun.STATUS.PARTIAL,
            exit_code=1,
        )
        CommandRunFactory.create(
            command="fetch_weather", release="46", mode=CommandRun.MODE.DRY_RUN
        )
        # Committed on the previous release: rehearsed, not done here.
        CommandRunFactory.create(command="backfill_resort_slugs", release="45")

        result = ops_tools.deploy_steps_status(steps_file)

        states = {entry["command"]: entry["state"] for entry in result["steps"]}
        assert states == {
            "relink_bulletin_regions": "DONE",
            "fill_what3words": "FAILED",
            "fetch_weather": "DRY_RUN_ONLY",
            "backfill_resort_slugs": "PENDING",
            "no_such_command": "UNKNOWN_COMMAND",
        }
        assert result["outstanding"] == 4
        assert result["summary"] == "Release 46: 1/5 deploy step(s) done."

    def test_a_later_success_outranks_an_earlier_failure(
        self, steps_file: Path
    ) -> None:
        """A retry that succeeded makes the step done."""
        now = timezone.now()
        CommandRunFactory.create(
            release="46",
            status=CommandRun.STATUS.FAILED,
            started_at=now - dt.timedelta(minutes=10),
        )
        ok = CommandRunFactory.create(release="46", started_at=now)

        result = ops_tools.deploy_steps_status(steps_file)

        relink = result["steps"][0]
        assert relink["state"] == "DONE"
        assert relink["run"]["id"] == str(ok.uuid)

    def test_no_file(self, tmp_path: Path) -> None:
        """A build without the file says so rather than reporting zero steps."""
        result = ops_tools.deploy_steps_status(tmp_path / "DEPLOY_STEPS")
        assert result["file_release"] is None
        assert "no DEPLOY_STEPS file" in result["summary"]

    def test_header_only_and_mismatched_release(self, tmp_path: Path) -> None:
        """A step-less file reads as none; a lagging file is called out."""
        path = tmp_path / "DEPLOY_STEPS"
        path.write_text("# Deploy steps for release v45: run each once.\n")

        result = ops_tools.deploy_steps_status(path)

        assert result["steps"] == []
        assert result["summary"] == (
            "Release 46 ships no deploy steps. The file names release 45, not 46."
        )

    def test_is_registered(self) -> None:
        """The tool is callable through the ops server."""
        result = _call("deploy_steps_status")
        assert result["isError"] is False
