"""
tests/bulletins/services/test_point_weather_fetcher.py — Tests for the point-shaped
weather_fetcher functions (fetch_weather_for_point / fetch_all_points).

Covers:
  - elevation pass-through — the outgoing request params include `elevation`
    equal to the point's elevation, and `daily` contains the extended
    variables; `hourly` contains the ski-relevant hourly set.
  - ICON-CH model selection (SNOW-443) — points inside ICON_CH_BOUNDS send
    `models=meteoswiss_icon_ch2`; points outside send no `models=` at all;
    a 400 or a null day-0 field falls back once to the default chain, and
    any other HTTP status still surfaces as a failure.
  - 7-day window — a single API call returns POINT_FORECAST_DAYS days of
    daily data; one ForecastPointWeather row is persisted per day.
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

from apps.bulletins.models import ForecastPointWeather
from apps.bulletins.services.weather_fetcher import (
    ICON_CH_BOUNDS,
    ICON_CH_MODEL,
    POINT_FORECAST_DAYS,
    POINT_HOURLY_DAYS,
    _is_alpine_point,
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
        point = ForecastPointFactory.create(elevation=1834.0)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["elevation"] == "1834.0"

    def test_daily_params_contain_extended_variables(self) -> None:
        """The daily params include the comprehensive variable set, not just the core trio."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        hourly_fields = mock.call_args[1]["params"]["hourly"].split(",")
        assert "temperature_2m" in hourly_fields
        assert "snowfall" in hourly_fields
        assert "precipitation" in hourly_fields
        assert "wind_speed_10m" in hourly_fields
        assert "wind_gusts_10m" in hourly_fields
        assert "freezing_level_height" in hourly_fields

    def test_no_models_param_outside_the_icon_ch_domain(self) -> None:
        """An out-of-domain point sends no `models=` — the default chain stands.

        This assertion previously covered every point (SNOW-417 shipped on
        the default chain unconditionally). SNOW-443 narrowed it: the
        factory's default coordinates are in Valais, which is inside the
        ICON-CH box, so the "no models" case now needs a point that
        genuinely is not.
        """
        point = ForecastPointFactory.create(latitude=51.5, longitude=-0.13)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        assert "models" not in mock.call_args[1]["params"]

    def test_request_window_spans_seven_days(self) -> None:
        """start_date/end_date span POINT_FORECAST_DAYS consecutive days."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["start_date"] == "2026-05-01"
        assert params["end_date"] == "2026-05-07"

    def test_lat_lon_passed_through(self) -> None:
        """The point's latitude/longitude are forwarded (not a region centre dict)."""
        point = ForecastPointFactory.create(latitude=47.2, longitude=8.1)
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, target, commit=False)

        params = mock.call_args[1]["params"]
        assert params["latitude"] == "47.2"
        assert params["longitude"] == "8.1"

    def test_persists_one_row_per_day(self) -> None:
        """A 7-day daily payload persists 7 ForecastPointWeather rows."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        assert len(results) == POINT_FORECAST_DAYS
        assert all(created for _, created in results)
        assert (
            ForecastPointWeather.objects.filter(forecast_point=point).count()
            == POINT_FORECAST_DAYS
        )
        rows = ForecastPointWeather.objects.filter(forecast_point=point).order_by(
            "valid_for_date"
        )
        assert list(rows.values_list("valid_for_date", flat=True)) == [
            target + datetime.timedelta(days=idx) for idx in range(POINT_FORECAST_DAYS)
        ]

    def test_extended_fields_round_trip(self) -> None:
        """A full daily payload persists temperature/snowfall/wind/uv onto each row."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_partial_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            results = fetch_weather_for_point(point, target, commit=False)

        assert results == []
        mock.assert_called_once()
        assert not ForecastPointWeather.objects.filter(forecast_point=point).exists()

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
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
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()
        api_data["daily"]["sunrise"][3] = "not-a-timestamp"

        with (
            patch(
                "apps.bulletins.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            pytest.raises(ValueError),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastPointWeather.objects.filter(forecast_point=point).count() == 0

    def test_write_failure_rolls_back_the_whole_window(self) -> None:
        """A mid-loop DB failure rolls back the days already written."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        real_uoc = ForecastPointWeather.objects.update_or_create
        call_count = {"n": 0}

        def _fail_on_third(*args: Any, **kwargs: Any) -> Any:
            """Let the first two writes succeed, fail the third."""
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise IntegrityError("simulated point weather write failure")
            return real_uoc(*args, **kwargs)

        with (
            patch(
                "apps.bulletins.services.weather_fetcher.requests.get",
                _mock_get(_make_full_point_response()),
            ),
            patch.object(
                ForecastPointWeather.objects,
                "update_or_create",
                side_effect=_fail_on_third,
            ),
            pytest.raises(IntegrityError),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastPointWeather.objects.filter(forecast_point=point).count() == 0

    def test_upsert_updates_existing_rows(self) -> None:
        """A second call updates the existing ForecastPointWeather rows, not duplicates."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=target, weather_code=0
        )

        api_data = _make_full_point_response(weather_codes=[5] * POINT_FORECAST_DAYS)
        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            results = fetch_weather_for_point(point, target, commit=True)

        weather_day0, created_day0 = results[0]
        assert created_day0 is False
        assert weather_day0.weather_code == 5
        assert (
            ForecastPointWeather.objects.filter(forecast_point=point).count()
            == POINT_FORECAST_DAYS
        )

    def test_base_url_threading(self) -> None:
        """When base_url is set, the request goes to {base_url}/forecast."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(
                point,
                target,
                commit=False,
                base_url="http://localhost:8000/dev/openmeteo-mirror/v1",
            )

        called_url = mock.call_args[0][0]
        assert called_url == "http://localhost:8000/dev/openmeteo-mirror/v1/forecast"

    def test_on_fetched_callback(self) -> None:
        """on_fetched is called once with the expected shape, for day 0."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response(weather_codes=[7] * POINT_FORECAST_DAYS)
        captured: list[dict[str, Any]] = []

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
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

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        assert mock.call_count == 1
        assert counts["created"] == POINT_FORECAST_DAYS
        assert counts["skipped"] == 0
        assert (
            ForecastPointWeather.objects.filter(forecast_point=active_point).count()
            == POINT_FORECAST_DAYS
        )

    def test_no_active_points_makes_no_calls(self) -> None:
        """With no favourited points, fetch_all_points is a no-op."""
        ForecastPointFactory.create()  # unreferenced
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=True)

        mock.assert_not_called()
        assert counts == {"created": 0, "updated": 0, "failed": 0, "skipped": 0}

    def test_idempotent_rerun_counts_as_updated(self) -> None:
        """A second run for the same window updates rather than duplicates."""
        favourite = FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            first = fetch_all_points(target, commit=True)
            second = fetch_all_points(target, commit=True)

        assert first["created"] == POINT_FORECAST_DAYS
        assert first["updated"] == 0
        assert second["created"] == 0
        assert second["updated"] == POINT_FORECAST_DAYS
        assert (
            ForecastPointWeather.objects.filter(
                forecast_point=favourite.forecast_point
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
            "apps.bulletins.services.weather_fetcher.requests.get", side_effect=fake_get
        ):
            counts = fetch_all_points(target, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == POINT_FORECAST_DAYS

    def test_commit_false_makes_no_db_writes(self) -> None:
        """commit=False calls the API for every active point but writes nothing."""
        FavouriteFactory.create()
        target = datetime.date(2026, 5, 1)
        mock = _mock_get(_make_full_point_response())

        with patch("apps.bulletins.services.weather_fetcher.requests.get", mock):
            counts = fetch_all_points(target, commit=False)

        mock.assert_called_once()
        assert counts["created"] == 0
        assert counts["updated"] == 0
        assert ForecastPointWeather.objects.count() == 0


@pytest.mark.django_db
class TestFetchWeatherForPointProviderDates:
    """SNOW-466: rows are keyed off provider dates, not target_date + idx."""

    def test_shifted_dates_stored_under_provider_dates(self) -> None:
        """A response shifted a day forward stores rows under the provider dates."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        # Provider returns May 2..8 even though we requested from May 1.
        api_data = _make_full_point_response(start_date="2026-05-02")

        with patch(
            "apps.bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            fetch_weather_for_point(point, target, commit=True)

        stored = list(
            ForecastPointWeather.objects.filter(forecast_point=point)
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
        point = ForecastPointFactory.create()
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
            "apps.bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            fetch_weather_for_point(point, target, commit=True)

        stored = list(
            ForecastPointWeather.objects.filter(forecast_point=point)
            .order_by("valid_for_date")
            .values_list("valid_for_date", flat=True)
        )
        assert [d.isoformat() for d in stored] == gapped
        assert datetime.date(2026, 5, 3) not in stored

    def test_misaligned_array_lengths_raise_and_write_nothing(self) -> None:
        """A required array shorter than time raises and writes no rows."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_full_point_response()
        api_data["daily"]["weather_code"].pop()  # 6 codes vs 7 dates

        with (
            patch(
                "apps.bulletins.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            pytest.raises(ValueError, match="misaligned"),
        ):
            fetch_weather_for_point(point, target, commit=True)

        assert ForecastPointWeather.objects.filter(forecast_point=point).count() == 0


@pytest.mark.django_db
class TestIconChModelSelection:
    """MeteoSwiss ICON-CH2 selection and its fallback (SNOW-443).

    Points inside ``ICON_CH_BOUNDS`` ask for the 2 km MeteoSwiss run
    rather than Open-Meteo's default blended chain. The fallback is not
    defensive padding: ``ForecastPointWeather.weather_code`` is a
    ``PositiveSmallIntegerField`` with no ``null=True``, so a partial
    ICON-CH payload would raise at ``update_or_create`` rather than
    skipping the row.
    """

    def test_in_domain_point_requests_icon_ch2(self) -> None:
        """A Valais point sends models=meteoswiss_icon_ch2."""
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=False)

        assert mock.call_args[1]["params"]["models"] == ICON_CH_MODEL

    def test_wider_alpine_arc_is_in_domain(self) -> None:
        """Chamonix and the Dolomites are inside the box, not just Switzerland.

        The box is deliberately wider than the Swiss border — Snowdesk
        serves the wider arc via ALBINA and Météo-France, and ICON-CH
        covers it.
        """
        for label, lat, lon in (
            ("Chamonix", 45.92, 6.87),
            ("Cortina", 46.54, 12.14 - 1.0),
            ("Innsbruck", 47.27, 11.39),
        ):
            point = ForecastPointFactory.create(latitude=lat, longitude=lon)
            mock = _mock_get(_make_full_point_response())
            with patch("bulletins.services.weather_fetcher.requests.get", mock):
                fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=False)
            assert mock.call_args[1]["params"].get("models") == ICON_CH_MODEL, label

    def test_out_of_domain_point_makes_exactly_one_request(self) -> None:
        """No speculative ICON-CH call is made for a point outside the box."""
        point = ForecastPointFactory.create(latitude=51.5, longitude=-0.13)
        mock = _mock_get(_make_full_point_response())

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=False)

        assert mock.call_count == 1

    def test_null_day_zero_falls_back_to_the_default_chain(self) -> None:
        """A dead day 0 triggers one retry with models= dropped."""
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        degraded = _make_full_point_response()
        degraded["daily"]["weather_code"][0] = None

        mock_response_degraded = MagicMock()
        mock_response_degraded.raise_for_status = MagicMock()
        mock_response_degraded.json.return_value = degraded
        mock_response_good = MagicMock()
        mock_response_good.raise_for_status = MagicMock()
        mock_response_good.json.return_value = _make_full_point_response()
        mock = MagicMock(side_effect=[mock_response_degraded, mock_response_good])

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            results = fetch_weather_for_point(
                point, datetime.date(2026, 5, 1), commit=True
            )

        assert mock.call_count == 2
        assert "models" in mock.call_args_list[0][1]["params"]
        assert "models" not in mock.call_args_list[1][1]["params"]
        # The persisted rows come from the fallback payload, so no null
        # weather_code reaches the non-nullable column.
        assert len(results) == POINT_FORECAST_DAYS
        assert (
            ForecastPointWeather.objects.filter(weather_code__isnull=True).count() == 0
        )

    def test_missing_sunrise_also_triggers_the_fallback(self) -> None:
        """sunrise and sunset are required by _build_point_defaults too."""
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        degraded = _make_full_point_response()
        degraded["daily"]["sunrise"][0] = None

        first = MagicMock()
        first.raise_for_status = MagicMock()
        first.json.return_value = degraded
        second = MagicMock()
        second.raise_for_status = MagicMock()
        second.json.return_value = _make_full_point_response()
        mock = MagicMock(side_effect=[first, second])

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=True)

        assert mock.call_count == 2

    def test_http_400_falls_back_to_the_default_chain(self) -> None:
        """A 400 means the box was optimistic for this point — retry, don't fail."""
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        rejected = MagicMock()
        rejected.status_code = 400
        error = requests.HTTPError(response=rejected)
        rejected.raise_for_status = MagicMock(side_effect=error)

        good = MagicMock()
        good.raise_for_status = MagicMock()
        good.json.return_value = _make_full_point_response()
        mock = MagicMock(side_effect=[rejected, good])

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            results = fetch_weather_for_point(
                point, datetime.date(2026, 5, 1), commit=True
            )

        assert mock.call_count == 2
        assert "models" not in mock.call_args_list[1][1]["params"]
        assert len(results) == POINT_FORECAST_DAYS

    def test_http_500_is_not_swallowed_by_the_fallback(self) -> None:
        """A real outage must still surface — only a 400 means out-of-domain.

        Retrying every failure would turn a rate limit or an outage into
        two requests and hide it from fetch_all_points' failed counter.
        """
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        broken = MagicMock()
        broken.status_code = 500
        error = requests.HTTPError(response=broken)
        broken.raise_for_status = MagicMock(side_effect=error)
        mock = MagicMock(return_value=broken)

        with (
            patch("bulletins.services.weather_fetcher.requests.get", mock),
            pytest.raises(requests.HTTPError),
        ):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=True)

        assert mock.call_count == 1

    def test_out_of_domain_degraded_payload_is_not_retried(self) -> None:
        """The fallback belongs to ICON-CH; the default chain gets no second go.

        A null day-0 weather_code from the default chain stays fatal, as it
        was before SNOW-443 — the point surfaces in fetch_all_points'
        failed counter rather than being silently retried against the
        same model that just produced it.
        """
        point = ForecastPointFactory.create(latitude=51.5, longitude=-0.13)
        degraded = _make_full_point_response()
        degraded["daily"]["weather_code"][0] = None
        mock = _mock_get(degraded)

        with (
            patch("bulletins.services.weather_fetcher.requests.get", mock),
            pytest.raises((TypeError, IntegrityError)),
        ):
            fetch_weather_for_point(point, datetime.date(2026, 5, 1), commit=True)

        assert mock.call_count == 1


class TestIsAlpinePoint:
    """Unit cases for the bounding-box predicate (no database needed)."""

    def test_inside_the_box(self) -> None:
        """A point well inside the box is in-domain."""
        assert _is_alpine_point(46.1, 7.4) is True

    def test_corners_are_inclusive(self) -> None:
        """The bounds themselves count as in-domain."""
        min_lat, max_lat, min_lon, max_lon = ICON_CH_BOUNDS
        assert _is_alpine_point(min_lat, min_lon) is True
        assert _is_alpine_point(max_lat, max_lon) is True

    def test_outside_on_each_axis(self) -> None:
        """A miss on either axis alone is enough to fall out of domain."""
        min_lat, max_lat, min_lon, max_lon = ICON_CH_BOUNDS
        assert _is_alpine_point(min_lat - 0.1, 7.4) is False
        assert _is_alpine_point(max_lat + 0.1, 7.4) is False
        assert _is_alpine_point(46.1, min_lon - 0.1) is False
        assert _is_alpine_point(46.1, max_lon + 0.1) is False

    def test_southern_hemisphere_is_out_of_domain(self) -> None:
        """Coordinates are (lat, lon) — a swapped pair must not read as Alpine."""
        assert _is_alpine_point(-41.3, 174.8) is False
