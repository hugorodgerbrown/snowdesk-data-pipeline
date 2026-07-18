"""
tests/bulletins/services/test_point_weather_fetcher.py — Tests for the point-shaped
weather_fetcher functions (fetch_weather_for_point / fetch_all_points).

Covers:
  - elevation pass-through — the outgoing request params include `elevation`
    equal to the point's elevation, and `daily` contains the extended
    variables.
  - extended fields round-trip — a mocked full daily payload persists
    temperature/snowfall/wind/uv/etc. onto the row.
  - null tolerance — a payload omitting precipitation_probability_max /
    uv_index_max still persists (those fields land None, no KeyError).
  - active-only — fetch_all_points fetches a favourited point and skips an
    unreferenced one.
  - idempotent re-run — second run updates the existing row.
  - per-point failure — one point raising increments failed without
    aborting the batch.
  - dry-run (commit=False) writes no rows but still calls the API.

All outbound HTTP calls are mocked via unittest.mock.patch so no network
traffic is required, mirroring test_weather_fetcher.py's pattern.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from bulletins.models import ForecastPointWeather
from bulletins.services.weather_fetcher import (
    fetch_all_points,
    fetch_weather_for_point,
)
from tests.factories import (
    FavouriteFactory,
    ForecastPointFactory,
    ForecastPointWeatherFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_full_point_response(
    weather_code: int = 1,
    sunrise: str = "2026-05-01T05:32+02:00",
    sunset: str = "2026-05-01T20:45+02:00",
    target_date: str = "2026-05-01",
) -> dict[str, Any]:
    """Build a full Open-Meteo point-forecast API response dict."""
    return {
        "latitude": 46.1,
        "longitude": 7.4,
        "elevation": 1500.0,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": [target_date],
            "weather_code": [weather_code],
            "sunrise": [sunrise],
            "sunset": [sunset],
            "temperature_2m_max": [4.2],
            "temperature_2m_min": [-3.1],
            "apparent_temperature_max": [2.0],
            "apparent_temperature_min": [-6.5],
            "precipitation_sum": [1.5],
            "snowfall_sum": [12.0],
            "precipitation_probability_max": [40],
            "precipitation_hours": [3.0],
            "wind_speed_10m_max": [18.0],
            "wind_gusts_10m_max": [35.0],
            "wind_direction_10m_dominant": [280],
            "uv_index_max": [4.5],
            "daylight_duration": [46800.0],
            "sunshine_duration": [30000.0],
        },
    }


def _make_partial_point_response(
    target_date: str = "2026-05-01",
) -> dict[str, Any]:
    """Build a point-forecast response omitting some extended variables.

    Omits precipitation_probability_max and uv_index_max — Open-Meteo drops
    these depending on the backing weather model.
    """
    return {
        "latitude": 46.1,
        "longitude": 7.4,
        "elevation": 1500.0,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": [target_date],
            "weather_code": [2],
            "sunrise": ["2026-05-01T05:32+02:00"],
            "sunset": ["2026-05-01T20:45+02:00"],
            "temperature_2m_max": [3.0],
            "temperature_2m_min": [-4.0],
            # precipitation_probability_max and uv_index_max omitted.
        },
    }


def _mock_get(response_data: dict[str, Any]) -> MagicMock:
    """Return a mock for requests.get that yields a JSON response."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = response_data
    mock = MagicMock(return_value=mock_response)
    return mock


