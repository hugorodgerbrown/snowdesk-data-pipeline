"""
tests/weather/services/test_point_weather_fetcher.py — Tests for the point-shaped
weather_fetcher functions (fetch_weather_for_point / fetch_all_points).

Covers:
  - elevation pass-through — the outgoing request params include `elevation`
    equal to the point's elevation, and `daily` contains the extended
    variables; `hourly` contains the ski-relevant hourly set.
  - single model policy (SNOW-699) — no point ever sends `models=`, wherever
    it sits, so Open-Meteo picks the best model for the coordinates; each
    point makes exactly one request, and every non-2xx status surfaces as a
    failure rather than being retried.
  - 7-day window — a single API call returns POINT_FORECAST_DAYS days of
    daily data; one ForecastCellWeather row is persisted per day.
  - extended fields round-trip — a mocked full daily payload persists
    temperature/snowfall/wind/uv/etc. onto each row.
  - null tolerance — a payload omitting precipitation_probability_max /
    uv_index_max still persists (those fields land None, no KeyError).
  - freezing_level_height — derived as the daily max of the hourly block for
    each day.
  - hourly_series — populated for the first POINT_HOURLY_DAYS rows only;
    None beyond.
  - active-only — fetch_all_points fetches a favourited point and skips an
    unreferenced one.
  - idempotent re-run — second run updates the existing rows, not
    duplicates.
  - per-point failure — one point raising increments failed without
    aborting the batch.
  - dry-run (commit=False) writes no rows but still calls the API.
  - atomic window (SNOW-546) — a malformed timestamp or a failed write
    part-way through the 7-day loop leaves zero rows, not a partial
    window that reports as a failed point.
  - truncated model horizon (SNOW-628) — a payload whose tail carries a
    null weather_code (any model can run short of the 7-day request)
    stores the days that resolved instead of rolling the whole window
    back, and is not counted as a failure; a payload resolving no day at
    all still is.

All outbound HTTP calls are mocked via unittest.mock.patch so no network
traffic is required, mirroring test_weather_fetcher.py's pattern.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.db import IntegrityError
from django.test import override_settings

from apps.weather.models import ForecastCellWeather, ForecastCellWeatherHistory
from apps.weather.services.weather_fetcher import (
    POINT_FORECAST_DAYS,
    POINT_HOURLY_DAYS,
    fetch_all_points,
    fetch_weather_for_point,
)
from tests.factories import (
    FavouriteFactory,
    ForecastCellFactory,
    ForecastCellWeatherFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _daily_dates(start: str, days: int) -> list[str]:
    """Return `days` consecutive ISO date strings starting from `start`."""
    start_date = datetime.date.fromisoformat(start)
    return [
        (start_date + datetime.timedelta(days=idx)).isoformat() for idx in range(days)
    ]


def _make_full_point_response(
    start_date: str = "2026-05-01",
    days: int = POINT_FORECAST_DAYS,
    weather_codes: list[int] | None = None,
) -> dict[str, Any]:
    """Build a full multi-day Open-Meteo point-forecast API response dict.

    Daily arrays span `days` consecutive dates from `start_date`. The hourly
    block spans the same window at 6-hourly resolution (4 hours/day) —
    enough to exercise per-day filtering and the daily-max derivation
    without an unwieldy fixture.
    """
    dates = _daily_dates(start_date, days)
    codes = weather_codes or [1] * days

    hourly_times: list[str] = []
    hourly_temp: list[float] = []
    hourly_snowfall: list[float] = []
    hourly_precip: list[float] = []
    hourly_wind: list[float] = []
    hourly_gusts: list[float] = []
    hourly_freezing: list[float] = []
    for day_idx, day in enumerate(dates):
        for hour in (0, 6, 12, 18):
            hourly_times.append(f"{day}T{hour:02d}:00")
            hourly_temp.append(-2.0 + hour / 6)
            hourly_snowfall.append(0.5 if hour < 12 else 0.0)
            hourly_precip.append(0.5 if hour < 12 else 0.0)
            hourly_wind.append(10.0 + hour)
            hourly_gusts.append(20.0 + hour)
            # Freezing level rises through the day and increases with day_idx
            # so the derived daily-max is distinguishable per day.
            hourly_freezing.append(1500.0 + day_idx * 50 + hour * 10)

    return {
        "latitude": 46.1,
        "longitude": 7.4,
        "elevation": 1500.0,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": dates,
            "weather_code": codes,
            "sunrise": [f"{day}T05:32+02:00" for day in dates],
            "sunset": [f"{day}T20:45+02:00" for day in dates],
            "temperature_2m_max": [4.2] * days,
            "temperature_2m_min": [-3.1] * days,
            "apparent_temperature_max": [2.0] * days,
            "apparent_temperature_min": [-6.5] * days,
            "precipitation_sum": [1.5] * days,
            "snowfall_sum": [12.0] * days,
            "precipitation_probability_max": [40] * days,
            "precipitation_hours": [3.0] * days,
            "wind_speed_10m_max": [18.0] * days,
            "wind_gusts_10m_max": [35.0] * days,
            "wind_direction_10m_dominant": [280] * days,
            "uv_index_max": [4.5] * days,
            "daylight_duration": [46800.0] * days,
            "sunshine_duration": [30000.0] * days,
        },
        "hourly": {
            "time": hourly_times,
            "temperature_2m": hourly_temp,
            "snowfall": hourly_snowfall,
            "precipitation": hourly_precip,
            "wind_speed_10m": hourly_wind,
            "wind_gusts_10m": hourly_gusts,
            "freezing_level_height": hourly_freezing,
        },
    }


def _make_partial_point_response(
    start_date: str = "2026-05-01",
    days: int = POINT_FORECAST_DAYS,
) -> dict[str, Any]:
    """Build a point-forecast response omitting some extended variables.

    Omits precipitation_probability_max, uv_index_max, and the entire
    hourly block — Open-Meteo drops daily variables depending on the
    backing weather model, and the hourly block may legitimately be absent.
    """
    dates = _daily_dates(start_date, days)
    return {
        "latitude": 46.1,
        "longitude": 7.4,
        "elevation": 1500.0,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": dates,
            "weather_code": [2] * days,
            "sunrise": [f"{day}T05:32+02:00" for day in dates],
            "sunset": [f"{day}T20:45+02:00" for day in dates],
            "temperature_2m_max": [3.0] * days,
            "temperature_2m_min": [-4.0] * days,
            # precipitation_probability_max and uv_index_max omitted.
        },
        # hourly omitted entirely.
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
        point = ForecastCellFactory.create(elevation=1834.0)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["elevation"] == "1834.0"

    def test_daily_params_contain_extended_variables(self) -> None:
        """The daily params include the comprehensive variable set, not just the core trio."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        daily_fields = mock.call_args[1]["params"]["daily"].split(",")
        assert "weather_code" in daily_fields
        assert "sunrise" in daily_fields
        assert "sunset" in daily_fields
        assert "temperature_2m_max" in daily_fields
        assert "snowfall_sum" in daily_fields
        assert "wind_speed_10m_max" in daily_fields
        assert "uv_index_max" in daily_fields

    def test_hourly_params_contain_ski_relevant_variables(self) -> None:
        """The hourly param requests temperature/snowfall/precip/wind/freezing level."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        hourly_fields = mock.call_args[1]["params"]["hourly"].split(",")
        assert "temperature_2m" in hourly_fields
        assert "snowfall" in hourly_fields
        assert "precipitation" in hourly_fields
        assert "wind_speed_10m" in hourly_fields
        assert "wind_gusts_10m" in hourly_fields
        assert "freezing_level_height" in hourly_fields

    @pytest.mark.parametrize(
        ("label", "latitude", "longitude"),
        [
            ("Valais", 46.1, 7.4),
            ("London", 51.5, -0.13),
        ],
    )
    def test_no_models_param_is_ever_sent(
        self, label: str, latitude: float, longitude: float
    ) -> None:
        """No point sends `models=`, wherever it sits (SNOW-699).

        SNOW-443 briefly pinned `models=meteoswiss_icon_ch2` for points
        inside an Alpine bounding box, which left this assertion able to
        cover only the out-of-box case. Both coordinates are asserted here
        — one squarely inside the old box, one far outside it — so the
        geography cannot quietly come back without a test failing.
        """
        point = ForecastCellFactory.create(latitude=latitude, longitude=longitude)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        assert "models" not in mock.call_args[1]["params"], label
        # One model means one call: the retry path was the only thing that
        # could ever make a point issue two.
        assert mock.call_count == 1, label

    def test_request_window_spans_seven_days(self) -> None:
        """start_date/end_date span POINT_FORECAST_DAYS consecutive days."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["start_date"] == "2026-05-01"
        assert params["end_date"] == "2026-05-07"

    def test_lat_lon_passed_through(self) -> None:
        """The point's latitude/longitude are forwarded (not a region centre dict)."""
        point = ForecastCellFactory.create(latitude=47.2, longitude=8.1)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["latitude"] == "47.2"
        assert params["longitude"] == "8.1"

    def test_persists_one_row_per_day(self) -> None:
        """A 7-day daily payload persists 7 ForecastCellWeather rows."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        assert len(results) == POINT_FORECAST_DAYS
        assert all(created for _, created in results)
        assert (
            ForecastCellWeather.objects.filter(forecast_cell=point).count()
            == POINT_FORECAST_DAYS
        )
        rows = ForecastCellWeather.objects.filter(forecast_cell=point).order_by(
            "valid_for_date"
        )
        assert list(rows.values_list("valid_for_date", flat=True)) == [
            target + datetime.timedelta(days=idx) for idx in range(POINT_FORECAST_DAYS)
        ]

    def test_extended_fields_round_trip(self) -> None:
        """A full daily payload persists temperature/snowfall/wind/uv onto each row."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        weather, created = results[0]
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

    def test_freezing_level_height_derived_as_daily_max(self) -> None:
        """freezing_level_height is the max of that day's hourly values."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        # Day 0 hourly freezing levels: 1500, 1560, 1620, 1680 -> max 1680.
        weather_day0, _ = results[0]
        assert weather_day0.freezing_level_height == 1680.0
        # Day 1 hourly freezing levels: 1550, 1610, 1670, 1730 -> max 1730.
        weather_day1, _ = results[1]
        assert weather_day1.freezing_level_height == 1730.0

    def test_hourly_series_populated_for_near_term_days_only(self) -> None:
        """hourly_series is populated for the first POINT_HOURLY_DAYS rows; None beyond."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        for idx, (weather, _) in enumerate(results):
            if idx < POINT_HOURLY_DAYS:
                assert weather.hourly_series is not None
                assert len(weather.hourly_series) == 4  # 4 hours/day in the fixture
                first_hour = weather.hourly_series[0]
                assert set(first_hour) == {
                    "time",
                    "temperature_2m",
                    "snowfall",
                    "precipitation",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                    "freezing_level_height",
                }
            else:
                assert weather.hourly_series is None

    def test_omitted_extended_variables_land_as_none(self) -> None:
        """Omitted precipitation_probability_max/uv_index_max persist as None, not KeyError."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_partial_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        weather, _ = results[0]
        assert weather.precipitation_probability_max is None
        assert weather.uv_index_max is None
        # Core + partially-present fields are still persisted correctly.
        assert weather.weather_code == 2
        assert weather.temperature_2m_max == 3.0
        # A wholly-omitted hourly block degrades to None, not KeyError.
        assert weather.freezing_level_height is None
        assert weather.hourly_series is None

    def test_commit_false_returns_empty_list_but_calls_api(self) -> None:
        """commit=False calls the API but does not write to the database."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            results = fetch_weather_for_point(point, target, commit=False)

        assert results == []
        mock.assert_called_once()
        assert not ForecastCellWeather.objects.filter(forecast_cell=point).exists()

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            return_value=mock_response,
        ):
            with pytest.raises(requests.HTTPError):
                fetch_weather_for_point(point, target, commit=True)

    def test_malformed_mid_window_sunrise_writes_nothing(self) -> None:
        """A bad timestamp on day 4 of 7 leaves zero rows, not a partial window.

        SNOW-546: the per-day ``update_or_create`` loop had no
        transaction, and ``_build_point_defaults`` parses sunrise/sunset
        inside it. Days 0-3 committed, day 4 raised, days 4-6 never
        wrote — and ``fetch_all_points`` counted the point as failed,
        masking that a partial window had actually been persisted.
        """
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()
        api_data["daily"]["sunrise"][3] = "not-a-timestamp"

        with (
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            pytest.raises(ValueError),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastCellWeather.objects.filter(forecast_cell=point).count() == 0

    def test_write_failure_rolls_back_the_whole_window(self) -> None:
        """A mid-loop DB failure rolls back the days already written."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        real_uoc = ForecastCellWeather.objects.update_or_create
        call_count = {"n": 0}

        def _fail_on_third(*args: Any, **kwargs: Any) -> Any:
            """Let the first two writes succeed, fail the third."""
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise IntegrityError("simulated point weather write failure")
            return real_uoc(*args, **kwargs)

        with (
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                _mock_get(_make_full_point_response()),
            ),
            patch.object(
                ForecastCellWeather.objects,
                "update_or_create",
                side_effect=_fail_on_third,
            ),
            pytest.raises(IntegrityError),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastCellWeather.objects.filter(forecast_cell=point).count() == 0

    def test_upsert_updates_existing_rows(self) -> None:
        """A second call updates the existing ForecastCellWeather rows, not duplicates."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        ForecastCellWeatherFactory.create(
            forecast_cell=point, valid_for_date=target, weather_code=0
        )

        api_data = _make_full_point_response(weather_codes=[5] * POINT_FORECAST_DAYS)
        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        weather_day0, created_day0 = results[0]
        assert created_day0 is False
        assert weather_day0.weather_code == 5
        assert (
            ForecastCellWeather.objects.filter(forecast_cell=point).count()
            == POINT_FORECAST_DAYS
        )

    def test_base_url_threading(self) -> None:
        """When base_url is set, the request goes to {base_url}/forecast."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(
                point,
                target,
                commit=False,
                base_url="http://localhost:8000/dev/openmeteo-mirror/v1",
            )

        called_url = mock.call_args[0][0]
        assert called_url == "http://localhost:8000/dev/openmeteo-mirror/v1/forecast"

    @override_settings(OPEN_METEO_API_BASE_URL="https://api.example/v1")
    def test_falls_back_to_configured_host_when_base_url_none(self) -> None:
        """When base_url=None, the configured forecast host is used."""
        point = ForecastCellFactory.create()
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=False)

        assert mock.call_args[0][0] == "https://api.example/v1/forecast"

    @override_settings(
        OPEN_METEO_API_KEY="sk-test",
        OPEN_METEO_API_BASE_URL="https://customer-api.example/v1",
    )
    def test_sends_apikey_when_configured(self) -> None:
        """A configured key is appended to the point forecast params (SNOW-577)."""
        point = ForecastCellFactory.create()
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=False)

        assert mock.call_args.kwargs["params"]["apikey"] == "sk-test"

    def test_on_fetched_callback(self) -> None:
        """on_fetched is called once with the expected shape, for day 0."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response(weather_codes=[7] * POINT_FORECAST_DAYS)
        captured: list[dict[str, Any]] = []

        with patch(
            "apps.weather.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            fetch_weather_for_point(
                point,
                target,
                commit=False,
                on_fetched=captured.append,
            )

        assert len(captured) == 1
        record = captured[0]
        assert record["forecast_cell_id"] == point.pk
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
        ForecastCellFactory.create(latitude=50.0, longitude=10.0)  # unreferenced
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        assert mock.call_count == 1
        assert counts["created"] == POINT_FORECAST_DAYS
        assert counts["skipped"] == 0
        assert (
            ForecastCellWeather.objects.filter(forecast_cell=active_point).count()
            == POINT_FORECAST_DAYS
        )

    def test_no_active_points_makes_no_calls(self) -> None:
        """With no favourited points, fetch_all_points is a no-op."""
        ForecastCellFactory.create()  # unreferenced
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        mock.assert_not_called()
        assert counts == {"created": 0, "updated": 0, "failed": 0, "skipped": 0}

    def test_idempotent_rerun_counts_as_updated(self) -> None:
        """A second run for the same window updates rather than duplicates."""
        favourite = FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.weather.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            first = fetch_all_points(target, commit=True)
            second = fetch_all_points(target, commit=True)

        assert first["created"] == POINT_FORECAST_DAYS
        assert first["updated"] == 0
        assert second["created"] == 0
        assert second["updated"] == POINT_FORECAST_DAYS
        assert (
            ForecastCellWeather.objects.filter(
                forecast_cell=favourite.forecast_point
            ).count()
            == POINT_FORECAST_DAYS
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
            "apps.weather.services.weather_fetcher.requests.get", side_effect=fake_get
        ):
            counts = fetch_all_points(target, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == POINT_FORECAST_DAYS

    def test_commit_false_makes_no_db_writes(self) -> None:
        """commit=False calls the API for every active point but writes nothing."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=False)

        mock.assert_called_once()
        assert counts["created"] == 0
        assert counts["updated"] == 0
        assert ForecastCellWeather.objects.count() == 0


@pytest.mark.django_db
class TestFetchWeatherForPointProviderDates:
    """SNOW-466: rows are keyed off provider dates, not target_date + idx."""

    def test_shifted_dates_stored_under_provider_dates(self) -> None:
        """A response shifted a day forward stores rows under the provider dates."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        # Provider returns May 2..8 even though we requested from May 1.
        api_data = _make_full_point_response(start_date="2026-05-02")

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            fetch_weather_for_point(point, target, commit=True)

        stored = list(
            ForecastCellWeather.objects.filter(forecast_cell=point)
            .order_by("valid_for_date")
            .values_list("valid_for_date", flat=True)
        )
        assert stored == [
            datetime.date(2026, 5, 2) + datetime.timedelta(days=idx)
            for idx in range(POINT_FORECAST_DAYS)
        ]
        # The requested-but-not-returned date must never be invented.
        assert datetime.date(2026, 5, 1) not in stored

    def test_gapped_dates_do_not_fabricate_the_missing_day(self) -> None:
        """A gap in the provider dates is stored as-is, never back-filled."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()  # 7 aligned arrays
        gapped = [
            "2026-05-01",
            "2026-05-02",
            "2026-05-04",  # May 3 skipped
            "2026-05-05",
            "2026-05-06",
            "2026-05-07",
            "2026-05-08",
        ]
        api_data["daily"]["time"] = gapped

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            fetch_weather_for_point(point, target, commit=True)

        stored = list(
            ForecastCellWeather.objects.filter(forecast_cell=point)
            .order_by("valid_for_date")
            .values_list("valid_for_date", flat=True)
        )
        assert [d.isoformat() for d in stored] == gapped
        assert datetime.date(2026, 5, 3) not in stored

    def test_misaligned_array_lengths_raise_and_write_nothing(self) -> None:
        """A required array shorter than time raises and writes no rows."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()
        api_data["daily"]["weather_code"].pop()  # 6 codes vs 7 dates

        with (
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            pytest.raises(ValueError, match="misaligned"),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastCellWeather.objects.filter(forecast_cell=point).count() == 0


@pytest.mark.django_db
class TestSingleModelPolicy:
    """One model policy, one request per point, no retries (SNOW-699).

    SNOW-443 pinned ``models=meteoswiss_icon_ch2`` inside an Alpine
    bounding box and retried on the default chain when that request came
    back a 400 or with a dead day 0. Both are gone: with no model pinned
    there is no model-specific failure to catch, so a point issues exactly
    one request and every non-2xx status propagates.
    """

    def test_http_400_propagates(self) -> None:
        """A 400 is a failure now, not a cue to retry on another model.

        The old fallback read a 400 as "this point is outside ICON-CH's
        domain" and silently reissued the request. Nothing asks for a
        model any more, so a 400 means the request itself was bad and
        belongs in ``fetch_all_points``' failed counter like any other
        error.
        """
        point = ForecastCellFactory.create(latitude=46.1, longitude=7.4)

        rejected = MagicMock()
        rejected.status_code = 400
        error = requests.HTTPError(response=rejected)
        rejected.raise_for_status = MagicMock(side_effect=error)
        mock = MagicMock(return_value=rejected)

        with (
            patch("apps.weather.services.weather_fetcher.requests.get", mock),
            pytest.raises(requests.HTTPError),
        ):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=True)

        assert mock.call_count == 1

    def test_http_500_propagates(self) -> None:
        """A real outage still surfaces — no status is swallowed by a retry."""
        point = ForecastCellFactory.create(latitude=46.1, longitude=7.4)

        broken = MagicMock()
        broken.status_code = 500
        error = requests.HTTPError(response=broken)
        broken.raise_for_status = MagicMock(side_effect=error)
        mock = MagicMock(return_value=broken)

        with (
            patch("apps.weather.services.weather_fetcher.requests.get", mock),
            pytest.raises(requests.HTTPError),
        ):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=True)

        assert mock.call_count == 1

    def test_degraded_day_zero_is_not_retried(self) -> None:
        """A null day-0 weather_code stores the rest of the window, once.

        There is no second model to ask, so the payload that arrived is
        the payload that gets stored. Since SNOW-628 a dead day 0 is not
        fatal either: the six days that did resolve are kept and day 0 is
        logged, rather than the whole window being discarded for it.
        """
        point = ForecastCellFactory.create(latitude=46.1, longitude=7.4)
        target = datetime.date(2026, 5, 1)
        degraded = _make_full_point_response()
        degraded["daily"]["weather_code"][0] = None
        mock = _mock_get(degraded)

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            results = fetch_weather_for_point(point, target, commit=True)

        assert mock.call_count == 1
        assert len(results) == POINT_FORECAST_DAYS - 1
        stored = ForecastCellWeather.objects.filter(forecast_cell=point)
        assert stored.count() == POINT_FORECAST_DAYS - 1
        assert not stored.filter(valid_for_date=target).exists()


