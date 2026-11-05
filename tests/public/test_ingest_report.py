"""
tests/public/test_ingest_report.py — the staff ingest report (SNOW-1423).

Covers ``apps.public.ingest_report``: each feed's figures and state, the run
strip, the next run, the Changes list and ``red_feeds``. Every test pins
``now`` so the window is exact.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.test import override_settings

from apps.bulletins.models import Bulletin
from apps.core.models import CommandRun
from apps.ingest.public import SourceBulletin, SourceRestZone
from apps.public import ingest_report
from apps.public.ingest_report import build_report, parse_window, red_feeds
from tests.factories import (
    BulletinFactory,
    CommandRunFactory,
    ResortLocationFactory,
    SourceBulletinFactory,
    SourceRestZoneFactory,
    WeatherFactory,
)

NOW = dt.datetime(2026, 11, 5, 10, 25, tzinfo=dt.UTC)

#: Every command the report reads, and a recent start that keeps it green.
_COMMANDS = {
    "fetch_bulletins": dt.timedelta(minutes=18),
    "fetch_weather": dt.timedelta(hours=4),
    "backfill_weather": dt.timedelta(minutes=5),
    "mirror_reference_data": dt.timedelta(minutes=45),
    "import_ski_tours": dt.timedelta(days=3),
    "render_ski_tours": dt.timedelta(hours=5),
    "import_rest_zones": dt.timedelta(days=3),
}


def _run(command: str, ago: dt.timedelta, **fields: Any) -> CommandRun:
    """Create a scheduler run of ``command`` that started ``ago`` before NOW."""
    defaults: dict[str, Any] = {
        "command": command,
        "invoked_via": CommandRun.INVOKED_VIA.SCHEDULER,
        "started_at": NOW - ago,
        "counts": {},
    }
    return CommandRunFactory.create(**{**defaults, **fields})


def _all_green() -> None:
    """Create the runs and today's weather that keep every feed ok or off."""
    for command, ago in _COMMANDS.items():
        _run(command, ago)
    WeatherFactory.create(
        location=ResortLocationFactory.create().location,
        observed_on=NOW.date(),
        fetched_at=NOW - dt.timedelta(hours=1),
    )


def _rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the report's feed rows by key."""
    return {row["key"]: row for group in report["groups"] for row in group["feeds"]}


def _metrics(row: dict[str, Any]) -> dict[str, int]:
    """Return a row's metrics as a label → value dict."""
    return {metric["label"]: metric["value"] for metric in row["metrics"]}


def _stamp(model: Any, pk: int, *, created: dt.datetime, updated: dt.datetime) -> None:
    """Set an auto_now row's timestamps, which save() would overwrite."""
    model.objects.filter(pk=pk).update(created_at=created, updated_at=updated)


class TestParseWindow:
    """parse_window: the three windows, a default, and nothing else."""

    def test_default(self) -> None:
        """No value is the default window."""
        assert parse_window(None) == "24h"
        assert parse_window("") == "24h"

    def test_known(self) -> None:
        """Each window key is accepted as given."""
        assert [parse_window(key) for key in ("1h", "24h", "7d")] == ["1h", "24h", "7d"]

    def test_unknown(self) -> None:
        """Anything else raises."""
        with pytest.raises(ValueError, match="window must be one of"):
            parse_window("30d")


@pytest.mark.django_db
class TestShape:
    """The report's groups, order and summary."""

    def test_groups_and_order(self) -> None:
        """Feeds come grouped in the order FEEDS gives."""
        report = build_report("24h", now=NOW)
        assert [group["name"] for group in report["groups"]] == [
            "Bulletins",
            "Weather",
            "Reference data",
        ]
        assert list(_rows(report)) == [feed.key for feed in ingest_report.FEEDS]

    def test_all_green(self) -> None:
        """With every job recent and weather in, nothing is red."""
        _all_green()
        report = build_report("24h", now=NOW)
        assert report["red"] == []
        assert red_feeds(report) == []
        assert report["summary"] == "All feeds green."

    def test_never_run_is_red(self) -> None:
        """A job that has never run is red, and the summary names it."""
        report = build_report("24h", now=NOW)
        rows = _rows(report)
        assert rows["mirror"]["status"] == "red"
        assert rows["mirror"]["reason"] == "never run"
        assert "Estate mirror" in report["summary"]

    def test_only_scheduler_runs_count(self) -> None:
        """A shell run of a scheduled command is not the scheduler's."""
        _run(
            "mirror_reference_data",
            dt.timedelta(minutes=5),
            invoked_via=CommandRun.INVOKED_VIA.SHELL,
        )
        assert _rows(build_report("24h", now=NOW))["mirror"]["reason"] == "never run"


