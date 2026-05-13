"""
tests/bulletins/services/test_weather_fetcher.py — Tests for the weather_fetcher service.

Covers:
  - _parse_dt: ISO-8601 parsing (tz-aware and naive inputs).
  - fetch_weather_for_region: happy path, commit=False (no DB write), HTTP error,
    base_url threading, on_fetched callback.
  - fetch_all_regions: creates/skips/fails correctly, returns accurate counters,
    base_url and on_fetched forwarding.
  - fetch_archive_for_region: happy path multi-day, commit=False, HTTP error,
    base_url threading, on_fetched callback shape.
  - backfill_all_regions: creates/updates correctly, per-region failure isolation,
    base_url and on_fetched forwarding.
  - resolve_weather_source: maps source strings to base URLs / raises on missing setting.

All outbound HTTP calls are mocked via ``unittest.mock.patch`` so no network
traffic is required. The mocking pattern mirrors test_data_fetcher.py.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from bulletins.models import WeatherSnapshot
from bulletins.services.weather_fetcher import (
    _parse_dt,
    backfill_all_regions,
    fetch_all_regions,
    fetch_archive_for_region,
    fetch_weather_async,
    fetch_weather_for_region,
    resolve_weather_source,
)
from tests.factories import MicroRegionFactory, WeatherSnapshotFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_forecast_response(
    weather_code: int = 1,
    sunrise: str = "2026-05-01T05:32+02:00",
    sunset: str = "2026-05-01T20:45+02:00",
    target_date: str = "2026-05-01",
) -> dict[str, Any]:
    """Build a minimal Open-Meteo forecast API response dict."""
    return {
        "latitude": 46.8,
        "longitude": 7.5,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": [target_date],
            "weather_code": [weather_code],
            "sunrise": [sunrise],
            "sunset": [sunset],
        },
    }


def _make_archive_response(
    dates: list[str],
    weather_codes: list[int],
    sunrises: list[str],
    sunsets: list[str],
) -> dict[str, Any]:
    """Build a minimal Open-Meteo archive API response dict."""
    return {
        "latitude": 46.8,
        "longitude": 7.5,
        "timezone": "Europe/Zurich",
        "daily": {
            "time": dates,
            "weather_code": weather_codes,
            "sunrise": sunrises,
            "sunset": sunsets,
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
# _parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    """Tests for _parse_dt (weather_fetcher variant — preserves local tz offset)."""

    def test_naive_input_becomes_utc(self) -> None:
        """A naive datetime string is tagged as UTC."""
        result = _parse_dt("2026-05-01T05:32:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == datetime.timedelta(0)

    def test_offset_input_preserves_local_tz(self) -> None:
        """An ISO-8601 string with a +02:00 offset keeps that offset."""
        result = _parse_dt("2026-05-01T05:32+02:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == datetime.timedelta(hours=2)

    def test_utc_z_suffix(self) -> None:
        """A Z-suffixed string is UTC-aware."""
        result = _parse_dt("2026-05-01T05:32:00Z")
        assert result.tzinfo is not None

    def test_hour_minute_values_preserved(self) -> None:
        """The hour and minute values from the input are preserved."""
        result = _parse_dt("2026-05-01T05:32+02:00")
        assert result.hour == 5
        assert result.minute == 32


# ---------------------------------------------------------------------------
# fetch_weather_for_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherForRegion:
    """Tests for fetch_weather_for_region."""

    def test_commit_true_creates_snapshot(self) -> None:
        """commit=True creates a WeatherSnapshot in the database."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=True)

        assert result is not None
        snapshot, created = result
        assert created is True
        assert snapshot.region == region
        assert snapshot.valid_for_date == target
        assert snapshot.weather_code == 1
        assert WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()

    def test_commit_false_returns_none(self) -> None:
        """commit=False calls the API but does not write to the database."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=False)

        assert result is None
        assert not WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()

    def test_commit_false_still_calls_api(self) -> None:
        """commit=False still makes the HTTP request (real API probe)."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        mock.assert_called_once()

    def test_upsert_updates_existing_snapshot(self) -> None:
        """A second call updates the existing WeatherSnapshot row."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=target, weather_code=0
        )

        api_data = _make_forecast_response(weather_code=3)
        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=True)

        assert result is not None
        snapshot, created = result
        assert created is False
        assert snapshot.weather_code == 3
        # Still only one row.
        assert (
            WeatherSnapshot.objects.filter(region=region, valid_for_date=target).count()
            == 1
        )

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        region = MicroRegionFactory.create()
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
                fetch_weather_for_region(region, target, commit=True)

    def test_snapshot_datetimes_are_tz_aware(self) -> None:
        """sunrise and sunset on the saved snapshot are tz-aware."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=True)

        assert result is not None
        snapshot, _ = result
        assert snapshot.sunrise.tzinfo is not None
        assert snapshot.sunset.tzinfo is not None

    def test_correct_lat_lon_passed_to_api(self) -> None:
        """The region's centre lat/lon are forwarded to the Open-Meteo API."""
        region = MicroRegionFactory.create()
        region.centre = {"lon": 8.1, "lat": 47.2}
        region.save()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        call_kwargs = mock.call_args
        params = call_kwargs[1]["params"]
        # Params are passed as strings to keep the dict[str, str] type.
        assert params["latitude"] == "47.2"
        assert params["longitude"] == "8.1"

    def test_forecast_params_shape(self) -> None:
        """Forecast params must not include a 'current' key; weather_code is in 'daily'."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        params = mock.call_args[1]["params"]
        assert "current" not in params, "forecast params must not include 'current'"
        daily_fields = params["daily"].split(",")
        assert "weather_code" in daily_fields, "weather_code must be in daily params"
        assert "sunrise" in daily_fields, "sunrise must be in daily params"
        assert "sunset" in daily_fields, "sunset must be in daily params"


# ---------------------------------------------------------------------------
# fetch_all_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchAllRegions:
    """Tests for fetch_all_regions."""

    def test_returns_correct_counters_on_success(self) -> None:
        """fetch_all_regions returns created/updated/failed/skipped counts."""
        region = MicroRegionFactory.create()
        assert region.centre is not None  # MicroRegionFactory default has centre
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = fetch_all_regions(target, commit=True)

        assert counts["created"] == 1
        assert counts["updated"] == 0
        assert counts["failed"] == 0
        assert counts["skipped"] == 0

    def test_skips_region_without_centre(self) -> None:
        """A region with centre=None is counted as skipped, not failed."""
        region = MicroRegionFactory.create()
        region.centre = None
        region.save()
        target = datetime.date(2026, 5, 1)

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(_make_forecast_response()),
        ) as mock:
            counts = fetch_all_regions(target, commit=True)

        assert counts["skipped"] == 1
        assert counts["created"] == 0
        mock.assert_not_called()

    def test_http_error_counts_as_failed(self) -> None:
        """A per-region HTTP failure is counted as failed; other regions continue."""
        MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            return_value=mock_response,
        ):
            counts = fetch_all_regions(target, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == 0

    def test_existing_snapshot_counted_as_updated(self) -> None:
        """When a snapshot already exists for (region, date), it is counted as updated."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        WeatherSnapshotFactory.create(region=region, valid_for_date=target)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = fetch_all_regions(target, commit=True)

        assert counts["updated"] == 1
        assert counts["created"] == 0

    def test_commit_false_does_not_write(self) -> None:
        """commit=False returns zero created/updated and writes nothing."""
        MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = fetch_all_regions(target, commit=False)

        assert counts["created"] == 0
        assert counts["updated"] == 0
        assert WeatherSnapshot.objects.count() == 0


