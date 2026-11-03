"""
tests/weather/management/test_backfill_weather.py — Tests for backfill_weather.

Covers the command contract from CLAUDE.md's management-command rules: it
runs with no arguments, it writes (and asks) nothing without ``--commit``,
it prints a countdown, and it exits non-zero when any unit failed. Since
SNOW-1399 it plans and enqueues one task per station-block; the tasks run
inline here. Plus the two flags that exist so the estate can be staged
in batches — ``--limit`` and ``--delay`` — and the SNOW-1245 additions:
the location and date filters, the weighted-call budget, and the throttle
exit.

The write rule itself is tested in tests/ingest/test_backfill.py, the apply
in tests/weather/services/test_backfill.py; this file is about the command
wrapper.
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any
from unittest import mock

import pytest
import requests
from django.core.management import call_command, load_command_class
from django.core.management.base import CommandError

from apps.core.command_tasks import backing_off
from apps.ingest.public import SourceLocation
from apps.locations.models import Location
from apps.locations.services.elevation import OPEN_METEO
from apps.weather.models import Weather
from apps.weather.services import backfill
from apps.weather.services.reference import mirror_reference_locations
from tests.factories import (
    LocationFactory,
    ResortLocationFactory,
    SourceWeatherFactory,
)


def _active_location(**kwargs: Any) -> Location:
    """Create a location the command's ``active()`` walk will reach.

    A bare ``LocationFactory`` row is reachable from nothing, so
    ``Location.objects.active()`` excludes it by design. Linking a resort is
    the cheapest way to put one in the walk; mirroring it gives it the
    weather service's station the sweep walks (SNOW-1275).
    """
    location = LocationFactory.create(**kwargs)
    ResortLocationFactory.create(location=location)
    mirror_reference_locations()
    location.refresh_from_db()
    return location


def _payload(days: list[datetime.date]) -> dict[str, Any]:
    """Return a minimal complete historical payload covering ``days``."""
    count = len(days)
    return {
        "daily": {
            "time": [d.isoformat() for d in days],
            "weather_code": [3] * count,
            "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
            "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
            "temperature_2m_max": [4.5] * count,
            "temperature_2m_min": [-2.0] * count,
        }
    }


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture()
def yesterday() -> datetime.date:
    """Return the last day the backfill may write."""
    return backfill.backfill_until()


@pytest.mark.django_db
class TestBackfillWeatherCommand:
    """The command wrapper around apps.weather.services.backfill."""

    def test_runs_with_no_arguments_and_writes_nothing(
        self, yesterday: datetime.date
    ) -> None:
        """The bare invocation plans; it asks nothing and writes nothing."""
        _active_location(name="Verbier village", elevation_m=1494)
        out = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload([yesterday]))
        ) as get:
            call_command(
                "backfill_weather", "--floor", yesterday.isoformat(), stdout=out
            )

        get.assert_not_called()
        assert not Weather.objects.exists()
        assert "READ-ONLY" in out.getvalue()
        assert "1 would be enqueued" in out.getvalue()

    def test_it_is_offered_to_the_ops_server(self) -> None:
        """It only enqueues, so run_command may start it."""
        command = load_command_class("apps.weather", "backfill_weather")
        assert getattr(command, "mcp_runnable") is True

    def test_commit_writes_one_row_per_missing_day(
        self, yesterday: datetime.date
    ) -> None:
        """With --commit the gap is filled."""
        location = _active_location()
        floor = yesterday - datetime.timedelta(days=1)
        days = [floor, yesterday]

        with mock.patch("requests.get", return_value=_response(_payload(days))):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                floor.isoformat(),
                stdout=StringIO(),
            )

        assert set(
            Weather.objects.filter(location=location).values_list(
                "observed_on", flat=True
            )
        ) == set(days)

    def test_the_countdown_names_each_location(self, yesterday: datetime.date) -> None:
        """The countdown names each station-block, so a run reads as progress."""
        _active_location(name="Mont Fort")
        out = StringIO()

        with mock.patch("requests.get", return_value=_response(_payload([yesterday]))):
            call_command(
                "backfill_weather", "--floor", yesterday.isoformat(), stdout=out
            )

        assert "1 unit(s) remaining" in out.getvalue()
        assert "Mont Fort" in out.getvalue()
        assert f"{yesterday} → {yesterday}" in out.getvalue()

    def test_limit_bounds_the_walk(self, yesterday: datetime.date) -> None:
        """--limit stops after N locations, so the estate can be staged."""
        for index in range(3):
            _active_location(name=f"Location {index}")

        with mock.patch(
            "requests.get", return_value=_response(_payload([yesterday]))
        ) as get:
            call_command(
                "backfill_weather",
                "--commit",
                "--limit",
                "2",
                "--delay",
                "0",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
            )

        assert get.call_count == 2
        assert Weather.objects.count() == 2

    def test_a_negative_delay_is_rejected(self) -> None:
        """--delay is a throttle, not an offset; a negative value is an error."""
        with pytest.raises(CommandError):
            call_command("backfill_weather", "--delay", "-1", stdout=StringIO())

    def test_exits_non_zero_when_a_location_fails(
        self, yesterday: datetime.date
    ) -> None:
        """A failed batch must be detectable by cron and CI."""
        _active_location()

        with (
            mock.patch("requests.get", side_effect=requests.HTTPError("boom")),
            pytest.raises(CommandError, match="1 failed station-block"),
        ):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
                stderr=StringIO(),
            )

    def test_a_complete_estate_issues_no_request(
        self, yesterday: datetime.date
    ) -> None:
        """Nothing missing, nothing called — the walk is a diff, not a re-fetch.

        Complete means the service holds the day (SNOW-1275): the diff is
        over ``SourceWeather``, the history the feed serves.
        """
        location = _active_location()
        SourceWeatherFactory.create(
            location=SourceLocation.objects.get(uuid=str(location.source_uuid)),
            observed_on=yesterday,
        )

        with mock.patch("requests.get") as get:
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
            )

        get.assert_not_called()


def _echo(url: str, params: dict[str, str], timeout: float) -> mock.Mock:
    """Answer a patched ``requests.get`` with a payload covering what it asked for."""
    start = datetime.date.fromisoformat(params["start_date"])
    end = datetime.date.fromisoformat(params["end_date"])
    span = (end - start).days + 1
    return _response(
        _payload([start + datetime.timedelta(days=d) for d in range(span)])
    )


@pytest.mark.django_db
class TestBackfillWeatherSelection:
    """--location, --resort and --until (SNOW-1245)."""

    def test_until_bounds_the_window(self, yesterday: datetime.date) -> None:
        """One day, on its own, for the onboarding fill."""
        location = _active_location()
        day = yesterday - datetime.timedelta(days=30)

        with mock.patch("requests.get", side_effect=_echo) as get:
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                day.isoformat(),
                "--until",
                day.isoformat(),
                stdout=StringIO(),
            )

        assert get.call_count == 1
        assert list(
            Weather.objects.filter(location=location).values_list(
                "observed_on", flat=True
            )
        ) == [day]

    def test_location_selects_by_id(self, yesterday: datetime.date) -> None:
        """Only the named location is requested."""
        wanted = _active_location()
        _active_location()

        with mock.patch("requests.get", side_effect=_echo):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                "--location",
                str(wanted.pk),
                stdout=StringIO(),
            )

        assert list(Weather.objects.values_list("location", flat=True)) == [wanted.pk]

    def test_resort_selects_its_locations(self, yesterday: datetime.date) -> None:
        """A resort's linked places are selected by its slug."""
        link = ResortLocationFactory.create()
        _active_location()

        with mock.patch("requests.get", side_effect=_echo):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                "--resort",
                link.resort.slug,
                stdout=StringIO(),
            )

        assert list(Weather.objects.values_list("location", flat=True)) == [
            link.location_id
        ]

    def test_an_unknown_resort_is_an_error(self) -> None:
        """A typo must not read as a finished, empty run."""
        _active_location()

        with pytest.raises(CommandError, match="no-such-resort"):
            call_command(
                "backfill_weather", "--resort", "no-such-resort", stdout=StringIO()
            )