@pytest.mark.django_db
class TestBulletins:
    """Bulletin feeds: new vs changed, off-season, stale, per-provider failure."""

    def test_new_and_changed(self) -> None:
        """New is created in the window; changed was older and updated in it."""
        start = NOW - dt.timedelta(hours=24)
        new = SourceBulletinFactory.create(provider=SourceBulletin.Provider.SLF)
        old = SourceBulletinFactory.create(provider=SourceBulletin.Provider.SLF)
        idle = SourceBulletinFactory.create(provider=SourceBulletin.Provider.SLF)
        albina = SourceBulletinFactory.create(provider=SourceBulletin.Provider.ALBINA)
        hour = dt.timedelta(hours=1)
        _stamp(SourceBulletin, new.pk, created=NOW - hour, updated=NOW - hour)
        _stamp(SourceBulletin, old.pk, created=start - hour, updated=NOW - hour)
        _stamp(SourceBulletin, idle.pk, created=start - hour, updated=start - hour)
        _stamp(SourceBulletin, albina.pk, created=NOW - hour, updated=NOW - hour)

        rows = _rows(build_report("24h", now=NOW))

        assert _metrics(rows["slf"]) == {"new": 1, "changed": 1}
        assert _metrics(rows["albina"]) == {"new": 1, "changed": 0}

    def test_off_season(self) -> None:
        """No issue in a week is off, never red, even with no runs."""
        BulletinFactory.create(
            source=Bulletin.Source.SLF, issued_at=NOW - dt.timedelta(days=12)
        )
        report = build_report("24h", now=NOW)
        row = _rows(report)["slf"]
        assert row["status"] == "off"
        assert "slf" not in report["red"]
        assert row not in red_feeds(report)

    def test_stale_while_publishing(self) -> None:
        """Publishing this week, but no issue for two days, is red."""
        _all_green()
        BulletinFactory.create(
            source=Bulletin.Source.SLF, issued_at=NOW - dt.timedelta(days=2)
        )
        row = _rows(build_report("24h", now=NOW))["slf"]
        assert row["status"] == "red"
        assert row["reason"] == "last issue 2 d ago"

    def test_fresh_issue_is_ok(self) -> None:
        """An issue this morning is ok, with its time as the latest data."""
        _all_green()
        issued = NOW - dt.timedelta(hours=3)
        BulletinFactory.create(source=Bulletin.Source.SLF, issued_at=issued)
        row = _rows(build_report("24h", now=NOW))["slf"]
        assert row["status"] == "ok"
        assert row["latest_at"] == issued

    def test_provider_failure_is_that_providers_alone(self) -> None:
        """A run with failed SLF slots fails SLF, not ALBINA."""
        _all_green()
        for source in (Bulletin.Source.SLF, Bulletin.Source.ALBINA):
            BulletinFactory.create(source=source, issued_at=NOW - dt.timedelta(hours=3))
        _run(
            "fetch_bulletins",
            dt.timedelta(hours=2),
            counts={"slf_failed": 2, "albina_failed": 0},
        )

        rows = _rows(build_report("24h", now=NOW))

        assert rows["slf"]["status"] == "warn"
        assert rows["slf"]["reason"] == "1 failed run(s)"
        assert rows["albina"]["status"] == "ok"

    def test_latest_run_failed_is_red(self) -> None:
        """The newest fetch failing turns every publishing provider red."""
        _all_green()
        BulletinFactory.create(
            source=Bulletin.Source.SLF, issued_at=NOW - dt.timedelta(hours=3)
        )
        _run(
            "fetch_bulletins",
            dt.timedelta(minutes=1),
            status=CommandRun.STATUS.FAILED,
        )
        row = _rows(build_report("24h", now=NOW))["slf"]
        assert row["status"] == "red"
        assert row["reason"] == "last run failed"