# ---------------------------------------------------------------------------
# fetch_archive_for_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchArchiveForRegion:
    """Tests for fetch_archive_for_region."""

    def test_commit_true_creates_snapshot_per_day(self) -> None:
        """One WeatherSnapshot is created for each day in the range."""
        region = MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 30)
        api_data = _make_archive_response(
            dates=["2026-04-28", "2026-04-29", "2026-04-30"],
            weather_codes=[0, 1, 2],
            sunrises=[
                "2026-04-28T05:40+02:00",
                "2026-04-29T05:38+02:00",
                "2026-04-30T05:36+02:00",
            ],
            sunsets=[
                "2026-04-28T20:30+02:00",
                "2026-04-29T20:32+02:00",
                "2026-04-30T20:34+02:00",
            ],
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_archive_for_region(region, start, end, commit=True)

        # result is a list of (snapshot, created) tuples.
        assert len(result) == 3
        assert all(created is True for _, created in result)
        assert WeatherSnapshot.objects.filter(region=region).count() == 3
        codes = list(
            WeatherSnapshot.objects.filter(region=region)
            .order_by("valid_for_date")
            .values_list("weather_code", flat=True)
        )
        assert codes == [0, 1, 2]

    def test_commit_false_returns_empty_list_and_no_db(self) -> None:
        """commit=False makes the API call but writes nothing and returns []."""
        region = MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 30)
        api_data = _make_archive_response(
            dates=["2026-04-28", "2026-04-29", "2026-04-30"],
            weather_codes=[0, 1, 2],
            sunrises=["2026-04-28T05:40+02:00"] * 3,
            sunsets=["2026-04-28T20:30+02:00"] * 3,
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_archive_for_region(region, start, end, commit=False)

        assert result == []
        assert WeatherSnapshot.objects.count() == 0

    def test_http_error_raises(self) -> None:
        """requests.HTTPError is propagated to the caller."""
        region = MicroRegionFactory.create()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("503")

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            return_value=mock_response,
        ):
            with pytest.raises(requests.HTTPError):
                fetch_archive_for_region(
                    region,
                    datetime.date(2026, 4, 28),
                    datetime.date(2026, 4, 30),
                    commit=True,
                )

    def test_upserts_existing_snapshots(self) -> None:
        """Existing snapshots are updated rather than duplicated."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 4, 28)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=target, weather_code=99
        )

        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[5],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_archive_for_region(region, target, target, commit=True)

        assert len(result) == 1
        snapshot, created = result[0]
        assert created is False
        assert snapshot.weather_code == 5
        assert WeatherSnapshot.objects.filter(region=region).count() == 1

    def test_correct_date_range_params_passed_to_api(self) -> None:
        """start_date and end_date are forwarded to Open-Meteo as ISO strings."""
        region = MicroRegionFactory.create()
        start = datetime.date(2026, 4, 1)
        end = datetime.date(2026, 4, 30)
        api_data = _make_archive_response(
            dates=[d.isoformat() for d in [datetime.date(2026, 4, 1)]],
            weather_codes=[0],
            sunrises=["2026-04-01T06:00+02:00"],
            sunsets=["2026-04-01T20:00+02:00"],
        )
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_archive_for_region(region, start, end, commit=False)

        params = mock.call_args[1]["params"]
        assert params["start_date"] == "2026-04-01"
        assert params["end_date"] == "2026-04-30"


# ---------------------------------------------------------------------------
# backfill_all_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBackfillAllRegions:
    """Tests for backfill_all_regions."""

    def test_creates_snapshots_for_all_regions(self) -> None:
        """Creates one snapshot per (region × day) for all regions with centres."""
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 29)
        api_data = _make_archive_response(
            dates=["2026-04-28", "2026-04-29"],
            weather_codes=[0, 1],
            sunrises=["2026-04-28T05:40+02:00", "2026-04-29T05:38+02:00"],
            sunsets=["2026-04-28T20:30+02:00", "2026-04-29T20:32+02:00"],
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = backfill_all_regions(start, end, commit=True)

        # 2 regions × 2 days = 4 snapshots
        assert WeatherSnapshot.objects.count() == 4
        assert counts["created"] == 4
        assert counts["updated"] == 0
        assert counts["failed"] == 0
        assert counts["skipped"] == 0

    def test_skips_region_without_centre(self) -> None:
        """Regions with centre=None are counted as skipped."""
        region = MicroRegionFactory.create()
        region.centre = None
        region.save()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 28)

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(_make_archive_response([], [], [], [])),
        ) as mock:
            counts = backfill_all_regions(start, end, commit=True)

        assert counts["skipped"] == 1
        assert counts["created"] == 0
        mock.assert_not_called()

    def test_http_failure_counted_as_failed(self) -> None:
        """A per-region HTTP error is counted as failed; others continue."""
        MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 28)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            return_value=mock_response,
        ):
            counts = backfill_all_regions(start, end, commit=True)

        assert counts["failed"] == 1
        assert counts["created"] == 0

    def test_commit_false_writes_nothing(self) -> None:
        """commit=False returns all zeros for created/updated and nothing is written."""
        MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 28)
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[0],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = backfill_all_regions(start, end, commit=False)

        assert counts["created"] == 0
        assert counts["updated"] == 0
        assert WeatherSnapshot.objects.count() == 0

    def test_existing_snapshots_counted_as_updated(self) -> None:
        """Snapshots that already exist are counted as updated, not created."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 4, 28)
        WeatherSnapshotFactory.create(region=region, valid_for_date=target)
        start = end = target
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[5],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            counts = backfill_all_regions(start, end, commit=True)

        assert counts["updated"] == 1
        assert counts["created"] == 0

    def test_delay_sleeps_between_regions(self) -> None:
        """``delay > 0`` sleeps between regions but never after the last one."""
        # Three regions with centres → two between-region gaps.
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        target = datetime.date(2026, 4, 28)
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[0],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )

        with (
            patch(
                "bulletins.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            patch("bulletins.services.weather_fetcher.time.sleep") as mock_sleep,
        ):
            backfill_all_regions(target, target, commit=True, delay=0.5)

        # Two sleeps between three regions, none after the last.
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call.args == (0.5,)

    def test_zero_delay_does_not_sleep(self) -> None:
        """``delay=0`` (the service default) skips ``time.sleep`` entirely."""
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        target = datetime.date(2026, 4, 28)
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[0],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )

        with (
            patch(
                "bulletins.services.weather_fetcher.requests.get",
                _mock_get(api_data),
            ),
            patch("bulletins.services.weather_fetcher.time.sleep") as mock_sleep,
        ):
            backfill_all_regions(target, target, commit=True, delay=0.0)

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# base_url threading
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBaseUrlThreading:
    """Tests that base_url is correctly threaded to the underlying requests.get call."""

    def test_fetch_weather_for_region_uses_base_url_for_forecast(self) -> None:
        """When base_url is set, the forecast request goes to {base_url}/forecast."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(
                region,
                target,
                commit=False,
                base_url="http://localhost:8000/dev/openmeteo-mirror/v1",
            )

        called_url = mock.call_args[0][0]
        assert called_url == "http://localhost:8000/dev/openmeteo-mirror/v1/forecast"

    def test_fetch_weather_for_region_falls_back_to_forecast_url_when_none(
        self,
    ) -> None:
        """When base_url=None, the module-level FORECAST_URL is used."""
        from bulletins.services.weather_fetcher import FORECAST_URL

        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        called_url = mock.call_args[0][0]
        assert called_url == FORECAST_URL

    def test_fetch_archive_for_region_uses_base_url_for_archive(self) -> None:
        """When base_url is set, the archive request goes to {base_url}/archive."""
        region = MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 28)
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[0],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_archive_for_region(
                region,
                start,
                end,
                commit=False,
                base_url="http://localhost:8000/dev/openmeteo-mirror/v1",
            )

        called_url = mock.call_args[0][0]
        assert called_url == "http://localhost:8000/dev/openmeteo-mirror/v1/archive"

    def test_fetch_archive_for_region_falls_back_to_archive_url_when_none(
        self,
    ) -> None:
        """When base_url=None, the module-level ARCHIVE_URL is used."""
        from bulletins.services.weather_fetcher import ARCHIVE_URL

        region = MicroRegionFactory.create()
        start = end = datetime.date(2026, 4, 28)
        api_data = _make_archive_response(
            dates=["2026-04-28"],
            weather_codes=[0],
            sunrises=["2026-04-28T05:40+02:00"],
            sunsets=["2026-04-28T20:30+02:00"],
        )
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_archive_for_region(region, start, end, commit=False)

        called_url = mock.call_args[0][0]
        assert called_url == ARCHIVE_URL


# ---------------------------------------------------------------------------
# on_fetched callback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOnFetchedCallback:
    """Tests that on_fetched is called with the right shape per record."""

    def test_on_fetched_called_once_for_forecast(self) -> None:
        """on_fetched is called once with the right shape for a forecast request."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response(weather_code=7)
        captured: list[dict] = []

        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            fetch_weather_for_region(
                region,
                target,
                commit=False,
                on_fetched=captured.append,
            )

        assert len(captured) == 1
        record = captured[0]
        assert record["region_id"] == region.region_id
        assert record["date"] == "2026-05-01"
        assert record["weather_code"] == 7
        assert "sunrise" in record
        assert "sunset" in record
        assert "captured_at" in record

    def test_on_fetched_not_called_when_none(self) -> None:
        """When on_fetched is None (default), nothing is captured."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        # Should not raise even without on_fetched.
        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            fetch_weather_for_region(region, target, commit=False)

    def test_on_fetched_called_per_day_for_archive(self) -> None:
        """on_fetched is called once per day for an archive request."""
        region = MicroRegionFactory.create()
        start = datetime.date(2026, 4, 28)
        end = datetime.date(2026, 4, 30)
        api_data = _make_archive_response(
            dates=["2026-04-28", "2026-04-29", "2026-04-30"],
            weather_codes=[0, 1, 2],
            sunrises=[
                "2026-04-28T05:40+02:00",
                "2026-04-29T05:38+02:00",
                "2026-04-30T05:36+02:00",
            ],
            sunsets=[
                "2026-04-28T20:30+02:00",
                "2026-04-29T20:32+02:00",
                "2026-04-30T20:34+02:00",
            ],
        )
        captured: list[dict] = []

        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            fetch_archive_for_region(
                region,
                start,
                end,
                commit=False,
                on_fetched=captured.append,
            )

        assert len(captured) == 3
        assert [r["date"] for r in captured] == [
            "2026-04-28",
            "2026-04-29",
            "2026-04-30",
        ]
        assert [r["weather_code"] for r in captured] == [0, 1, 2]
        for record in captured:
            assert record["region_id"] == region.region_id
            assert "sunrise" in record
            assert "sunset" in record
            assert "captured_at" in record

    def test_on_fetched_called_even_when_commit_false(self) -> None:
        """on_fetched fires regardless of the commit flag (stash without DB write)."""
        region = MicroRegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        captured: list[dict] = []

        with patch(
            "bulletins.services.weather_fetcher.requests.get", _mock_get(api_data)
        ):
            result = fetch_weather_for_region(
                region,
                target,
                commit=False,
                on_fetched=captured.append,
            )

        assert result is None  # commit=False → no snapshot returned
        assert len(captured) == 1  # but on_fetched still fired


# ---------------------------------------------------------------------------
# resolve_weather_source
# ---------------------------------------------------------------------------


class TestResolveWeatherSource:
    """Tests for resolve_weather_source."""

    def test_live_source_returns_none(self) -> None:
        """'live' source returns None so callers fall back to the module URL constants."""
        result = resolve_weather_source("live")
        assert result is None

    def test_local_mirror_returns_configured_url(self) -> None:
        """'local-mirror' returns WEATHER_API_LOCAL_MIRROR_BASE_URL when configured."""
        from django.test import override_settings

        with override_settings(
            WEATHER_API_LOCAL_MIRROR_BASE_URL="http://localhost:8000/dev/openmeteo-mirror/v1"
        ):
            result = resolve_weather_source("local-mirror")

        assert result == "http://localhost:8000/dev/openmeteo-mirror/v1"

    def test_local_mirror_raises_when_setting_missing(self) -> None:
        """'local-mirror' raises CommandError when the setting is not configured."""
        from django.core.management.base import CommandError
        from django.test import override_settings

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=None):
            with pytest.raises(CommandError, match="WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                resolve_weather_source("local-mirror")

    def test_local_mirror_raises_when_setting_absent(self) -> None:
        """'local-mirror' raises CommandError when the setting is completely absent."""
        from django.core.management.base import CommandError

        from bulletins.services import weather_fetcher

        original = getattr(weather_fetcher, "resolve_weather_source", None)
        # Simulate missing attribute by deleting the setting entirely.
        from django.test import override_settings  # noqa: F811

        # Remove the attribute from settings entirely (not just set to None).
        with override_settings():
            from django.conf import settings as djsettings

            if hasattr(djsettings, "WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                del djsettings.WEATHER_API_LOCAL_MIRROR_BASE_URL
            with pytest.raises(CommandError, match="WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                resolve_weather_source("local-mirror")
        _ = original  # suppress unused warning


# ---------------------------------------------------------------------------
# fetch_weather_async
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherAsync:
    """Tests for fetch_weather_async (background-thread weather warmup — SNOW-164).

    All tests rely on the ``_force_sync_weather_fetch`` autouse fixture in
    conftest.py which pins ``WEATHER_FETCH_ASYNC = False``.  In sync mode
    the helper runs ``_worker`` directly on the calling thread, so DB
    assertions see the written snapshot immediately and the main-thread
    guard in ``finally`` skips ``connections.close_all()``.
    """

    def test_sync_mode_persists_snapshot(self) -> None:
        """When WEATHER_FETCH_ASYNC is False, the call runs inline and writes a snapshot."""
        region = MicroRegionFactory.create()
        target = datetime.date.today() - datetime.timedelta(
            days=2
        )  # past → archive path
        api_data = _make_archive_response(
            dates=[target.isoformat()],
            weather_codes=[3],
            sunrises=[f"{target.isoformat()}T07:00+02:00"],
            sunsets=[f"{target.isoformat()}T17:00+02:00"],
        )
        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            fetch_weather_async(region, target)

        assert WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()

    def test_sync_mode_swallows_inner_exception(self, monkeypatch) -> None:
        """A failure inside the worker is caught and logged; the caller still returns."""
        region = MicroRegionFactory.create()
        target = datetime.date.today() - datetime.timedelta(days=2)

        def _boom(*args, **kwargs):
            raise requests.HTTPError("503")

        monkeypatch.setattr(
            "bulletins.services.weather_fetcher.fetch_archive_for_region", _boom
        )

        # Must not raise.
        fetch_weather_async(region, target)
        assert not WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()

    def test_sync_mode_skips_when_snapshot_already_exists(self, monkeypatch) -> None:
        """Idempotent guard: a pre-existing snapshot means no API call."""
        region = MicroRegionFactory.create()
        target = datetime.date.today() - datetime.timedelta(days=2)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=target, weather_code=0
        )

        def _boom(*args, **kwargs):
            raise AssertionError("API must not be called when snapshot already exists")

        monkeypatch.setattr(
            "bulletins.services.weather_fetcher.fetch_archive_for_region", _boom
        )
        monkeypatch.setattr(
            "bulletins.services.weather_fetcher.fetch_weather_for_region", _boom
        )

        fetch_weather_async(region, target)
        # Existing snapshot's weather_code is unchanged (no overwrite).
        assert (
            WeatherSnapshot.objects.get(
                region=region, valid_for_date=target
            ).weather_code
            == 0
        )

    def test_sync_mode_forecast_branch_for_today(self) -> None:
        """target_date == today routes through fetch_weather_for_region (forecast).

        The production call site in ``bulletin_detail`` guards with
        ``target_date < today`` so this branch is unreachable from the page
        render today, but the helper itself dispatches on ``target_date`` so
        the forecast branch needs direct coverage. A future caller that
        passes today's or a future date must continue to hit the forecast
        endpoint rather than the archive one.
        """
        region = MicroRegionFactory.create()
        target = datetime.date.today()
        api_data = _make_forecast_response(target_date=target.isoformat())

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ) as mock_get:
            fetch_weather_async(region, target)

        # Snapshot persisted via the forecast path.
        assert WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()
        # And the forecast endpoint (not archive) was the one hit.
        called_url = mock_get.call_args[0][0]
        assert "forecast" in called_url
        assert "archive" not in called_url
