"""
tests/weather/services/test_backfill.py — Tests for the weather backfill.

The backfill is the one legitimate writer of past ``Weather`` rows, and it
reaches ``upsert_weather`` — which *raises* on an existing past row rather
than skipping it. That makes two things load-bearing enough to be tested
directly rather than inferred:

* **It never writes today.** Today is ``fetch_weather``'s row, and
  ``upsert_weather`` permits a rewrite of it — so an unguarded backfill
  would overwrite the live forecast with a stitched historical timeline
  and nothing would raise. Both guards get a test: the window ends at
  yesterday, and a provider date outside the requested set is skipped.
* **A re-run asks for nothing.** Idempotence here is the caller's, not
  ``upsert_weather``'s: the diff is what stops the second run raising.

Plus the shape of what gets written — ``forecast`` null on purpose, hourly
per day, freezing level derived — and the addressing: the history host, and
no ``apikey`` sent to it on the shipped free default.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest import mock

import pytest
import requests
from django.utils import timezone

from apps.weather.models import Weather
from apps.weather.services import backfill
from tests.factories import LocationFactory, WeatherFactory


def _days(start: datetime.date, count: int) -> list[datetime.date]:
    """Return ``count`` consecutive dates from ``start``."""
    return [start + datetime.timedelta(days=offset) for offset in range(count)]


def _daily(days: list[datetime.date], **overrides: Any) -> dict[str, Any]:
    """Build a complete, aligned ``daily`` block for ``days``."""
    count = len(days)
    block: dict[str, Any] = {
        "time": [d.isoformat() for d in days],
        "weather_code": [3] * count,
        "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
        "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
        "temperature_2m_max": [4.5] * count,
        "temperature_2m_min": [-2.0] * count,
        "snowfall_sum": [1.2] * count,
    }
    block.update(overrides)
    return block


def _hourly(days: list[datetime.date]) -> dict[str, Any]:
    """Build an ``hourly`` block with two hours per day in ``days``."""
    times = [f"{d.isoformat()}T{hour:02d}:00+02:00" for d in days for hour in (6, 12)]
    count = len(times)
    return {
        "time": times,
        "temperature_2m": [1.0] * count,
        "snowfall": [0.0] * count,
        "precipitation": [0.0] * count,
        "wind_speed_10m": [10.0] * count,
        "wind_gusts_10m": [20.0] * count,
        "wind_direction_10m": [270.0, 315.0] * len(days),
        "freezing_level_height": [2400.0, 2600.0] * len(days),
    }


def _payload(days: list[datetime.date], **overrides: Any) -> dict[str, Any]:
    """Build a whole response body covering ``days``."""
    return {"daily": _daily(days, **overrides), "hourly": _hourly(days)}


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _requested_days(call: Any) -> list[datetime.date]:
    """Return the day span one patched ``requests.get`` call asked for."""
    params = call.kwargs["params"]
    start = datetime.date.fromisoformat(params["start_date"])
    end = datetime.date.fromisoformat(params["end_date"])
    return _days(start, (end - start).days + 1)


class TestWindowArithmetic:
    """The bounds every other behaviour is derived from."""

    def test_until_is_yesterday(self) -> None:
        """The window ends the day before today, never on it."""
        assert backfill.backfill_until() == timezone.localdate() - datetime.timedelta(
            days=1
        )

    def test_expected_days_counts_the_window_inclusively(self) -> None:
        """A floor of yesterday over a window ending yesterday is one day."""
        yesterday = backfill.backfill_until()
        assert backfill.expected_days(yesterday, yesterday) == 1

    def test_expected_days_is_zero_for_an_inverted_window(self) -> None:
        """A floor after the end yields no days rather than a negative count."""
        today = timezone.localdate()
        assert backfill.expected_days(today, today - datetime.timedelta(days=3)) == 0


class TestGapWindows:
    """Contiguous runs become one request each."""

    def test_contiguous_days_collapse_to_one_window(self) -> None:
        """Five consecutive days are one request, not five."""
        days = _days(datetime.date(2026, 1, 1), 5)
        assert backfill.gap_windows(days) == [(days[0], days[-1])]

    def test_a_break_starts_a_new_window(self) -> None:
        """A missing day between two runs splits them."""
        first = _days(datetime.date(2026, 1, 1), 3)
        second = _days(datetime.date(2026, 1, 10), 2)
        assert backfill.gap_windows(first + second) == [
            (first[0], first[-1]),
            (second[0], second[-1]),
        ]

    def test_a_run_longer_than_the_cap_is_split(self) -> None:
        """MAX_RANGE_DAYS bounds one request even when the run is contiguous."""
        days = _days(datetime.date(2020, 1, 1), backfill.MAX_RANGE_DAYS + 5)
        windows = backfill.gap_windows(days)
        assert len(windows) == 2
        assert (windows[0][1] - windows[0][0]).days + 1 == backfill.MAX_RANGE_DAYS

    def test_no_dates_yields_no_windows(self) -> None:
        """A complete location asks for nothing."""
        assert backfill.gap_windows([]) == []


@pytest.mark.django_db
class TestMissingDates:
    """The diff that makes a re-run safe."""

    def test_a_location_with_no_rows_is_all_gap(self) -> None:
        """Every day in the window is missing when nothing was ever written."""
        location = LocationFactory.create()
        floor = backfill.backfill_until() - datetime.timedelta(days=2)
        until = backfill.backfill_until()
        assert backfill.missing_dates(location, floor, until) == _days(floor, 3)

    def test_existing_rows_are_subtracted(self) -> None:
        """A day already recorded is never requested again."""
        location = LocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        WeatherFactory.create(location=location, observed_on=floor)

        assert backfill.missing_dates(location, floor, until) == _days(
            floor + datetime.timedelta(days=1), 2
        )

    def test_another_locations_rows_do_not_count(self) -> None:
        """Coverage is per location; a sibling's history fills no gap here."""
        location = LocationFactory.create()
        other = LocationFactory.create()
        until = backfill.backfill_until()
        WeatherFactory.create(location=other, observed_on=until)

        assert backfill.missing_dates(location, until, until) == [until]


