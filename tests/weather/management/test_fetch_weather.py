"""
tests/weather/management/test_fetch_weather.py — Tests for the fetch_weather command.

Covers the command contract from CLAUDE.md's management-command rules: it
runs with no arguments, it writes nothing (and asks nothing) without
``--commit``, it streams rather than materialising, and it exits non-zero
when any location failed. Since SNOW-1399 it enqueues one task per station;
the tasks run inline here, and a 429 opens the Open-Meteo breaker.
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

from apps.core.command_tasks import backing_off
from apps.ingest.public import SourceLocation, SourceWeather
from apps.locations.services.elevation import OPEN_METEO
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    ResortLocationFactory,
    TripFactory,
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
        """The bare invocation counts — it asks nothing and writes nothing."""
        ResortLocationFactory.create()
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ) as get:
            call_command("fetch_weather", stdout=stdout)

        assert get.call_count == 0
        assert Weather.objects.count() == 0
        assert "READ-ONLY" in stdout.getvalue()
        assert "1 station(s) would be enqueued" in stdout.getvalue()

    def test_commit_writes_one_row_per_day_per_active_location(self) -> None:
        """--commit persists one row per day of the window per location."""
        ResortLocationFactory.create()
        ResortLocationFactory.create()
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=stdout)

        # Two locations, a two-day payload: four rows (SNOW-1337).
        assert Weather.objects.count() == 4
        assert "2 station(s) enqueued" in stdout.getvalue()

    def test_a_second_run_refines_rather_than_appending(self) -> None:
        """The four daily runs must not accumulate rows for the same day."""
        ResortLocationFactory.create()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert Weather.objects.count() == 2

    def test_a_favourite_or_a_trip_is_never_fetched(self) -> None:
        """Only Snowdesk decides where a forecast is fetched (2026-10-28).

        A saved pin and a trip's meeting point borrow the nearest curated
        forecast; neither buys a call of its own.
        """
        FavouriteFactory.create()
        TripFactory.create(date=timezone.localdate())

        with mock.patch("requests.get") as get:
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert get.call_count == 0

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
        ResortLocationFactory.create()

        with (
            mock.patch("requests.get", side_effect=requests.HTTPError("boom")),
            pytest.raises(CommandError, match="1 station failure"),
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

    def test_a_429_opens_the_breaker_for_the_rest(self) -> None:
        """The next station's task returns without asking; the run exits 1."""
        ResortLocationFactory.create()
        ResortLocationFactory.create()
        refused = requests.HTTPError("429", response=mock.Mock(status_code=429))

        with (
            mock.patch("requests.get", side_effect=refused) as get,
            pytest.raises(CommandError, match="1 station failure"),
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert get.call_count == 1
        assert backing_off(OPEN_METEO)

    @pytest.mark.parametrize(
        "outage",
        [
            requests.Timeout("slow"),
            requests.ConnectionError("refused"),
            requests.HTTPError("503", response=mock.Mock(status_code=503)),
        ],
        ids=["timeout", "connection", "5xx"],
    )
    def test_an_outage_opens_the_breaker_for_the_rest(self, outage: Exception) -> None:
        """Open-Meteo down: one station asks, the rest wait for the next run."""
        ResortLocationFactory.create()
        ResortLocationFactory.create()

        with (
            mock.patch("requests.get", side_effect=outage) as get,
            pytest.raises(CommandError, match="1 station failure"),
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert get.call_count == 1
        assert backing_off(OPEN_METEO)

    def test_a_4xx_fails_its_station_alone(self) -> None:
        """A refused input leaves the breaker shut; every station still asks."""
        ResortLocationFactory.create()
        ResortLocationFactory.create()
        refused = requests.HTTPError("400", response=mock.Mock(status_code=400))

        with (
            mock.patch("requests.get", side_effect=refused) as get,
            pytest.raises(CommandError, match="2 station failure"),
        ):
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert get.call_count == 2
        assert not backing_off(OPEN_METEO)

    def test_a_station_waits_for_its_slot(self) -> None:
        """The pace is the gap on the slot it shares with the backfill."""
        ResortLocationFactory.create()

        with (
            mock.patch(
                "requests.get",
                return_value=_response(_payload(timezone.localdate())),
            ),
            mock.patch("apps.weather.services.station_tasks.wait_for_slot") as wait,
        ):
            call_command(
                "fetch_weather", "--commit", "--delay", "0.25", stdout=StringIO()
            )

        wait.assert_called_once_with(OPEN_METEO, 0.25)

    def test_prints_a_countdown_line_per_location(self) -> None:
        """Streamed output reads as a countdown, per the SNOW-602 rule."""
        location = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=location)
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", "--commit", stdout=stdout)

        assert f"{location.pk} " in stdout.getvalue()

    def test_a_past_row_is_left_alone(self) -> None:
        """The command writes today onwards; yesterday's account is not touched.

        Today's week is created alongside it rather than the past one being
        rewritten, which is the whole point of the immutability rule.
        """
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
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
        assert Weather.objects.for_location(location).count() == 3


@pytest.mark.django_db
class TestReferencePlacesGoThroughTheService:
    """SNOW-1253: a curated place is fetched by the service and applied here."""

    def test_commit_mirrors_fetches_and_applies(self) -> None:
        """The place becomes a station, the service stores its day, Weather gets it."""
        link = ResortLocationFactory.create(location__name="Attelas")
        stdout = StringIO()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ) as get:
            call_command("fetch_weather", "--commit", stdout=stdout)

        assert get.call_count == 1
        station = SourceLocation.objects.get()
        assert station.uuid == link.location.uuid
        assert set(SourceWeather.objects.values_list("location", flat=True)) == {
            station.pk
        }
        assert SourceWeather.objects.count() == 2
        assert set(Weather.objects.values_list("location", flat=True)) == {
            link.location.pk
        }
        assert Weather.objects.count() == 2
        output = stdout.getvalue()
        assert "1 station(s)" in output
        assert "Stations: 1 created" in output

    def test_a_favourite_is_never_fetched(self) -> None:
        """User points borrow a forecast (SNOW-1265); the service never sees them."""
        ResortLocationFactory.create()
        FavouriteFactory.create()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ) as get:
            call_command("fetch_weather", "--commit", stdout=StringIO())

        assert get.call_count == 1
        assert SourceLocation.objects.count() == 1
        assert Weather.objects.count() == 2

    def test_read_only_mirrors_nothing(self) -> None:
        """Without --commit the station list is left as it is."""
        ResortLocationFactory.create()

        with mock.patch(
            "requests.get", return_value=_response(_payload(timezone.localdate()))
        ):
            call_command("fetch_weather", stdout=StringIO())

        assert SourceLocation.objects.count() == 0
        assert SourceWeather.objects.count() == 0
        assert Weather.objects.count() == 0
