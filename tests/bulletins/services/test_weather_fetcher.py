"""
tests/bulletins/services/test_weather_fetcher.py — Tests for the weather_fetcher service.

Covers:
  - _parse_dt: ISO-8601 parsing (tz-aware and naive inputs).
  - fetch_weather_for_region: happy path, commit=False (no DB write), HTTP error.
  - fetch_all_regions: creates/skips/fails correctly, returns accurate counters.
  - fetch_archive_for_region: happy path multi-day, commit=False, HTTP error.
  - backfill_all_regions: creates/updates correctly, per-region failure isolation.

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
    fetch_weather_for_region,
)
from tests.factories import RegionFactory, WeatherSnapshotFactory

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
        region = RegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=True)

        assert result is not None
        assert result.region == region
        assert result.valid_for_date == target
        assert result.weather_code == 1
        assert WeatherSnapshot.objects.filter(
            region=region, valid_for_date=target
        ).exists()

    def test_commit_false_returns_none(self) -> None:
        """commit=False calls the API but does not write to the database."""
        region = RegionFactory.create()
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
        region = RegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        mock.assert_called_once()

    def test_upsert_updates_existing_snapshot(self) -> None:
        """A second call updates the existing WeatherSnapshot row."""
        region = RegionFactory.create()
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
        assert result.weather_code == 3
        # Still only one row.
        assert (
            WeatherSnapshot.objects.filter(region=region, valid_for_date=target).count()
            == 1
        )

    def test_http_error_raises(self) -> None:
        """requests.HTTPError propagates to the caller."""
        region = RegionFactory.create()
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
        region = RegionFactory.create()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()

        with patch(
            "bulletins.services.weather_fetcher.requests.get",
            _mock_get(api_data),
        ):
            result = fetch_weather_for_region(region, target, commit=True)

        assert result is not None
        assert result.sunrise.tzinfo is not None
        assert result.sunset.tzinfo is not None

    def test_correct_lat_lon_passed_to_api(self) -> None:
        """The region's centre lat/lon are forwarded to the Open-Meteo API."""
        region = RegionFactory.create()
        region.centre = {"lon": 8.1, "lat": 47.2}
        region.save()
        target = datetime.date(2026, 5, 1)
        api_data = _make_forecast_response()
        mock = _mock_get(api_data)

        with patch("bulletins.services.weather_fetcher.requests.get", mock):
            fetch_weather_for_region(region, target, commit=False)

        call_kwargs = mock.call_args
        params = call_kwargs[1]["params"]
        assert params["latitude"] == 47.2
        assert params["longitude"] == 8.1


# ---------------------------------------------------------------------------
# fetch_all_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchAllRegions:
    """Tests for fetch_all_regions."""

    def test_returns_correct_counters_on_success(self) -> None:
        """fetch_all_regions returns created/updated/failed/skipped counts."""
        region = RegionFactory.create()
        assert region.centre is not None  # RegionFactory default has centre
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
        region = RegionFactory.create()
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
        RegionFactory.create()
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
        region = RegionFactory.create()
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
        RegionFactory.create()
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
        region = RegionFactory.create()
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

        assert len(result) == 3
        assert WeatherSnapshot.objects.filter(region=region).count() == 3
        codes = list(
            WeatherSnapshot.objects.filter(region=region)
            .order_by("valid_for_date")
            .values_list("weather_code", flat=True)
        )
        assert codes == [0, 1, 2]

    def test_commit_false_returns_empty_list_and_no_db(self) -> None:
        """commit=False makes the API call but writes nothing and returns []."""
        region = RegionFactory.create()
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
        region = RegionFactory.create()
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
        region = RegionFactory.create()
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
        assert result[0].weather_code == 5
        assert WeatherSnapshot.objects.filter(region=region).count() == 1

    def test_correct_date_range_params_passed_to_api(self) -> None:
        """start_date and end_date are forwarded to Open-Meteo as ISO strings."""
        region = RegionFactory.create()
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
        RegionFactory.create()
        RegionFactory.create()
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
        region = RegionFactory.create()
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
        RegionFactory.create()
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
        RegionFactory.create()
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
        region = RegionFactory.create()
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