@pytest.mark.django_db
class TestBackfillWeatherBudget:
    """The weighted-call budget and the throttle (SNOW-1245)."""

    def test_the_budget_stops_the_run(self, yesterday: datetime.date) -> None:
        """2.4 weighted calls buys one unit, and the summary says so."""
        for _ in range(3):
            _active_location()
        out = StringIO()

        with mock.patch("requests.get", side_effect=_echo) as get:
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                "--budget",
                "2.4",
                "--delay",
                "0",
                stdout=out,
            )

        assert get.call_count == 1
        assert "1 station-block(s) planned, 2.4 weighted call(s)" in out.getvalue()

    def test_the_default_budget_is_derived_from_the_estate(
        self, yesterday: datetime.date
    ) -> None:
        """No --budget means one hourly sweep's share of the free quota."""
        _active_location()
        out = StringIO()

        with mock.patch("requests.get", side_effect=_echo):
            call_command(
                "backfill_weather", "--floor", yesterday.isoformat(), stdout=out
            )

        expected = backfill.sweep_budget(Location.objects.active().count())
        assert f"budget {expected:.0f} weighted call(s)" in out.getvalue()

    def test_a_throttle_opens_the_breaker(self, yesterday: datetime.date) -> None:
        """A 429 fails its task and stops the rest asking; the run exits 1."""
        _active_location()
        _active_location()
        throttled = requests.HTTPError("429", response=mock.Mock(status_code=429))

        with (
            mock.patch("requests.get", side_effect=throttled) as get,
            pytest.raises(CommandError, match="1 failed station-block"),
        ):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
            )

        assert get.call_count == 1
        assert backing_off(OPEN_METEO)

    def test_an_outage_opens_the_breaker(self, yesterday: datetime.date) -> None:
        """A 5xx is treated as a 429: one unit asks, the rest wait."""
        _active_location()
        _active_location()
        down = requests.HTTPError("503", response=mock.Mock(status_code=503))

        with (
            mock.patch("requests.get", side_effect=down) as get,
            pytest.raises(CommandError, match="1 failed station-block"),
        ):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
            )

        assert get.call_count == 1
        assert backing_off(OPEN_METEO)

    def test_a_unit_waits_for_its_slot(self, yesterday: datetime.date) -> None:
        """The pace is the gap on the slot it shares with the live fetch."""
        _active_location()

        with (
            mock.patch("requests.get", side_effect=_echo),
            mock.patch("apps.weather.services.station_tasks.wait_for_slot") as wait,
        ):
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                "--delay",
                "0.75",
                stdout=StringIO(),
            )

        wait.assert_called_once_with(OPEN_METEO, 0.75)
