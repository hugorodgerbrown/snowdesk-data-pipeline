"""
tests/bulletins/services/test_weather_display.py — Tests for weather_display.

Covers:
  - weather_code_bucket: representative WMO codes from each band, plus the
    fallback for an unknown code.
  - weather_code_icon_bucket: representative WMO codes from each of the 12
    icon buckets, plus the fallback for an unknown code.
  - is_day: boundary cases around sunrise (inclusive) and sunset (exclusive),
    plus mid-day and mid-night reference points.
  - build_weather_display: shape of the returned dict, ``None`` short-circuit
    when no snapshot is supplied, and the new icon_bucket / condition_label /
    icon_filename fields.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from bulletins.services.weather_display import (
    DEFAULT_BUCKET,
    DEFAULT_ICON_BUCKET,
    WEATHER_BUCKETS,
    WEATHER_ICON_BUCKETS,
    build_weather_display,
    is_day,
    weather_code_bucket,
    weather_code_icon_bucket,
)
from tests.factories import WeatherSnapshotFactory

# ---------------------------------------------------------------------------
# weather_code_bucket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "clear"),
        (1, "partly_cloudy"),
        (2, "partly_cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (48, "fog"),
        (51, "rain"),
        (61, "rain"),
        (65, "rain"),
        (80, "rain"),
        (82, "rain"),
        (71, "snow"),
        (75, "snow"),
        (85, "snow"),
        (86, "snow"),
        (95, "thunder"),
        (99, "thunder"),
    ],
)
def test_weather_code_bucket_known_codes(code: int, expected: str) -> None:
    """Each WMO code in the table maps to its expected display bucket."""
    assert weather_code_bucket(code) == expected


def test_weather_code_bucket_unknown_falls_back_to_default() -> None:
    """Unknown / unmapped WMO codes resolve to the safe default bucket."""
    # 4 is intentionally absent from the WMO table; pick something well out
    # of range too so we cover both "near miss" and "wildly invalid" inputs.
    assert weather_code_bucket(4) == DEFAULT_BUCKET
    assert weather_code_bucket(999) == DEFAULT_BUCKET


def test_default_bucket_is_in_the_bucket_list() -> None:
    """The fallback bucket must itself be a valid bucket identifier."""
    assert DEFAULT_BUCKET in WEATHER_BUCKETS


# ---------------------------------------------------------------------------
# weather_code_icon_bucket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "clear"),
        (1, "partly_cloudy"),
        (2, "partly_cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (48, "fog"),
        (51, "drizzle"),
        (55, "drizzle"),
        (61, "light_rain"),
        (66, "light_rain"),
        (63, "moderate_rain"),
        (65, "heavy_rain"),
        (67, "heavy_rain"),
        (71, "light_snow"),
        (77, "light_snow"),
        (73, "moderate_snow"),
        (75, "heavy_snow"),
        (80, "light_rain"),
        (81, "moderate_rain"),
        (82, "heavy_rain"),
        (85, "light_snow"),
        (86, "heavy_snow"),
        (95, "thunder"),
        (96, "thunder"),
        (99, "thunder"),
    ],
)
def test_weather_code_icon_bucket_known_codes(code: int, expected: str) -> None:
    """Each WMO code in the icon table maps to its expected icon bucket."""
    assert weather_code_icon_bucket(code) == expected


def test_weather_code_icon_bucket_unknown_falls_back_to_default() -> None:
    """Unknown / unmapped WMO codes resolve to the safe default icon bucket."""
    # 4 is intentionally absent from the WMO table; pick something well out
    # of range too so we cover both "near miss" and "wildly invalid" inputs.
    assert weather_code_icon_bucket(4) == DEFAULT_ICON_BUCKET
    assert weather_code_icon_bucket(999) == DEFAULT_ICON_BUCKET


def test_default_icon_bucket_is_in_the_icon_bucket_list() -> None:
    """The fallback icon bucket must itself be a valid icon bucket identifier."""
    assert DEFAULT_ICON_BUCKET in WEATHER_ICON_BUCKETS


# ---------------------------------------------------------------------------
# is_day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsDay:
    """Tests for the is_day boundary semantics."""

    @pytest.fixture()
    def snapshot(self):
        """A snapshot with sunrise 06:00 UTC, sunset 20:00 UTC on 2026-05-01."""
        return WeatherSnapshotFactory.create(
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )

    def test_just_before_sunrise_is_night(self, snapshot) -> None:
        """One second before sunrise still resolves as night."""
        moment = datetime.datetime(2026, 5, 1, 5, 59, 59, tzinfo=UTC)
        assert is_day(snapshot, moment) is False

    def test_exactly_sunrise_is_day(self, snapshot) -> None:
        """Sunrise is inclusive — that instant is the first day moment."""
        assert is_day(snapshot, snapshot.sunrise) is True

    def test_mid_day_is_day(self, snapshot) -> None:
        """A noon-ish reference falls comfortably inside the day window."""
        moment = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_just_before_sunset_is_day(self, snapshot) -> None:
        """One second before sunset still resolves as day."""
        moment = datetime.datetime(2026, 5, 1, 19, 59, 59, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_exactly_sunset_is_night(self, snapshot) -> None:
        """Sunset is exclusive — that instant is the first night moment."""
        assert is_day(snapshot, snapshot.sunset) is False

    def test_after_sunset_is_night(self, snapshot) -> None:
        """A reference after sunset is night."""
        moment = datetime.datetime(2026, 5, 1, 22, 0, tzinfo=UTC)
        assert is_day(snapshot, moment) is False

    def test_now_on_later_date_with_daytime_clock_is_day(self, snapshot) -> None:
        """A wall-clock 'now' weeks later still resolves as day at noon.

        This is the user-facing scenario: when the viewer browses a
        historical bulletin, the page should track the user's current
        time-of-day projected onto that day — not the wall-clock instant
        of *today*, which would always trail past every historical sunset.
        """
        moment = datetime.datetime(2026, 6, 15, 11, 9, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_now_on_later_date_with_evening_clock_is_night(self, snapshot) -> None:
        """A wall-clock 'now' in the evening resolves to night on any date."""
        moment = datetime.datetime(2026, 6, 15, 23, 9, tzinfo=UTC)
        assert is_day(snapshot, moment) is False

    def test_now_in_different_timezone_uses_snapshot_local_tz(self) -> None:
        """``now`` in another tz is converted to the snapshot's offset first.

        A viewer at 11:00 in Tokyo (UTC+9) is at 02:00 UTC, which is
        before sunrise in a UTC-offset snapshot. The function must use
        the snapshot's local-time window, not the viewer's wall-clock.
        """
        snapshot = WeatherSnapshotFactory.create(
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        tokyo = datetime.timezone(datetime.timedelta(hours=9))
        # 11:00 Tokyo = 02:00 UTC — before the snapshot's 06:00 sunrise.
        moment = datetime.datetime(2026, 5, 1, 11, 0, tzinfo=tokyo)
        assert is_day(snapshot, moment) is False


# ---------------------------------------------------------------------------
# build_weather_display
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildWeatherDisplay:
    """Tests for the top-level context builder."""

    def test_none_snapshot_returns_none(self) -> None:
        """Missing snapshots short-circuit to None for the partial fallback."""
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert build_weather_display(None, now) is None

    def test_returns_full_dict_during_day(self) -> None:
        """A daytime call produces a populated dict with bucket + day flag."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=0,  # clear sky
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["weather"] is snapshot
        assert display["bucket"] == "clear"
        assert display["is_day"] is True
        assert display["time_of_day"] == "day"

    def test_returns_full_dict_during_night(self) -> None:
        """A night-time call sets is_day=False and time_of_day='night'."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=71,  # snowfall
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 23, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["bucket"] == "snow"
        assert display["is_day"] is False
        assert display["time_of_day"] == "night"

    def test_unknown_code_falls_back_to_default_bucket(self) -> None:
        """An unmapped WMO code does not raise — it lands in DEFAULT_BUCKET."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=4,  # not in the WMO table
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["bucket"] == DEFAULT_BUCKET

    def test_clear_sky_daytime_icon_fields(self) -> None:
        """Clear-sky (WMO 0) at midday populates icon_bucket, label, and filename."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=0,  # clear sky
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["icon_bucket"] == "clear"
        assert display["condition_label"] == "Clear"
        assert display["icon_filename"] == "clear-day.svg"

    def test_heavy_snow_nighttime_icon_fields(self) -> None:
        """Heavy snow (WMO 75) at night yields heavy_snow icon with night suffix."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=75,  # heavy snowfall
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 23, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["icon_bucket"] == "heavy_snow"
        assert display["condition_label"] == "Heavy snow"
        assert display["icon_filename"] == "heavy_snow-night.svg"

    def test_cloudy_code_emits_no_day_night_suffix(self) -> None:
        """Cloudy (WMO 3) at midnight ships as cloudy.svg — no day/night suffix."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=3,  # overcast
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 0, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["icon_bucket"] == "cloudy"
        assert display["condition_label"] == "Overcast"
        assert display["icon_filename"] == "cloudy.svg"
        assert "day" not in display["icon_filename"]
        assert "night" not in display["icon_filename"]
