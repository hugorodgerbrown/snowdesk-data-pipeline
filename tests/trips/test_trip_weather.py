"""
tests/trips/test_trip_weather.py — the forecast for a trip's day (SNOW-1217).

Covers ``apps.trips.services.trip_weather``: the Plan page's nearest public
forecast point to a route's start (never a private one, never one too far,
never one with no row today, named the way its forecast page is), and the
trip page's meeting point forecast — today's row while the trip is ahead,
the row of the day itself once it has passed.
"""

from __future__ import annotations

import datetime

import pytest

from apps.trips.services.trip_weather import (
    NEAREST_RADIUS_KM,
    meeting_point_weather,
    nearest_weather,
)
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    TripFactory,
    WeatherFactory,
    forecast_days,
)

TODAY = datetime.date(2026, 10, 24)
START = (46.10, 7.20)


def _public_point(latitude: float, longitude: float, name: str = "Verbier") -> object:
    """A named resort location with today's forecast."""
    location = LocationFactory.create(
        name=name, latitude=latitude, longitude=longitude, elevation_m=1520.0
    )
    ResortLocationFactory.create(location=location)
    WeatherFactory.create(
        location=location, observed_on=TODAY, forecast=forecast_days(TODAY, [73, 0])
    )
    return location


@pytest.mark.django_db
class TestNearestWeather:
    """``nearest_weather`` — the Plan page's forecast point."""

    def test_the_nearest_public_point_wins(self) -> None:
        """Two candidates: the closer is read, with its distance and week."""
        near = _public_point(46.11, 7.20, "Near")
        _public_point(46.15, 7.20, "Far")

        weather = nearest_weather(*START, TODAY)

        assert weather is not None
        assert weather.location == near
        assert weather.label == "Near"
        assert weather.distance_km == pytest.approx(1.11, abs=0.01)
        assert weather.elevation_m == 1520
        assert sorted(weather.days) == [
            TODAY,
            TODAY + datetime.timedelta(days=1),
            TODAY + datetime.timedelta(days=2),
        ]
        tomorrow = weather.on(TODAY + datetime.timedelta(days=1))
        assert tomorrow is not None
        assert tomorrow["condition_label"] == "Snow"
        assert weather.on(TODAY + datetime.timedelta(days=9)) is None
        assert "Near" in str(weather)

    def test_a_point_beyond_the_radius_is_no_answer(self) -> None:
        """A forecast from the next valley does not stand for this one."""
        degrees = (NEAREST_RADIUS_KM + 2) / 111.0
        _public_point(START[0] + degrees, START[1])

        assert nearest_weather(*START, TODAY) is None

    def test_a_private_pin_is_never_borrowed(self) -> None:
        """A favourite's forecast would name somebody's private place."""
        favourite = FavouriteFactory.create(
            location=LocationFactory.create(latitude=46.101, longitude=7.20)
        )
        WeatherFactory.create(location=favourite.location, observed_on=TODAY)

        assert nearest_weather(*START, TODAY) is None

    def test_a_point_with_no_row_today_is_skipped(self) -> None:
        """Yesterday's forecast is not today's."""
        location = LocationFactory.create(latitude=46.101, longitude=7.20)
        ResortLocationFactory.create(location=location)
        WeatherFactory.create(
            location=location, observed_on=TODAY - datetime.timedelta(days=1)
        )

        assert nearest_weather(*START, TODAY) is None

    def test_an_unnamed_point_is_named_by_its_resort_or_region(self) -> None:
        """Most public points are unnamed; the forecast page's precedence."""
        pinned = LocationFactory.create(
            name="", latitude=46.101, longitude=7.20, elevation_m=None
        )
        ResortFactory.create(name="Bruson", location=pinned)
        WeatherFactory.create(location=pinned, observed_on=TODAY)

        weather = nearest_weather(*START, TODAY)

        assert weather is not None
        assert weather.label == "Bruson"
        assert weather.elevation_m is None

        centroid = LocationFactory.create(name="", latitude=46.1001, longitude=7.20)
        MicroRegionFactory.create(name="Martigny-Verbier", centroid_location=centroid)
        WeatherFactory.create(location=centroid, observed_on=TODAY)

        weather = nearest_weather(*START, TODAY)

        assert weather is not None
        assert weather.label == "Martigny-Verbier"

    def test_a_point_linked_to_a_resort_is_named_after_it(self) -> None:
        """A resort's mid-station with no name of its own."""
        location = LocationFactory.create(name="", latitude=46.101, longitude=7.20)
        ResortLocationFactory.create(
            location=location, resort=ResortFactory.create(name="Nendaz")
        )
        WeatherFactory.create(location=location, observed_on=TODAY)

        weather = nearest_weather(*START, TODAY)

        assert weather is not None
        assert weather.label == "Nendaz"


@pytest.mark.django_db
class TestMeetingPointWeather:
    """``meeting_point_weather`` — the trip page's forecast."""

    def test_an_upcoming_trip_reads_todays_row(self) -> None:
        """The newest forecast, carrying the trip's day in its window."""
        trip = TripFactory.create(date=TODAY + datetime.timedelta(days=2))
        WeatherFactory.create(
            location=trip.meeting_point,
            observed_on=TODAY - datetime.timedelta(days=1),
            forecast=forecast_days(TODAY - datetime.timedelta(days=1), [0, 0, 0]),
        )
        WeatherFactory.create(
            location=trip.meeting_point,
            observed_on=TODAY,
            forecast=forecast_days(TODAY, [61, 73], snow=[0.0, 8.0]),
        )

        weather = meeting_point_weather(trip, TODAY)

        assert weather is not None
        assert weather.distance_km is None
        assert weather.label == ""
        day = weather.on(trip.date)
        assert day is not None
        assert day["snowfall_sum"] == 8.0
        assert "meeting point" in str(weather)

    def test_a_past_trip_reads_the_row_of_its_own_day(self) -> None:
        """What the forecast said that morning, never rewritten."""
        day = TODAY - datetime.timedelta(days=3)
        trip = TripFactory.create(date=day)
        WeatherFactory.create(
            location=trip.meeting_point, observed_on=day, temperature_2m_max=-1.0
        )
        WeatherFactory.create(
            location=trip.meeting_point,
            observed_on=TODAY,
            temperature_2m_max=9.0,
        )

        weather = meeting_point_weather(trip, TODAY)

        assert weather is not None
        that_day = weather.on(day)
        assert that_day is not None
        assert that_day["temp_max"] == -1.0

    def test_nothing_fetched_yet_is_none(self) -> None:
        """Until the first fetch lands, the page has no line."""
        trip = TripFactory.create(date=TODAY)

        assert meeting_point_weather(trip, TODAY) is None