# ---------------------------------------------------------------------------
# fetch_weather_for_point
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherForPoint:
    """Tests for fetch_weather_for_point."""

    def test_elevation_passed_through_to_api(self) -> None:
        """The point's elevation is forwarded to Open-Meteo as a string param."""
        point = ForecastPointFactory.create(elevation=1834.0)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["elevation"] == "1834.0"

    def test_daily_params_contain_extended_variables(self) -> None:
        """The daily params include the comprehensive variable set, not just the core trio."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        daily_fields = mock.call_args[1]["params"]["daily"].split(",")
        assert "weather_code" in daily_fields
        assert "sunrise" in daily_fields
        assert "sunset" in daily_fields
        assert "temperature_2m_max" in daily_fields
        assert "snowfall_sum" in daily_fields
        assert "wind_speed_10m_max" in daily_fields
        assert "uv_index_max" in daily_fields

    def test_lat_lon_passed_through(self) -> None:
        """The point's latitude/longitude are forwarded (not a region centre dict)."""
        point = ForecastPointFactory.create(latitude=47.2, longitude=8.1)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["latitude"] == "47.2"
        assert params["longitude"] == "8.1"

    def test_extended_fields_round_trip(self) -> None:
        """A full daily payload persists temperature/snowfall/wind/uv onto the row."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_point(point, target, commit=True)

        assert result is not None
        weather, created = result
        assert created is True
        assert weather.temperature_2m_max == 4.2
        assert weather.temperature_2m_min == -3.1
        assert weather.apparent_temperature_max == 2.0
        assert weather.apparent_temperature_min == -6.5
        assert weather.precipitation_sum == 1.5
        assert weather.snowfall_sum == 12.0
        assert weather.precipitation_probability_max == 40
        assert weather.precipitation_hours == 3.0
        assert weather.wind_speed_10m_max == 18.0
        assert weather.wind_gusts_10m_max == 35.0
        assert weather.wind_direction_10m_dominant == 280
        assert weather.uv_index_max == 4.5
        assert weather.daylight_duration == 46800.0
        assert weather.sunshine_duration == 30000.0

    def test_omitted_extended_variables_land_as_none(self) -> None:
        """Omitted precipitation_probability_max/uv_index_max persist as None, not KeyError."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_partial_point_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_point(point, target, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.precipitation_probability_max is None
        assert weather.uv_index_max is None
        # Core + partially-present fields are still persisted correctly.
        assert weather.weather_code == 2
        assert weather.temperature_2m_max == 3.0

    def test_commit_false_returns_none_but_calls_api(self) -> None:
        """commit=False calls the API but does not write to the database."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            result = fetch_weather_for_point(point, target, commit=False)

        assert result is None
        mock.assert_called_once()
        assert not ForecastPointWeather.objects.filter(
            forecast_point=point, valid_for_date=target
        ).exists()

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            return_value=mock_response,
        ):
            with pytest.raises(requests.HTTPError):
                fetch_weather_for_point(point, target, commit=True)

    def test_upsert_updates_existing_row(self) -> None:
        """A second call updates the existing ForecastPointWeather row."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=target, weather_code=0
        )

        api_data = _make_full_point_response(weather_code=5)
        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_point(point, target, commit=True)

        assert result is not None
        weather, created = result
        assert created is False
        assert weather.weather_code == 5
        assert (
            ForecastPointWeather.objects.filter(
                forecast_point=point, valid_for_date=target
            ).count()
            == 1
        )

    def test_base_url_threading(self) -> None:
        """When base_url is set, the request goes to {base_url}/forecast."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(
                point,
                target,
                commit=False,
                base_url="http://localhost:8000/dev/openmeteo-mirror/v1",
            )

        called_url = mock.call_args[0][0]
        assert called_url == "http://localhost:8000/dev/openmeteo-mirror/v1/forecast"

    def test_on_fetched_callback(self) -> None:
        """on_fetched is called once with the expected shape."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response(weather_code=7)
        captured: list[dict[str, Any]] = []

        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            fetch_weather_for_point(
                point,
                target,
                commit=False,
                on_fetched=captured.append,
            )

        assert len(captured) == 1
        record = captured[0]
        assert record["forecast_point_id"] == point.pk
        assert record["date"] == "2026-05-01"
        assert record["weather_code"] == 7
        assert "sunrise" in record
        assert "sunset" in record
        assert "captured_at" in record


# ---------------------------------------------------------------------------
# fetch_all_points
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchAllPoints:
    """Tests for fetch_all_points."""

    def test_fetches_only_active_points(self) -> None:
        """Only points with at least one Favourite are fetched; others are skipped."""
        favourite = FavouriteFactory.create()
        active_point = favourite.forecast_point
        ForecastPointFactory.create(latitude=50.0, longitude=10.0)  # unreferenced
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        assert mock.call_count == 1
        assert counts["created"] == 1
        assert counts["skipped"] == 0
        assert ForecastPointWeather.objects.filter(
            forecast_point=active_point, valid_for_date=target
        ).exists()

    def test_no_active_points_makes_no_calls(self) -> None:
        """With no favourited points, fetch_all_points is a no-op."""
        ForecastPointFactory.create()  # unreferenced
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        mock.assert_not_called()
        assert counts == {"created": 0, "updated": 0, "failed": 0, "skipped": 0}

    def test_idempotent_rerun_counts_as_updated(self) -> None:
        """A second run for the same date updates rather than duplicates."""
        favourite = FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            first = fetch_all_points(target, commit=True)
            second = fetch_all_points(target, commit=True)

        assert first["created"] == 1
        assert first["updated"] == 0
        assert second["created"] == 0
        assert second["updated"] == 1
        assert (
            ForecastPointWeather.objects.filter(
                forecast_point=favourite.forecast_point, valid_for_date=target
            ).count()
            == 1
        )

    def test_per_point_failure_counted_without_aborting_batch(self) -> None:
        """A per-point HTTP failure is counted as failed; other points still succeed."""
        failing = FavouriteFactory.create()
        succeeding = FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)

        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = _make_full_point_response()

        error_response = MagicMock()
        error_response.raise_for_status.side_effect = requests.HTTPError("500")

        # Points are iterated in `id` order — the first-created favourite's
        # point fails, the second succeeds.
        ordered_points = sorted(
            [failing.forecast_point, succeeding.forecast_point], key=lambda p: p.pk
        )
        responses = {
            ordered_points[0].pk: error_response,
            ordered_points[1].pk: ok_response,
        }

        def fake_get(url: str, params: dict[str, Any], timeout: int) -> MagicMock:
            lat = float(params["latitude"])
            for point in ordered_points:
                if point.latitude == lat:
                    return responses[point.pk]
            raise AssertionError("unexpected point in fake_get")

        with patch(
            "bulletins.services.weather_fetcher.requests.get", side_effect=fake_get
        ):
            counts = fetch_all_points(target, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == 1

    def test_commit_false_makes_no_db_writes(self) -> None:
        """commit=False calls the API for every active point but writes nothing."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=False)

        mock.assert_called_once()
        assert counts["created"] == 0
        assert counts["updated"] == 0
        assert ForecastPointWeather.objects.count() == 0
