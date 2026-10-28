"""
tests/weather/services/test_reference.py — The app's side of the weather service.

SNOW-1253: production mirrors its curated estate into ingest's place list,
and applies each service row into ``Weather`` through
``Location.source_uuid``. A favourite never reaches the service.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.ingest.public import SourceLocation
from apps.weather.exceptions import ImmutableWeatherRowError
from apps.weather.models import Weather
from apps.weather.services.reference import (
    apply_source_weather,
    mirror_reference_locations,
)
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    ResortLocationFactory,
    SourceLocationFactory,
    SourceWeatherFactory,
    WeatherFactory,
)

pytestmark = pytest.mark.django_db


class TestMirrorReferenceLocations:
    """Production's curated estate is the station list."""

    def test_a_public_location_becomes_a_station_under_its_own_uuid(self) -> None:
        """The location is linked to the station by its own uuid, coordinate exact."""
        link = ResortLocationFactory.create(
            location__name="Attelas",
            location__latitude=46.0987654,
            location__longitude=7.2512345,
            location__elevation_m=2727.0,
        )
        counts = mirror_reference_locations()

        location = link.location
        location.refresh_from_db()
        assert location.source_uuid == location.uuid
        station = SourceLocation.objects.get()
        assert station.uuid == location.uuid
        assert (station.latitude, station.longitude) == (46.0987654, 7.2512345)
        assert station.elevation_m == 2727.0
        assert station.name == "Attelas"
        assert counts["linked"] == 1
        assert counts["created"] == 1

    def test_a_favourite_never_becomes_a_station(self) -> None:
        """User data never leaves the instance."""
        favourite = FavouriteFactory.create()
        mirror_reference_locations()
        assert SourceLocation.objects.count() == 0
        location = favourite.location
        assert location is not None
        location.refresh_from_db()
        assert location.source_uuid is None

    def test_a_second_run_changes_nothing(self) -> None:
        """Linked once; afterwards the mirror is a no-op."""
        ResortLocationFactory.create()
        mirror_reference_locations()
        counts = mirror_reference_locations()
        assert counts["linked"] == 0
        assert counts["unchanged"] == 1

    def test_a_place_no_longer_curated_is_tombstoned(self) -> None:
        """Unlinking a resort's location tombstones its station."""
        link = ResortLocationFactory.create()
        mirror_reference_locations()
        link.delete()
        counts = mirror_reference_locations()
        assert counts["deleted"] == 1
        assert SourceLocation.objects.get().deleted_at is not None


class TestApplySourceWeather:
    """A service row lands in the Weather of the location carrying its uuid."""

    def test_the_row_is_copied_into_weather(self) -> None:
        """Every column is copied; the key is the location and the day."""
        station = SourceLocationFactory.create()
        location = LocationFactory.create(source_uuid=station.uuid)
        row = SourceWeatherFactory.create(
            location=station, weather_code=71, snowfall_sum=12.5, forecast=[{"d": 1}]
        )

        weather, created = apply_source_weather(row)  # type: ignore[misc]

        assert created
        assert weather.location == location
        assert weather.observed_on == row.observed_on
        assert weather.weather_code == 71
        assert weather.snowfall_sum == 12.5
        assert weather.forecast == [{"d": 1}]
        assert weather.fetched_at == row.fetched_at

    def test_todays_row_is_refined_on_a_second_apply(self) -> None:
        """The same day applied twice updates the one Weather row."""
        station = SourceLocationFactory.create()
        LocationFactory.create(source_uuid=station.uuid)
        row = SourceWeatherFactory.create(location=station)
        apply_source_weather(row)
        row.weather_code = 0
        row.save()
        _, created = apply_source_weather(row)  # type: ignore[misc]
        assert not created
        assert Weather.objects.get().weather_code == 0

    def test_a_place_this_instance_lacks_is_skipped(self) -> None:
        """No location carries the uuid yet: nothing is written."""
        assert apply_source_weather(SourceWeatherFactory.create()) is None
        assert Weather.objects.count() == 0

    def test_the_past_day_rule_still_holds(self) -> None:
        """Applying cannot rewrite a past Weather row."""
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        station = SourceLocationFactory.create()
        location = LocationFactory.create(source_uuid=station.uuid)
        WeatherFactory.create(location=location, observed_on=yesterday)
        row = SourceWeatherFactory.create(location=station, observed_on=yesterday)
        with pytest.raises(ImmutableWeatherRowError):
            apply_source_weather(row)