@pytest.mark.django_db
class TestHistoryIsOptIn:
    """History retention is off unless asked for (SNOW-629).

    Nothing user-facing reads ForecastCellWeatherHistory — it exists for
    future convergence analysis — so it is switchable independently of the
    operational ForecastCellWeather write, which must never be affected
    by the setting either way.
    """

    def test_history_not_written_by_default(self) -> None:
        """Without add_history, the weather rows land and no history does."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert (
            ForecastCellWeather.objects.filter(forecast_cell=point).count()
            == POINT_FORECAST_DAYS
        )
        assert not ForecastCellWeatherHistory.objects.exists()

    def test_history_written_when_requested(self) -> None:
        """add_history=True restores the SNOW-575 retention."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        assert (
            ForecastCellWeatherHistory.objects.filter(forecast_cell=point).count()
            == POINT_FORECAST_DAYS
        )

    def test_fetch_all_points_threads_the_flag(self) -> None:
        """The batch entry point forwards add_history to each point."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            counts = fetch_all_points(target, commit=True, add_history=True)

        assert counts["created"] == POINT_FORECAST_DAYS
        assert ForecastCellWeatherHistory.objects.count() == POINT_FORECAST_DAYS

    def test_weather_rows_are_unaffected_by_the_flag(self) -> None:
        """Turning history off changes nothing about the operational rows."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            without = fetch_weather_for_point(point, target, commit=True)
            with_history = fetch_weather_for_point(
                point, target, commit=True, add_history=True
            )

        assert [row.valid_for_date for row, _ in without] == [
            row.valid_for_date for row, _ in with_history
        ]


