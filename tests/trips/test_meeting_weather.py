"""
tests/trips/test_meeting_weather.py — fetching a meeting point's forecast
when a trip is saved (SNOW-1217).

Covers ``apps.trips.services.meeting_weather``: one fetch for a trip whose
day is inside the forecast window, none for a day outside it or a point
that already has today's row — unless the point has just moved — and a
failed fetch logged rather than raised; plus the two save paths,
``create_trip`` and ``update_trip``, enqueueing it.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest
import requests
from django.utils import timezone

from apps.trips.services.meeting_weather import (
    _worker_fetch_meeting_weather,
    enqueue_meeting_weather,
)
from apps.trips.services.trips import create_trip, update_trip
from tests.factories import RouteFactory, TripFactory, WeatherFactory

_FETCH = "apps.trips.services.meeting_weather.fetch_weather_for_location"


def _days(n: int) -> datetime.date:
    """Return the day ``n`` days from today."""
    return timezone.localdate() + datetime.timedelta(days=n)


@pytest.mark.django_db
class TestEnqueue:
    """``enqueue_meeting_weather`` and its worker."""

    def test_a_trip_in_the_window_is_fetched_once(self) -> None:
        """Today's forecast, for the meeting point, committed."""
        trip = TripFactory.create(date=_days(3))

        with patch(_FETCH) as fetch:
            enqueue_meeting_weather(trip)

        fetch.assert_called_once()
        location, day = fetch.call_args.args
        assert location == trip.meeting_point
        assert day == timezone.localdate()
        assert fetch.call_args.kwargs == {"commit": True}

    @pytest.mark.parametrize("offset", [-1, 7, 30])
    def test_a_day_outside_the_window_is_not_fetched(self, offset: int) -> None:
        """Past days have nothing to forecast; far ones are the cron's."""
        trip = TripFactory.create(date=_days(offset))

        with patch(_FETCH) as fetch:
            enqueue_meeting_weather(trip)
            _worker_fetch_meeting_weather.call(trip.pk)

        fetch.assert_not_called()

    def test_a_point_with_todays_row_is_not_fetched_again(self) -> None:
        """An edit to the time must not spend a call."""
        trip = TripFactory.create(date=_days(1))
        WeatherFactory.create(location=trip.meeting_point)

        with patch(_FETCH) as fetch:
            enqueue_meeting_weather(trip)

        fetch.assert_not_called()

    def test_a_moved_point_is_fetched_again(self) -> None:
        """Today's row describes where the pin used to be."""
        trip = TripFactory.create(date=_days(1))
        WeatherFactory.create(location=trip.meeting_point)

        with patch(_FETCH) as fetch:
            enqueue_meeting_weather(trip, moved=True)

        fetch.assert_called_once()

    def test_a_failed_fetch_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The trip is saved either way; the scheduled run retries."""
        trip = TripFactory.create(date=_days(1))

        with patch(_FETCH, side_effect=requests.ConnectionError("refused")):
            enqueue_meeting_weather(trip)

        assert "meeting point weather fetch failed" in caplog.text

    def test_a_deleted_trip_is_skipped(self) -> None:
        """The queue can outlive the row."""
        trip = TripFactory.create(date=_days(1))
        pk = trip.pk
        trip.delete()

        with patch(_FETCH) as fetch:
            _worker_fetch_meeting_weather.call(pk)

        fetch.assert_not_called()


@pytest.mark.django_db
class TestSavePaths:
    """Both save paths ask for the forecast."""

    def _fetches(self, action: Any) -> list[Any]:
        """Run ``action`` with the fetch mocked; return its calls."""
        with patch(_FETCH) as fetch:
            action()
        return fetch.call_args_list

    def test_create_trip_fetches_the_new_meeting_point(self) -> None:
        """A trip saved at 09:00 has its forecast before the next run."""
        route = RouteFactory.create()
        calls = self._fetches(
            lambda: create_trip(
                route.user,
                route_uuid=route.uuid,
                date=_days(2),
                start_time=datetime.time(8, 0),
            )
        )

        assert len(calls) == 1

    def test_update_trip_refetches_only_a_moved_point(self) -> None:
        """A new time spends nothing; a new place does."""
        trip = TripFactory.create(date=_days(2))
        WeatherFactory.create(location=trip.meeting_point)
        point = trip.meeting_point

        def edit(latitude: float) -> None:
            update_trip(
                trip.created_by,
                trip.uuid,
                date=trip.date,
                start_time=datetime.time(9, 0),
                latitude=latitude,
                longitude=point.longitude,
            )

        assert self._fetches(lambda: edit(point.latitude)) == []
        assert len(self._fetches(lambda: edit(point.latitude + 0.01))) == 1
