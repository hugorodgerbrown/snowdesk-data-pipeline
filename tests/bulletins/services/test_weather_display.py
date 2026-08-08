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
    when no snapshot is supplied, the new icon_bucket / condition_label /
    icon_filename fields, and that it also accepts a ForecastPointWeather row.
  - build_point_forecast_panel (SNOW-417): per-day shape, ``None`` for an
    empty list, and hourly passthrough.
  - weather_icon_filename (SNOW-573): day/night suffix behaviour, extracted
    from build_weather_display.
  - build_point_weather_days (SNOW-573): date-keyed dict shape, empty input,
    and null extended fields passing through unchanged.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from apps.bulletins.models import WeatherSnapshot
from apps.bulletins.services.weather_display import (
    DEFAULT_BUCKET,
    DEFAULT_ICON_BUCKET,
    WEATHER_BUCKETS,
    WEATHER_ICON_BUCKETS,
    build_point_forecast_panel,
    build_point_weather_days,
    build_weather_display,
    is_day,
    weather_code_bucket,
    weather_code_icon_bucket,
    weather_icon_filename,
)
from tests.factories import ForecastPointWeatherFactory, WeatherSnapshotFactory

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
    def snapshot(self) -> WeatherSnapshot:
        """A snapshot with sunrise 06:00 UTC, sunset 20:00 UTC on 2026-05-01."""
        return WeatherSnapshotFactory.create(
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )

    def test_just_before_sunrise_is_night(self, snapshot: WeatherSnapshot) -> None:
        """One second before sunrise still resolves as night."""
        moment = datetime.datetime(2026, 5, 1, 5, 59, 59, tzinfo=UTC)
        assert is_day(snapshot, moment) is False

    def test_exactly_sunrise_is_day(self, snapshot: WeatherSnapshot) -> None:
        """Sunrise is inclusive — that instant is the first day moment."""
        assert is_day(snapshot, snapshot.sunrise) is True

    def test_mid_day_is_day(self, snapshot: WeatherSnapshot) -> None:
        """A noon-ish reference falls comfortably inside the day window."""
        moment = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_just_before_sunset_is_day(self, snapshot: WeatherSnapshot) -> None:
        """One second before sunset still resolves as day."""
        moment = datetime.datetime(2026, 5, 1, 19, 59, 59, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_exactly_sunset_is_night(self, snapshot: WeatherSnapshot) -> None:
        """Sunset is exclusive — that instant is the first night moment."""
        assert is_day(snapshot, snapshot.sunset) is False

    def test_after_sunset_is_night(self, snapshot: WeatherSnapshot) -> None:
        """A reference after sunset is night."""
        moment = datetime.datetime(2026, 5, 1, 22, 0, tzinfo=UTC)
        assert is_day(snapshot, moment) is False

    def test_now_on_later_date_with_daytime_clock_is_day(
        self, snapshot: WeatherSnapshot
    ) -> None:
        """A wall-clock 'now' weeks later still resolves as day at noon.

        This is the user-facing scenario: when the viewer browses a
        historical bulletin, the page should track the user's current
        time-of-day projected onto that day — not the wall-clock instant
        of *today*, which would always trail past every historical sunset.
        """
        moment = datetime.datetime(2026, 6, 15, 11, 9, tzinfo=UTC)
        assert is_day(snapshot, moment) is True

    def test_now_on_later_date_with_evening_clock_is_night(
        self, snapshot: WeatherSnapshot
    ) -> None:
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

    def test_accepts_forecast_point_weather_row(self) -> None:
        """build_weather_display also accepts a ForecastPointWeather row."""
        weather = ForecastPointWeatherFactory.create(
            weather_code=0,
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(weather, now)

        assert display is not None
        assert display["weather"] is weather
        assert display["bucket"] == "clear"
        assert display["is_day"] is True

    def test_temp_and_snowfall_pass_through_when_present(self) -> None:
        """temp_max/temp_min/snowfall_sum pass through from a populated snapshot."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=0,
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
            temperature_2m_max=4.2,
            temperature_2m_min=-3.1,
            snowfall_sum=12.0,
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["temp_max"] == 4.2
        assert display["temp_min"] == -3.1
        assert display["snowfall_sum"] == 12.0

    def test_temp_and_snowfall_are_none_when_absent(self) -> None:
        """A sparse snapshot (fields unset) surfaces None for all three."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=0,
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["temp_max"] is None
        assert display["temp_min"] is None
        assert display["snowfall_sum"] is None

    def test_snowfall_zero_is_distinct_from_none(self) -> None:
        """An explicit 0 cm snowfall total surfaces as 0.0, not None."""
        snapshot = WeatherSnapshotFactory.create(
            weather_code=0,
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
            snowfall_sum=0.0,
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        display = build_weather_display(snapshot, now)

        assert display is not None
        assert display["snowfall_sum"] == 0.0
        assert display["snowfall_sum"] is not None


# ---------------------------------------------------------------------------
# build_point_forecast_panel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildPointForecastPanel:
    """Tests for the multi-day favourite forecast panel builder (SNOW-417)."""

    def test_empty_list_returns_none(self) -> None:
        """No snapshots short-circuits to None for the template's empty state."""
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert build_point_forecast_panel([], now) is None

    def test_per_day_shape(self) -> None:
        """Each day carries the icon/label fields plus the multi-day extras."""
        snapshot = ForecastPointWeatherFactory.create(
            weather_code=71,  # snowfall
            valid_for_date=datetime.date(2026, 5, 1),  # a Friday
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
            temperature_2m_max=4.2,
            temperature_2m_min=-3.1,
            snowfall_sum=12.0,
            freezing_level_height=1800.0,
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        panel = build_point_forecast_panel([snapshot], now)

        assert panel is not None
        assert len(panel["days"]) == 1
        day = panel["days"][0]
        assert day["date"] == datetime.date(2026, 5, 1)
        assert day["weekday_label"] == "Fri"
        assert day["icon_bucket"] == "light_snow"
        assert day["condition_label"] == "Light snow"
        assert day["icon_filename"] == "light_snow-day.svg"
        assert day["temp_max"] == 4.2
        assert day["temp_min"] == -3.1
        assert day["snowfall_sum"] == 12.0
        assert day["freezing_level_height"] == 1800.0
        assert day["hourly"] == snapshot.hourly_series

    def test_multiple_days_preserve_order(self) -> None:
        """Days are emitted in the order the snapshots list is passed in."""
        day0 = ForecastPointWeatherFactory.create(
            valid_for_date=datetime.date(2026, 5, 1)
        )
        day1 = ForecastPointWeatherFactory.create(
            forecast_point=day0.forecast_point,
            valid_for_date=datetime.date(2026, 5, 2),
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        panel = build_point_forecast_panel([day0, day1], now)

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 5, 1),
            datetime.date(2026, 5, 2),
        ]

    def test_none_hourly_series_becomes_empty_list(self) -> None:
        """A row with hourly_series=None surfaces as an empty list, not None."""
        snapshot = ForecastPointWeatherFactory.create(hourly_series=None)
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        panel = build_point_forecast_panel([snapshot], now)

        assert panel is not None
        assert panel["days"][0]["hourly"] == []


# ---------------------------------------------------------------------------
# weather_icon_filename
# ---------------------------------------------------------------------------


class TestWeatherIconFilename:
    """Tests for the extracted filename-derivation helper (SNOW-573)."""

    def test_day_night_bucket_gets_time_of_day_suffix(self) -> None:
        """A bucket with day/night variants ships the suffixed filename."""
        assert weather_icon_filename("light_snow", "day") == "light_snow-day.svg"
        assert weather_icon_filename("light_snow", "night") == "light_snow-night.svg"

    def test_cloudy_bucket_has_no_suffix(self) -> None:
        """cloudy is the lone bucket without a day/night distinction."""
        assert weather_icon_filename("cloudy", "day") == "cloudy.svg"
        assert weather_icon_filename("cloudy", "night") == "cloudy.svg"


# ---------------------------------------------------------------------------
# build_point_weather_days
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildPointWeatherDays:
    """Tests for the shared map-weather-layer projection (SNOW-573)."""

    def test_empty_list_returns_empty_dict(self) -> None:
        """No rows produces an empty dict, not None — payloads carry {}."""
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert build_point_weather_days([], now) == {}

    def test_keyed_by_iso_date_with_expected_shape(self) -> None:
        """Each entry is keyed by ISO date with icon/label/tmax/tmin/snow."""
        row = ForecastPointWeatherFactory.create(
            weather_code=71,  # light snowfall
            valid_for_date=datetime.date(2026, 5, 1),
            sunrise=datetime.datetime(2026, 5, 1, 6, 0, tzinfo=UTC),
            sunset=datetime.datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
            temperature_2m_max=4.0,
            temperature_2m_min=-3.0,
            snowfall_sum=2.0,
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        days = build_point_weather_days([row], now)

        assert days == {
            "2026-05-01": {
                "icon": "light_snow-day.svg",
                "label": "Light snow",
                "tmax": 4.0,
                "tmin": -3.0,
                "snow": 2.0,
            }
        }

    def test_multiple_rows_produce_multiple_keys(self) -> None:
        """A multi-day window yields one dict entry per date."""
        point = ForecastPointWeatherFactory.create(
            valid_for_date=datetime.date(2026, 5, 1)
        ).forecast_point
        row0 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 5, 2)
        )
        row1 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 5, 3)
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        days = build_point_weather_days([row0, row1], now)

        assert set(days.keys()) == {"2026-05-02", "2026-05-03"}

    def test_null_extended_fields_pass_through_as_none(self) -> None:
        """Nullable temperature/snowfall fields surface as None, not dropped.

        ForecastPointWeatherFactory sets every extended field non-null by
        default, so nulls must be overridden explicitly here.
        """
        row = ForecastPointWeatherFactory.create(
            valid_for_date=datetime.date(2026, 5, 1),
            temperature_2m_max=None,
            temperature_2m_min=None,
            snowfall_sum=None,
        )
        now = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        days = build_point_weather_days([row], now)

        entry = days["2026-05-01"]
        assert entry["tmax"] is None
        assert entry["tmin"] is None
        assert entry["snow"] is None
