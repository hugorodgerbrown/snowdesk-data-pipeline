"""
tests/weather/management/test_backfill_weather.py — Tests for backfill_weather.

Covers the command contract from CLAUDE.md's management-command rules: it
runs with no arguments, it writes nothing without ``--commit``, it streams
a countdown rather than materialising, and it exits non-zero when any
location failed. Plus the two flags that exist so the estate can be staged
in batches — ``--limit`` and ``--delay``.

The write rule itself is tested in tests/weather/services/test_backfill.py;
this file is about the command wrapper.
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any
from unittest import mock

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from apps.weather.models import Weather
from apps.weather.services import backfill
from tests.factories import LocationFactory, ResortLocationFactory, WeatherFactory


def _active_location(**kwargs: Any) -> Location:
    """Create a location the command's ``active()`` walk will reach.

    A bare ``LocationFactory`` row is reachable from nothing, so
    ``Location.objects.active()`` excludes it by design. Linking a resort is
    the cheapest way to put one in the walk.
    """
    location = LocationFactory.create(**kwargs)
    ResortLocationFactory.create(location=location)
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
        """The bare invocation probes the API and leaves the database alone."""
        _active_location(name="Verbier village", elevation_m=1494)
        out = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload([yesterday]))
        ) as get:
            call_command(
                "backfill_weather", "--floor", yesterday.isoformat(), stdout=out
            )

        assert get.called
        assert not Weather.objects.exists()
        assert "READ-ONLY" in out.getvalue()

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
        """Streaming output prints a line per location, so a long run reads as progress."""
        location = _active_location(name="Mont Fort")
        out = StringIO()

        with mock.patch("requests.get", return_value=_response(_payload([yesterday]))):
            call_command(
                "backfill_weather", "--floor", yesterday.isoformat(), stdout=out
            )

        assert f"{location.pk} " in out.getvalue()

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
            pytest.raises(CommandError, match="1 location failure"),
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
        """Nothing missing, nothing called — the walk is a diff, not a re-fetch."""
        location = _active_location()
        WeatherFactory.create(location=location, observed_on=yesterday)

        with mock.patch("requests.get") as get:
            call_command(
                "backfill_weather",
                "--commit",
                "--floor",
                yesterday.isoformat(),
                stdout=StringIO(),
            )

        get.assert_not_called()