@pytest.mark.django_db
class TestWeather:
    """Forecast counts the rows that landed; backfill what it queued."""

    def test_forecast_counts_rows_not_tasks(self) -> None:
        """Refreshed is places with a row fetched in the window, of all active."""
        _all_green()
        stale = ResortLocationFactory.create().location
        WeatherFactory.create(
            location=stale,
            observed_on=NOW.date(),
            fetched_at=NOW - dt.timedelta(days=2),
        )
        # The command claims to have queued many; the report ignores that.
        _run("fetch_weather", dt.timedelta(hours=3), counts={"enqueued": 633})

        row = _rows(build_report("24h", now=NOW))["forecast"]

        assert _metrics(row) == {"refreshed": 1, "places": 2}

    def test_place_without_today_is_red(self) -> None:
        """An active place with no row for today turns the forecast red."""
        _all_green()
        ResortLocationFactory.create()
        row = _rows(build_report("24h", now=NOW))["forecast"]
        assert row["status"] == "red"
        assert row["reason"] == "1 place(s) without today"

    def test_backfill_queued_and_failed(self) -> None:
        """Queued sums the window's enqueued; failed counts its task failures."""
        _all_green()
        _run("backfill_weather", dt.timedelta(hours=1), counts={"enqueued": 50})
        _run(
            "backfill_weather",
            dt.timedelta(hours=2),
            counts={"enqueued": 50, "weighted_calls": 120},
        )
        row = _rows(build_report("24h", now=NOW))["backfill"]
        assert _metrics(row) == {"queued": 100, "failed": 0}
        assert row["status"] == "ok"

    def test_backfill_failed_task_is_red(self) -> None:
        """A failed backfill task in the window turns the feed red."""
        from django_tasks_db.models import DBTaskResult

        _all_green()
        DBTaskResult.objects.create(
            status="FAILED",
            task_path=ingest_report.backfill_unit_task.module_path,
            args_kwargs={"args": [], "kwargs": {}},
            backend_name="default",
            run_after=NOW,
            finished_at=NOW - dt.timedelta(hours=1),
            exception_class_path="requests.HTTPError",
            traceback="",
        )
        tasks = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}
        with override_settings(TASKS=tasks):
            row = _rows(build_report("24h", now=NOW))["backfill"]
        assert _metrics(row)["failed"] == 1
        assert row["status"] == "red"
        assert row["reason"] == "1 failed task(s)"

    def test_backfill_stopped_is_red(self) -> None:
        """No backfill run for three hours is red."""
        for command, ago in _COMMANDS.items():
            if command != "backfill_weather":
                _run(command, ago)
        _run("backfill_weather", dt.timedelta(hours=3))
        row = _rows(build_report("24h", now=NOW))["backfill"]
        assert row["status"] == "red"
        assert row["reason"] == "last run 3 h ago"


@pytest.mark.django_db
class TestReferenceData:
    """The mirror and the imports, from their counts; rest zones from the table."""

    def test_mirror_sums_host_outcomes(self) -> None:
        """Created, updated and deleted add up across kinds; unchanged does not."""
        _run(
            "mirror_reference_data",
            dt.timedelta(minutes=45),
            counts={
                "resorts_created": 1,
                "resorts_updated": 2,
                "locations_updated": 3,
                "locations_unchanged": 630,
                "passes_deleted": 1,
            },
        )
        row = _rows(build_report("24h", now=NOW))["mirror"]
        assert _metrics(row) == {"new": 1, "changed": 5, "removed": 1}

    def test_ski_tours_ignore_service_counts(self) -> None:
        """The import's service_* counts are the service's copy, not this host's."""
        _run(
            "import_ski_tours",
            dt.timedelta(days=1),
            counts={"service_tours_created": 2900, "tours_created": 4, "failed": 0},
        )
        row = _rows(build_report("7d", now=NOW))["ski_tours"]
        assert _metrics(row) == {"new": 4, "changed": 0, "removed": 0}
        assert row["weekly"] is True

    def test_rest_zones_from_the_table(self) -> None:
        """Rest zones count ingest's rows created or updated in the window."""
        zone = SourceRestZoneFactory.create()
        hour = dt.timedelta(hours=1)
        _stamp(SourceRestZone, zone.pk, created=NOW - hour, updated=NOW - hour)
        row = _rows(build_report("24h", now=NOW))["rest_zones"]
        assert _metrics(row) == {"new": 1, "changed": 0}

    def test_weekly_import_stale_after_eight_days(self) -> None:
        """A weekly import last run nine days ago is red."""
        _run("import_rest_zones", dt.timedelta(days=9))
        row = _rows(build_report("24h", now=NOW))["rest_zones"]
        assert row["status"] == "red"
        assert row["reason"] == "last run 9 d ago"