@pytest.mark.django_db
class TestTruncatedModelHorizon:
    """A model that runs short of the window stores what it resolved (SNOW-628).

    Any model can cover fewer days than the 7-day request. The days past
    its horizon come back with a null ``weather_code`` while
    ``sunrise``/``sunset`` — astronomical rather than modelled — stay
    populated, which is what made this look like a well-formed payload
    right up to the write. Against a non-null column that raised, and
    SNOW-546's transaction then discarded the good days with it, so a
    point whose model ran short wrote nothing at all.

    This is a general guard on the shape of the payload, not on any one
    provider: it long predates the model pin SNOW-699 removed and outlives
    it unchanged.
    """

    @staticmethod
    def _truncated(days_resolved: int = 5) -> dict[str, Any]:
        """Return a 7-day payload whose tail carries no weather_code."""
        payload = _make_full_point_response()
        for idx in range(days_resolved, POINT_FORECAST_DAYS):
            payload["daily"]["weather_code"][idx] = None
        return payload

    def test_short_window_is_stored_not_rejected(self) -> None:
        """Five resolved days persist five rows; the null tail is dropped."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(self._truncated()),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        assert len(results) == 5
        rows = ForecastCellWeather.objects.filter(forecast_cell=point).order_by(
            "valid_for_date"
        )
        assert list(rows.values_list("valid_for_date", flat=True)) == [
            target + datetime.timedelta(days=idx) for idx in range(5)
        ]

    def test_near_term_days_survive_the_null_tail(self) -> None:
        """Day 0 is written — the regression was the tail rolling it back."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(self._truncated()),
        ):
            fetch_weather_for_point(point, target, commit=True)

        day_zero = ForecastCellWeather.objects.get(
            forecast_cell=point, valid_for_date=target
        )
        assert day_zero.weather_code == 1
        assert day_zero.hourly_series is not None

    def test_history_follows_the_short_window(self) -> None:
        """History rows are written for the stored days only."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(self._truncated()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        history = ForecastCellWeatherHistory.objects.filter(forecast_cell=point)
        assert history.count() == 5
        assert sorted(history.values_list("lead_days", flat=True)) == [0, 1, 2, 3, 4]

    def test_short_window_is_not_a_batch_failure(self) -> None:
        """fetch_all_points counts the rows, not a failure — so the command exits 0."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(self._truncated()),
        ):
            counts = fetch_all_points(target, commit=True)

        assert counts["failed"] == 0
        assert counts["created"] == 5

    def test_payload_with_no_usable_day_is_still_a_failure(self) -> None:
        """A window resolving nothing is malformed, not short — it stays counted."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(self._truncated(days_resolved=0)),
        ):
            counts = fetch_all_points(target, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == 0
        assert ForecastCellWeather.objects.count() == 0


# ---------------------------------------------------------------------------
# ForecastCellWeatherHistory (SNOW-575)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestForecastHistoryCapture:
    """fetch_weather_for_point retains each issue's view of a forecast day."""

    def test_one_history_row_per_day_of_the_window(self) -> None:
        """A single run writes one history row per day, stamped with the run date."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        rows = ForecastCellWeatherHistory.objects.filter(forecast_cell=point)
        assert rows.count() == POINT_FORECAST_DAYS
        assert {row.issued_date for row in rows} == {target}

    def test_lead_days_span_the_window_from_zero(self) -> None:
        """lead_days runs 0..N-1 across the window, day 0 being the day-of view."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        leads = sorted(
            ForecastCellWeatherHistory.objects.filter(forecast_cell=point).values_list(
                "lead_days", flat=True
            )
        )
        assert leads == list(range(POINT_FORECAST_DAYS))

    def test_later_issue_appends_rather_than_overwrites(self) -> None:
        """A run on a later date adds a second view of an overlapping day.

        This is the behaviour the model exists for: ForecastCellWeather
        keeps only the newer forecast for the shared day, while the history
        table keeps both.
        """
        point = ForecastCellFactory.create()
        first_run = datetime.date(2026, 5, 1)
        second_run = datetime.date(2026, 5, 2)
        shared_day = datetime.date(2026, 5, 2)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response(start_date="2026-05-01")),
        ):
            fetch_weather_for_point(point, first_run, commit=True, add_history=True)
        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response(start_date="2026-05-02")),
        ):
            fetch_weather_for_point(point, second_run, commit=True, add_history=True)

        series = ForecastCellWeatherHistory.objects.convergence_for(point, shared_day)
        assert [(row.issued_date, row.lead_days) for row in series] == [
            (first_run, 1),
            (second_run, 0),
        ]
        # The live table still holds exactly one row for that day.
        assert (
            ForecastCellWeather.objects.filter(
                forecast_cell=point, valid_for_date=shared_day
            ).count()
            == 1
        )

    def test_same_day_rerun_updates_rather_than_appends(self) -> None:
        """The four runs within one day collapse to a single history row."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)
        first = _make_full_point_response()
        second = _make_full_point_response(weather_codes=[73] * POINT_FORECAST_DAYS)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get", _mock_get(first)
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)
        with patch(
            "apps.weather.services.weather_fetcher.requests.get", _mock_get(second)
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        rows = ForecastCellWeatherHistory.objects.filter(forecast_cell=point)
        assert rows.count() == POINT_FORECAST_DAYS
        # Last run of the day wins.
        assert {row.weather_code for row in rows} == {73}

    def test_payload_is_the_retained_subset(self) -> None:
        """History carries the convergence scalars and omits the rest."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_full_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        row = ForecastCellWeatherHistory.objects.get(
            forecast_cell=point, valid_for_date=target
        )
        assert row.temperature_2m_max == 4.2
        assert row.temperature_2m_min == -3.1
        assert row.precipitation_sum == 1.5
        assert row.snowfall_sum == 12.0
        assert row.wind_speed_10m_max == 18.0
        # Derived from the hourly block, same as its ForecastCellWeather twin.
        assert row.freezing_level_height == 1680.0
        assert not hasattr(row, "hourly_series")
        assert not hasattr(row, "sunrise")

    def test_partial_payload_degrades_to_none(self) -> None:
        """A response omitting extended variables still writes history rows."""
        point = ForecastCellFactory.create()
        target = datetime.date(2026, 5, 1)

        with patch(
            "apps.weather.services.weather_fetcher.requests.get",
            _mock_get(_make_partial_point_response()),
        ):
            fetch_weather_for_point(point, target, commit=True, add_history=True)

        row = ForecastCellWeatherHistory.objects.get(
            forecast_cell=point, valid_for_date=target
        )
        assert row.weather_code == 2
        assert row.snowfall_sum is None
        assert row.freezing_level_height is None

    def test_commit_false_writes_no_history(self) -> None:
        """A dry run calls the API but persists nothing."""
        point = ForecastCellFactory.create()
        mock = _mock_get(_make_full_point_response())

        with patch("apps.weather.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(
                point, datetime.date(2026, 5, 1), commit=False, add_history=True
            )

        assert mock.call_count == 1
        assert not ForecastCellWeatherHistory.objects.exists()

    def test_history_rolls_back_with_its_partner_row(self) -> None:
        """A mid-window failure leaves no history rows behind (SNOW-546).

        The trigger is a malformed timestamp rather than a null
        weather_code: since SNOW-628 a null required field marks the day
        unstorable and drops it before the loop, so it no longer reaches
        the write at all. A value that is present but unparseable still
        raises inside the transaction, which is what this asserts.
        """
        point = ForecastCellFactory.create(latitude=51.5, longitude=-0.13)
        degraded = _make_full_point_response()
        degraded["daily"]["sunrise"][3] = "not-a-timestamp"

        with (
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                _mock_get(degraded),
            ),
            pytest.raises(ValueError),
        ):
            fetch_weather_for_point(
                point, datetime.date(2026, 5, 1), commit=True, add_history=True
            )

        assert not ForecastCellWeatherHistory.objects.exists()
        assert not ForecastCellWeather.objects.exists()
