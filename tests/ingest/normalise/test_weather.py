"""
tests/ingest/normalise/test_weather.py — Tests for apps.ingest.normalise.weather.

The Open-Meteo parsing moved here from apps/weather/services/fetch.py
(SNOW-1248). tests/weather/services/test_fetch.py still drives it end to end
through ``fetch_weather_for_location``; these tests pin each function on its
own, against hand-built ``daily`` / ``hourly`` blocks, with no location and no
database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from apps.ingest.normalise.weather import (
    DAILY_VARIABLES,
    HOURLY_VARIABLES,
    daily_dates,
    daily_fields,
    daily_max_freezing_level,
    day_is_complete,
    forecast_entry,
    hourly_rows_for_day,
    parse_dt_preserve_offset,
)

DAY_ONE = date(2026, 1, 10)
DAY_TWO = date(2026, 1, 11)


def _daily(**overrides: Any) -> dict[str, Any]:
    """Return a two-day ``daily`` block, every required field populated."""
    block: dict[str, Any] = {
        "time": ["2026-01-10", "2026-01-11"],
        "weather_code": [71, 3],
        "sunrise": ["2026-01-10T08:05+01:00", "2026-01-11T08:05+01:00"],
        "sunset": ["2026-01-10T16:55+01:00", "2026-01-11T16:56+01:00"],
        "temperature_2m_max": [-2.0, 1.5],
        "snowfall_sum": [12.0],
    }
    block.update(overrides)
    return block


def _hourly() -> dict[str, Any]:
    """Return an ``hourly`` block of two hours on each of the two days."""
    return {
        "time": [
            "2026-01-10T09:00+01:00",
            "2026-01-10T10:00+01:00",
            "2026-01-11T09:00+01:00",
            "2026-01-11T10:00+01:00",
        ],
        "temperature_2m": [-5.0, -4.0, 0.0, 1.0],
        "freezing_level_height": [900.0, 1200.0, None, None],
    }


class TestVariables:
    """The request's variable lists."""

    def test_the_hourly_list_names_every_freezing_level_source(self) -> None:
        """The daily freezing level is derived from the hourly block."""
        assert "freezing_level_height" in HOURLY_VARIABLES.split(",")

    def test_the_daily_list_starts_with_the_required_fields(self) -> None:
        """A day missing one of these is dropped, so all three are asked for."""
        assert DAILY_VARIABLES.split(",")[:3] == ["weather_code", "sunrise", "sunset"]


class TestParseDtPreserveOffset:
    """parse_dt_preserve_offset."""

    def test_keeps_the_local_offset(self) -> None:
        """The hour stays as it is experienced at the location."""
        parsed = parse_dt_preserve_offset("2026-01-10T08:05+01:00")
        assert parsed.utcoffset() == timedelta(hours=1)
        assert parsed.hour == 8

    def test_naive_input_is_utc(self) -> None:
        """A string with no offset is read as UTC."""
        assert parse_dt_preserve_offset("2026-01-10T08:05").tzinfo == UTC


class TestDailyDates:
    """daily_dates — the provider's own dates key every row (SNOW-466)."""

    def test_returns_the_provider_dates(self) -> None:
        """The dates are read, not derived by offset."""
        assert daily_dates(_daily(), 7) == [DAY_ONE, DAY_TWO]

    def test_an_empty_time_array_is_refused(self) -> None:
        """No dates is a malformed response."""
        with pytest.raises(ValueError, match="empty 'time'"):
            daily_dates(_daily(time=[]), 7)

    def test_a_misaligned_required_array_is_refused(self) -> None:
        """A required array shorter than ``time`` rejects the whole batch."""
        with pytest.raises(ValueError, match="location=7: 'sunset' has 1"):
            daily_dates(_daily(sunset=["2026-01-10T16:55+01:00"]), 7)


class TestDayIsComplete:
    """day_is_complete — a day past the model's horizon is dropped (SNOW-628)."""

    def test_a_populated_day_is_complete(self) -> None:
        """All three required fields carry a value."""
        assert day_is_complete(_daily(), 0)

    def test_a_null_weather_code_is_incomplete(self) -> None:
        """Sunrise and sunset are astronomical; the code is modelled."""
        assert not day_is_complete(_daily(weather_code=[71, None]), 1)

    def test_an_index_past_the_array_is_incomplete(self) -> None:
        """A short array is no value for that day."""
        assert not day_is_complete(_daily(), 2)


class TestHourlyRowsForDay:
    """hourly_rows_for_day."""

    def test_filters_to_the_day(self) -> None:
        """Only the day's hours come back, every field present."""
        rows = hourly_rows_for_day(_hourly(), DAY_ONE)
        assert [row["time"] for row in rows] == [
            "2026-01-10T09:00+01:00",
            "2026-01-10T10:00+01:00",
        ]
        assert rows[0]["temperature_2m"] == -5.0
        assert rows[0]["snowfall"] is None

    def test_no_block_is_no_rows(self) -> None:
        """A response with no hourly block yields nothing."""
        assert hourly_rows_for_day(None, DAY_ONE) == []


class TestDailyMaxFreezingLevel:
    """daily_max_freezing_level."""

    def test_is_the_highest_hourly_reading(self) -> None:
        """The day's figure is the height the freezing level reached."""
        assert daily_max_freezing_level(_hourly(), DAY_ONE) == 1200.0

    def test_no_reading_is_none(self) -> None:
        """A day whose hours carry no freezing level has none."""
        assert daily_max_freezing_level(_hourly(), DAY_TWO) is None


class TestDailyFields:
    """daily_fields."""

    def test_reads_required_and_optional_fields(self) -> None:
        """Sunrise is a datetime; an omitted or short variable is None."""
        fields = daily_fields(_daily(), 1)
        assert fields["weather_code"] == 3
        assert fields["sunrise"] == datetime(
            2026, 1, 11, 8, 5, tzinfo=timezone(timedelta(hours=1))
        )
        assert fields["temperature_2m_max"] == 1.5
        assert fields["snowfall_sum"] is None
        assert fields["uv_index_max"] is None


class TestForecastEntry:
    """forecast_entry."""

    def test_keeps_sunrise_as_a_string_and_nests_hours(self) -> None:
        """JSON has no datetime; the first forward days carry their hours."""
        entry = forecast_entry(_daily(), _hourly(), 0, DAY_ONE, with_hourly=True)
        assert entry["date"] == "2026-01-10"
        assert entry["sunrise"] == "2026-01-10T08:05+01:00"
        assert entry["freezing_level_height"] == 1200.0
        assert len(entry["hourly"]) == 2

    def test_later_days_carry_no_hourly_key(self) -> None:
        """Past HOURLY_DAYS the key is absent, not empty."""
        entry = forecast_entry(_daily(), _hourly(), 1, DAY_TWO, with_hourly=False)
        assert "hourly" not in entry