@pytest.mark.django_db
class TestStrip:
    """The run strip buckets the window and keeps the worst run."""

    @pytest.mark.parametrize(("window", "cells"), [("1h", 12), ("24h", 24), ("7d", 42)])
    def test_cell_count(self, window: str, cells: int) -> None:
        """Each window has its own number of cells."""
        row = _rows(build_report(window, now=NOW))["mirror"]
        assert len(row["strip"]) == cells

    def test_bucketing_and_worst_wins(self) -> None:
        """Two runs in one hour show the failed one; empty hours are none."""
        _run("mirror_reference_data", dt.timedelta(minutes=50))
        failed = _run(
            "mirror_reference_data",
            dt.timedelta(minutes=40),
            status=CommandRun.STATUS.FAILED,
        )
        _run("mirror_reference_data", dt.timedelta(hours=5, minutes=30))

        strip = _rows(build_report("24h", now=NOW))["mirror"]["strip"]

        assert strip[23]["state"] == "failed"
        assert strip[23]["url"].endswith(f"/{failed.pk}/change/")
        assert strip[18]["state"] == "ok"
        assert strip[0] == {"state": "none", "url": ""}

    def test_running(self) -> None:
        """A run still open shows as running."""
        _run(
            "mirror_reference_data",
            dt.timedelta(minutes=2),
            status=CommandRun.STATUS.RUNNING,
            finished_at=None,
        )
        strip = _rows(build_report("1h", now=NOW))["mirror"]["strip"]
        assert strip[-1]["state"] == "running"


@pytest.mark.django_db
class TestNextRun:
    """Next run comes from the scheduler's own triggers."""

    def test_next_fetch(self) -> None:
        """From 10:25, the bulletin fetch next fires at 11:02."""
        row = _rows(build_report("24h", now=NOW))["slf"]
        assert row["next_run_at"] == dt.datetime(2026, 11, 5, 11, 2, tzinfo=dt.UTC)

    def test_weekly(self) -> None:
        """The ski-tour import next fires on Monday at 02:30."""
        row = _rows(build_report("24h", now=NOW))["ski_tours"]
        assert row["next_run_at"] == dt.datetime(2026, 11, 9, 2, 30, tzinfo=dt.UTC)

    def test_unknown_job(self) -> None:
        """A job id the scheduler lacks has no next run."""
        assert ingest_report.next_run("nope", NOW) is None


@pytest.mark.django_db
class TestChanges:
    """Only real changes and failures reach the Changes list."""

    def test_operational_counts_stay_out(self) -> None:
        """Non-zero counts that change nothing do not make a run a change.

        That includes an enqueue-only run's ``enqueued``: its tasks land the
        data, and listing every hourly "queued" would bury the real changes.
        """
        _run(
            "mirror_reference_data",
            dt.timedelta(minutes=45),
            counts={"locations_unchanged": 633, "resorts_linked": 0},
        )
        _run(
            "backfill_weather",
            dt.timedelta(minutes=5),
            counts={"enqueued": 50, "weighted_calls": 120, "already_queued": 3},
        )
        _run(
            "import_ski_tours",
            dt.timedelta(days=1),
            counts={"service_tours_created": 12, "fetched": 40},
        )
        _run(
            "fetch_bulletins",
            dt.timedelta(minutes=18),
            counts={"fetched": 44, "replayed": 3, "slf_created": 0},
        )
        assert build_report("7d", now=NOW)["changes"] == []

    def test_changes_and_failures_newest_first(self) -> None:
        """A change names its parts; a failure is flagged; newest comes first."""
        _run(
            "fetch_bulletins",
            dt.timedelta(hours=2),
            counts={"slf_created": 12, "slf_updated": 3},
        )
        _run(
            "mirror_reference_data",
            dt.timedelta(minutes=45),
            status=CommandRun.STATUS.FAILED,
        )

        changes = build_report("24h", now=NOW)["changes"]

        assert [(c["feed"], c["failed"], c["parts"]) for c in changes] == [
            ("Estate mirror", True, []),
            ("SLF", False, ["12 new", "3 changed"]),
        ]

    def test_a_failed_fetch_is_one_entry(self) -> None:
        """A fetch that failed outright is listed once, as Bulletins."""
        _run(
            "fetch_bulletins",
            dt.timedelta(hours=1),
            status=CommandRun.STATUS.FAILED,
        )
        changes = build_report("24h", now=NOW)["changes"]
        assert [(c["feed"], c["failed"]) for c in changes] == [("Bulletins", True)]

    def test_capped(self) -> None:
        """At most CHANGES_SHOWN entries."""
        for minute in range(25):
            _run(
                "mirror_reference_data",
                dt.timedelta(minutes=minute + 1),
                counts={"resorts_updated": 1},
            )
        changes = build_report("24h", now=NOW)["changes"]
        assert len(changes) == ingest_report.CHANGES_SHOWN


@pytest.mark.django_db
class TestAsJson:
    """as_json turns the report into JSON-safe values."""

    def test_datetimes_become_strings(self) -> None:
        """Datetimes are ISO strings after as_json."""
        report = ingest_report.as_json(build_report("24h", now=NOW))
        assert report["now"] == "2026-11-05T10:25:00Z"
