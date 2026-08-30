"""
tests/weather/management/test_fetch_weather.py — Tests for the fetch_weather command.

Covers the command contract from CLAUDE.md's management-command rules: it
runs with no arguments, it writes nothing without ``--commit``, it streams
rather than materialising, and it exits non-zero when any location failed.
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
from django.utils import timezone

from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    WeatherFactory,
)


def _payload(start: datetime.date, count: int = 2) -> dict[str, Any]:
    """Return a minimal complete Open-Meteo forecast payload."""
    days = [start + datetime.timedelta(days=offset) for offset in range(count)]
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


@pytest.mark.django_db
class TestFetchWeatherCommand:
    """Tests for the fetch_weather management command."""

    def test_runs_with_no_arguments_and_writes_nothing(self) -> None:
        """The bare invocation is a read-only probe — the command-design rule."""
        FavouriteFactory.create()
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ) as get:
            call_command("fetch_weather", stdout=stdout)

        assert get.call_count == 1
        assert Weather.objects.count() == 0
        assert "READ-ONLY" in stdout.getvalue()

    def test_commit_writes_one_row_per_active_location(self) -> None:
        """--commit persists exactly one row per active location."""
        FavouriteFactory.create()
        FavouriteFactory.create()
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=stdout)

        assert Weather.objects.count() == 2
        assert "2 row(s) created" in stdout.getvalue()

    def test_a_second_run_refines_rather_than_appending(self) -> None:
        """The four daily runs must not accumulate rows for the same day."""
        FavouriteFactory.create()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert Weather.objects.count() == 1

    def test_an_observation_only_location_is_never_fetched(self) -> None:
        """A field report must not mint a billable forecast call.

        The exclusion lives in ``Location.objects.active()``; this is the
        command-level assertion that it actually governs what gets fetched.
        """
        FieldObservationFactory.create()
        stdout = StringIO()

        with mock.patch("requests.get") as get:
            call_command("fetch_weather", "--commit", stdout=stdout)

        assert get.call_count == 0
        assert Weather.objects.count() == 0

    def test_exits_non_zero_when_a_location_failed(self) -> None:
        """A partially failed batch must be visible to cron and CI."""
        FavouriteFactory.create()

        with (
            mock.patch("requests.get", side_effect=requests.HTTPError("boom")),
            pytest.raises(CommandError, match="1 location failure"),
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

    def test_prints_a_countdown_line_per_location(self) -> None:
        """Streamed output reads as a countdown, per the SNOW-602 rule."""
        location = LocationFactory.create(name="Mont Fort")
        FavouriteFactory.create(location=location)
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=stdout)

        assert f"{location.pk} " in stdout.getvalue()

    def test_a_past_row_is_left_alone(self) -> None:
        """The command writes today; yesterday's account is not touched.

        Today's row is created alongside it rather than the past one being
        rewritten, which is the whole point of the immutability rule.
        """
        location = LocationFactory.create()
        FavouriteFactory.create(location=location)
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        past = WeatherFactory.create(
            location=location, observed_on=yesterday, weather_code=3
        )

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

        past.refresh_from_db()
        assert past.weather_code == 3
        assert Weather.objects.for_location(location).count() == 2
