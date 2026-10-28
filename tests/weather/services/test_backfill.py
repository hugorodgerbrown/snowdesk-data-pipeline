"""
tests/weather/services/test_backfill.py — The app's side of the weather backfill (SNOW-1275).

The backfill itself is the weather service's and is tested in
``tests/ingest/test_backfill.py``. Here: which stations a selection of
locations reaches, and that every row the service writes is applied into
``Weather`` — keeping a past day this instance already holds.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest import mock

import pytest

from apps.ingest.public import SourceLocation, SourceWeather
from apps.locations.models import Location
from apps.weather.models import Weather
from apps.weather.services import backfill
from apps.weather.services.reference import mirror_reference_locations
from tests.factories import (
    LocationFactory,
    ResortLocationFactory,
    SourceWeatherFactory,
    WeatherFactory,
)

pytestmark = pytest.mark.django_db


def _station() -> Location:
    """Return a curated location, mirrored, so the service holds its station."""
    location = ResortLocationFactory.create().location
    mirror_reference_locations()
    location.refresh_from_db()
    return location


def _response(days: list[datetime.date]) -> mock.Mock:
    """Return a mock ``requests`` response covering ``days``."""
    count = len(days)
    response = mock.Mock()
    response.json.return_value = {
        "daily": {
            "time": [d.isoformat() for d in days],
            "weather_code": [3] * count,
            "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
            "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
        }
    }
    response.raise_for_status.return_value = None
    return response


class TestStationsFor:
    """A location reaches the service through ``source_uuid``."""

    def test_a_mirrored_location_reaches_its_station(self) -> None:
        """The station carries the location's source uuid."""
        location = _station()
        stations = backfill.stations_for(Location.objects.filter(pk=location.pk))
        assert [s.uuid for s in stations] == [location.source_uuid]

    def test_a_users_point_and_a_tombstoned_station_reach_nothing(self) -> None:
        """Only live stations are backfilled."""
        point = LocationFactory.create()
        location = _station()
        SourceLocation.objects.update(deleted_at=datetime.datetime.now(tz=datetime.UTC))
        selection = Location.objects.filter(pk__in=[point.pk, location.pk])
        assert not backfill.stations_for(selection).exists()


class TestApply:
    """Each backfilled row lands in ``Weather``."""

    def test_the_admin_walk_applies_and_counts_what_it_cannot_reach(self) -> None:
        """A station's days reach Weather; a user's point is counted unheld."""
        location = _station()
        point = LocationFactory.create()
        day = backfill.backfill_until()
        with mock.patch("requests.get", return_value=_response([day])):
            counts = backfill.backfill_locations(
                [location, point], floor=day, until=day, commit=True, delay=0
            )
        assert Weather.objects.get(location=location).observed_on == day
        assert SourceWeather.objects.get().observed_on == day
        assert counts["unheld"] == 1
        assert counts["filled"] == 1

    def test_the_sweep_applies_each_row(self) -> None:
        """The hourly path writes both tables."""
        location = _station()
        day = backfill.backfill_until()
        with mock.patch("requests.get", return_value=_response([day])):
            backfill.sweep(
                Location.objects.filter(pk=location.pk),
                floor=day,
                until=day,
                commit=True,
                budget=None,
                delay=0,
            )
        assert Weather.objects.filter(location=location, observed_on=day).exists()

    def test_a_day_this_instance_already_holds_is_kept(self) -> None:
        """History from before the service is this instance's own account."""
        location = _station()
        day = backfill.backfill_until()
        held = WeatherFactory.create(location=location, observed_on=day, weather_code=0)
        with mock.patch("requests.get", return_value=_response([day])):
            counts = backfill.backfill_locations(
                [location], floor=day, until=day, commit=True, delay=0
            )
        held.refresh_from_db()
        assert held.weather_code == 0
        assert counts["failed"] == 0
        assert SourceWeather.objects.get().weather_code == 3

    def test_apply_backfilled_row_is_a_noop_for_a_place_not_synced(self) -> None:
        """A row whose station no location carries is skipped, not raised."""
        row: Any = SourceWeatherFactory.create()
        backfill.apply_backfilled_row(row)
        assert not Weather.objects.exists()
