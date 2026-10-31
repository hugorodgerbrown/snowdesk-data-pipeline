"""
tests/trips/test_trip_weather.py — the forecast for a trip's day (SNOW-1217).

Covers ``apps.trips.services.trip_weather``: the Plan page's nearest public
forecast point to a route's start (never a private one, never one with no
row today, at any distance, named the way its forecast page is), and the
trip page's forecast, borrowed from the curated point nearest the meeting
point (Hugo, 2026-10-28) — today's row while the trip is ahead, the row of
the day itself once it has passed.
"""

from __future__ import annotations

import datetime

import pytest

from apps.locations.models import Location
from apps.trips.services.trip_weather import (
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


MEETING = (46.1, 7.4)  # TripFactory's meeting point.


def _public_point(
    latitude: float,
    longitude: float,
    name: str = "Verbier",
    observed_on: datetime.date = TODAY,
    **weather: object,
) -> Location:
    """A named resort location with a forecast row for ``observed_on``."""
    location = LocationFactory.create(
        name=name, latitude=latitude, longitude=longitude, elevation_m=1520.0
    )
    ResortLocationFactory.create(location=location)
    weather.setdefault("forecast", forecast_days(observed_on, [73, 0]))
    WeatherFactory.create(location=location, observed_on=observed_on, **weather)
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

    def test_a_distant_point_is_still_borrowed(self) -> None:
        """No distance cap (Hugo, 2026-10-28): the page says how far it is.

        Until then a point past 15 km was no answer, and a remote start's
        tiles had no forecast at all.
        """
        _public_point(START[0] + 0.5, START[1], "Arolla")

        weather = nearest_weather(*START, TODAY)

        assert weather is not None
        assert weather.label == "Arolla"
        assert weather.distance_km == pytest.approx(55.6, abs=0.1)
        assert not weather.is_same_place

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
    """``meeting_point_weather`` — the trip page's borrowed forecast."""

    def test_an_upcoming_trip_borrows_the_nearest_points_row(self) -> None:
        """Today's row from the nearest curated point, carrying the trip's day."""
        trip = TripFactory.create(date=TODAY + datetime.timedelta(days=2))
        near = _public_point(
            MEETING[0] + 0.01,
            MEETING[1],
            "Near",
            forecast=forecast_days(TODAY, [61, 73], snow=[0.0, 8.0]),
        )
        _public_point(MEETING[0] + 0.05, MEETING[1], "Far")

        weather = meeting_point_weather(trip, TODAY)

        assert weather is not None
        assert weather.location == near
        assert weather.label == "Near"
        assert weather.distance_km == pytest.approx(1.11, abs=0.01)
        day = weather.on(trip.date)
        assert day is not None
        assert day["snowfall_sum"] == 8.0
        assert "Near" in str(weather)

    def test_the_meeting_point_itself_is_never_read(self) -> None:
        """A meeting point's own rows, fetched before 2026-10-28, are ignored."""
        trip = TripFactory.create(date=TODAY)
        WeatherFactory.create(location=trip.meeting_point, observed_on=TODAY)

        assert meeting_point_weather(trip, TODAY) is None

    def test_the_meeting_time_is_read_on_the_forecast_points_clock(self) -> None:
        """06:30 at a UTC+2 point is before a 07:40 sunrise: a night icon.

        Read in the site's zone (UTC) it would be 08:30 local, after
        sunrise, and the clear sky would wrongly draw the sun.
        """
        day = TODAY + datetime.timedelta(days=1)
        early = TripFactory.create(date=day, start_time=datetime.time(6, 30))
        late = TripFactory.create(date=day, start_time=datetime.time(9, 0))
        _public_point(*MEETING, forecast=forecast_days(TODAY, [0, 0]))

        before = meeting_point_weather(early, TODAY)
        after = meeting_point_weather(late, TODAY)

        assert before is not None and after is not None
        assert before.is_same_place
        assert before.days[day]["icon_filename"] == "clear-night.svg"
        assert after.days[day]["icon_filename"] == "clear-day.svg"

    def test_a_stored_icon_bucket_wins_and_takes_the_meeting_times_variant(
        self,
    ) -> None:
        """A day's stored bucket is drawn, in the meeting time's day/night variant.

        The row says cloudy (code 3) but its stored bucket says clear, so the
        bucket written at fetch time is what the trip draws (SNOW-1343). The
        legacy case, an entry with no ``icon_bucket``, is the test above.
        """
        day = TODAY + datetime.timedelta(days=1)
        early = TripFactory.create(date=day, start_time=datetime.time(6, 30))
        late = TripFactory.create(date=day, start_time=datetime.time(9, 0))
        forecast = forecast_days(TODAY, [3, 3])
        for entry in forecast:
            entry["icon_bucket"] = "clear"
        _public_point(*MEETING, forecast=forecast)

        before = meeting_point_weather(early, TODAY)
        after = meeting_point_weather(late, TODAY)

        assert before is not None and after is not None
        assert before.days[day]["icon_filename"] == "clear-night.svg"
        assert after.days[day]["icon_filename"] == "clear-day.svg"

    def test_a_past_trip_reads_the_row_of_its_own_day(self) -> None:
        """What the forecast said that morning, never rewritten."""
        day = TODAY - datetime.timedelta(days=3)
        trip = TripFactory.create(date=day)
        point = _public_point(
            *MEETING, observed_on=day, forecast=None, temperature_2m_max=-1.0
        )
        WeatherFactory.create(location=point, observed_on=TODAY, temperature_2m_max=9.0)

        weather = meeting_point_weather(trip, TODAY)

        assert weather is not None
        that_day = weather.on(day)
        assert that_day is not None
        assert that_day["temp_max"] == -1.0

    def test_no_curated_row_is_none(self) -> None:
        """Before any curated point has a row for the day, the page has no line."""
        trip = TripFactory.create(date=TODAY)

        assert meeting_point_weather(trip, TODAY) is None