@pytest.mark.django_db
class TestNeverWritesToday:
    """The one destructive failure mode — today is fetch_weather's row."""

    def test_a_caller_asking_for_today_is_clamped_to_yesterday(self) -> None:
        """``until`` past yesterday is clamped, so today is never requested."""
        location = LocationFactory.create()
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        with mock.patch(
            "requests.get", return_value=_response(_payload([yesterday]))
        ) as get:
            backfill.backfill_location(
                location, floor=yesterday, until=today, commit=True
            )

        assert _requested_days(get.call_args) == [yesterday]
        assert not Weather.objects.filter(location=location, observed_on=today).exists()

    def test_a_provider_day_outside_the_request_is_skipped(self) -> None:
        """A response carrying today is dropped rather than overwriting the live row.

        The window guard alone would not catch this: the request asked for
        yesterday, and it is the write loop that has to refuse the extra day.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)
        live = WeatherFactory.create(
            location=location, observed_on=today, weather_code=0
        )

        # The provider returns one day more than was asked for.
        payload = _payload([yesterday, today])
        with mock.patch("requests.get", return_value=_response(payload)):
            result = backfill.backfill_location(
                location, floor=yesterday, until=yesterday, commit=True
            )

        assert result.filled == 1
        live.refresh_from_db()
        assert live.weather_code == 0


@pytest.mark.django_db
class TestBackfillLocation:
    """One location, end to end."""

    def test_a_gap_is_filled_with_one_row_per_day(self) -> None:
        """Three missing days become three rows from one request."""
        location = LocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        days = _days(floor, 3)

        with mock.patch("requests.get", return_value=_response(_payload(days))) as get:
            result = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert get.call_count == 1
        assert result.filled == 3
        assert set(
            Weather.objects.filter(location=location).values_list(
                "observed_on", flat=True
            )
        ) == set(days)

    def test_a_backfilled_row_carries_no_forecast(self) -> None:
        """``forecast`` stays null: a stitched timeline is not an outlook.

        The visible consequence is that a historical page renders no outlook
        chart, which is correct — see the module docstring.
        """
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = Weather.objects.get(location=location, observed_on=day)
        assert row.forecast is None

    def test_a_backfilled_row_carries_its_hourly_series(self) -> None:
        """The meteogram's data is written, one series per day."""
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = Weather.objects.get(location=location, observed_on=day)
        assert row.hourly is not None
        assert len(row.hourly) == 2

    def test_freezing_level_is_derived_from_the_hourly_maximum(self) -> None:
        """Open-Meteo publishes no daily aggregate, so the day takes its peak."""
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = Weather.objects.get(location=location, observed_on=day)
        assert row.freezing_level_height == 2600.0

    def test_an_unresolved_day_is_dropped_not_raised_on(self) -> None:
        """A null weather_code costs that day its row and nothing else (SNOW-628)."""
        location = LocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=1)
        days = _days(floor, 2)
        payload = _payload(days, weather_code=[3, None])

        with mock.patch("requests.get", return_value=_response(payload)):
            result = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert result.filled == 1
        assert result.unresolved == 1
        assert Weather.objects.filter(location=location).count() == 1

    def test_a_complete_location_issues_no_request(self) -> None:
        """No gap, no call — and no raise from upsert_weather on the past row."""
        location = LocationFactory.create()
        day = backfill.backfill_until()
        WeatherFactory.create(location=location, observed_on=day)

        with mock.patch("requests.get") as get:
            result = backfill.backfill_location(
                location, floor=day, until=day, commit=True
            )

        get.assert_not_called()
        assert result.requests == 0
        assert result.already_present == 1

    def test_a_second_run_over_a_filled_range_is_a_no_op(self) -> None:
        """Idempotence is the caller's: the diff is what stops the second raise."""
        location = LocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        days = _days(floor, 3)

        with mock.patch("requests.get", return_value=_response(_payload(days))) as get:
            backfill.backfill_location(location, floor=floor, until=until, commit=True)
            second = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert get.call_count == 1
        assert second.filled == 0
        assert second.already_present == 3

    def test_read_only_calls_the_api_but_writes_nothing(self) -> None:
        """Without --commit the probe is real; the database is untouched."""
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            result = backfill.backfill_location(
                location, floor=day, until=day, commit=False
            )

        assert get.call_count == 1
        assert result.filled == 0
        assert not Weather.objects.filter(location=location).exists()


