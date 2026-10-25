"""
tests/locations/services/test_prime.py — a location's height and forecast on save (SNOW-1226).

Covers ``apps.locations.services.prime``: the height is resolved when it is
missing and before the forecast, so the forecast is downscaled to it; today's
forecast is fetched only for an active location without today's row, unless
the pin has just moved; both upstream failures are logged rather than raised;
and the enqueue waits for the transaction to commit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.utils import timezone

from apps.locations.models import Location
from apps.locations.services.prime import (
    _worker_prime_location,
    enqueue_location_prime,
)
from tests.factories import LocationFactory, ResortLocationFactory, WeatherFactory

_ELEVATION = "apps.locations.services.prime.fetch_elevation"
_FETCH = "apps.locations.services.prime.fetch_weather_for_location"


def _linked(**kwargs: Any) -> Location:
    """Return a curated location linked to a resort, so it is active."""
    location: Location = LocationFactory.create(**kwargs)
    ResortLocationFactory.create(location=location)
    return location


@pytest.mark.django_db
class TestWorker:
    """``_worker_prime_location`` — the decisions, made when the task runs."""

    def test_height_then_forecast_for_a_new_point(self) -> None:
        """The forecast sees the height the same task just resolved."""
        location = _linked()
        seen: list[float | None] = []

        def fetch(loc: Location, day: Any, *, commit: bool) -> None:
            seen.append(loc.elevation_m)

        with (
            patch(_ELEVATION, return_value=2970.0) as elevation,
            patch(_FETCH, side_effect=fetch) as forecast,
        ):
            _worker_prime_location.call(location.pk)

        elevation.assert_called_once_with(location.latitude, location.longitude)
        location.refresh_from_db()
        assert location.elevation_m == 2970.0
        forecast.assert_called_once()
        assert forecast.call_args.args[1] == timezone.localdate()
        assert forecast.call_args.kwargs == {"commit": True}
        assert seen == [2970.0]

    def test_a_pin_moved_during_the_lookup_is_left_to_the_newer_task(
        self,
    ) -> None:
        """The old coordinate's height must not land on the new spot."""
        location = _linked()

        def move_then_answer(latitude: float, longitude: float) -> float:
            Location.objects.filter(pk=location.pk).update(latitude=46.3)
            return 2970.0

        with (
            patch(_ELEVATION, side_effect=move_then_answer),
            patch(_FETCH) as forecast,
        ):
            _worker_prime_location.call(location.pk)

        location.refresh_from_db()
        assert location.elevation_m is None
        forecast.assert_not_called()

    def test_a_known_height_is_not_looked_up_again(self) -> None:
        """A rename must not spend an elevation call."""
        location = _linked(resolved=True)

        with patch(_ELEVATION) as elevation, patch(_FETCH):
            _worker_prime_location.call(location.pk)

        elevation.assert_not_called()

    def test_a_point_with_todays_row_is_not_fetched_again(self) -> None:
        """Nothing missing, nothing spent."""
        location = _linked(resolved=True)
        WeatherFactory.create(location=location)

        with patch(_FETCH) as forecast:
            _worker_prime_location.call(location.pk)

        forecast.assert_not_called()

    def test_a_moved_point_is_fetched_again(self) -> None:
        """Today's row describes where the pin used to be."""
        location = _linked(resolved=True)
        WeatherFactory.create(location=location)

        with patch(_FETCH) as forecast:
            _worker_prime_location.call(location.pk, True)

        forecast.assert_called_once()

    def test_an_inactive_location_gets_its_height_but_no_forecast(self) -> None:
        """The forecast is only for what the scheduled run would pay for."""
        location = LocationFactory.create()

        with (
            patch(_ELEVATION, return_value=1200.0),
            patch(_FETCH) as forecast,
        ):
            _worker_prime_location.call(location.pk)

        location.refresh_from_db()
        assert location.elevation_m == 1200.0
        forecast.assert_not_called()

    def test_a_failed_height_still_fetches_the_forecast(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The cell's forecast beats none; the height waits for a retry."""
        location = _linked()

        with (
            patch(_ELEVATION, side_effect=requests.ConnectionError("refused")),
            patch(_FETCH) as forecast,
        ):
            _worker_prime_location.call(location.pk)

        assert "location elevation lookup failed" in caplog.text
        location.refresh_from_db()
        assert location.elevation_m is None
        forecast.assert_called_once()

    def test_a_failed_forecast_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The save stands either way; the scheduled run retries."""
        location = _linked(resolved=True)

        with patch(_FETCH, side_effect=requests.ConnectionError("refused")):
            _worker_prime_location.call(location.pk)

        assert "location weather fetch failed" in caplog.text

    def test_a_deleted_location_is_skipped(self) -> None:
        """The queue can outlive the row."""
        location = LocationFactory.create()
        pk = location.pk
        location.delete()

        with patch(_ELEVATION) as elevation, patch(_FETCH) as forecast:
            _worker_prime_location.call(pk)

        elevation.assert_not_called()
        forecast.assert_not_called()


@pytest.mark.django_db
class TestEnqueue:
    """``enqueue_location_prime`` — deferred to commit."""

    def test_nothing_runs_until_the_transaction_commits(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """The admin saves inside a transaction; the upstream calls wait."""
        location = _linked()
        worker = MagicMock()

        with (
            patch("apps.locations.services.prime._worker_prime_location", worker),
        ):
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                enqueue_location_prime(location, moved=True)
            worker.enqueue.assert_not_called()

            assert len(callbacks) == 1
            callbacks[0]()

        worker.enqueue.assert_called_once_with(location.pk, True)