@pytest.mark.django_db
class TestAddressing:
    """Which host is called, and what is sent with the request."""

    def test_the_request_goes_to_the_history_host(self) -> None:
        """The backfill addresses the historical forecast API, not the live one."""
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert get.call_args.args[0] == (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
        )

    def test_no_api_key_is_sent_to_the_free_history_host(self, settings: Any) -> None:
        """A key set for the customer tier is not leaked to the public host.

        The history host ships on its free default, so it is outside the
        customer set even when a key is configured (SNOW-579).
        """
        settings.OPEN_METEO_API_KEY = "secret"
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert "apikey" not in get.call_args.kwargs["params"]

    def test_elevation_is_sent_when_the_location_has_one(self) -> None:
        """A resolved height downscales the day to the point, as the live fetch does."""
        location = LocationFactory.create(elevation_m=1494)
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert get.call_args.kwargs["params"]["elevation"] == "1494"

    def test_elevation_is_omitted_when_unresolved(self) -> None:
        """A location with no height is still fetched — at the cell's altitude."""
        location = LocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert "elevation" not in get.call_args.kwargs["params"]


@pytest.mark.django_db
class TestBackfillLocations:
    """The walk — one failure must not cost the rest their history."""

    def test_a_failing_location_is_counted_not_raised(self) -> None:
        """An HTTP error on one location is caught; the walk continues."""
        bad = LocationFactory.create(name="Bad")
        good = LocationFactory.create(name="Good")
        day = backfill.backfill_until()

        responses = [requests.HTTPError("boom"), _response(_payload([day]))]
        with mock.patch("requests.get", side_effect=responses):
            counts = backfill.backfill_locations(
                [bad, good], floor=day, until=day, commit=True, delay=0
            )

        assert counts["failed"] == 1
        assert counts["filled"] == 1
        assert Weather.objects.filter(location=good).count() == 1

    def test_the_throttle_sleeps_between_locations_but_not_before_the_first(
        self,
    ) -> None:
        """Two locations mean one wait, injected so the suite does not pay it."""
        locations = [LocationFactory.create() for _ in range(3)]
        day = backfill.backfill_until()
        slept: list[float] = []

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_locations(
                locations,
                floor=day,
                until=day,
                commit=True,
                delay=2.5,
                sleep=slept.append,
            )

        assert slept == [2.5, 2.5]

    def test_a_zero_delay_disables_the_throttle(self) -> None:
        """--delay 0 is an explicit opt-out, not a floor."""
        locations = [LocationFactory.create() for _ in range(2)]
        day = backfill.backfill_until()
        slept: list[float] = []

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_locations(
                locations,
                floor=day,
                until=day,
                commit=True,
                delay=0,
                sleep=slept.append,
            )

        assert slept == []
