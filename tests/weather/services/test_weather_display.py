"""
tests/weather/services/test_weather_display.py — Tests for the display service.

Covers the four things this module actually decides:

* the two WMO bucket maps and their unknown-code fallback;
* ``weather_icon_filename``'s day/night split and the one bucket exempt
  from it;
* ``is_day``'s sunrise-inclusive / sunset-exclusive boundary, compared
  time-of-day only;
* the three context builders, including the two degradations that matter —
  a ``None`` row produces ``None`` rather than an error, and a forward day
  with no ``hourly`` key produces an empty list rather than a KeyError.

Every assertion that names an icon filename runs under ``freeze_time`` at
midday. The day/night suffix projects the *current* wall-clock time onto the
page date, so an unfrozen assertion passes locally and fails in CI after
sunset.
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.weather.models import Weather
from apps.weather.services.weather_display import (
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
from tests.factories import WeatherFactory

# A midday reference instant. Every icon assertion below is anchored here.
MIDDAY = "2026-08-30T12:00:00+00:00"


def _forecast_day(
    *,
    date: str = "2026-08-31",
    weather_code: int = 71,
    hourly: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    """Build one ``forecast[]`` entry for a test row.

    Args:
        date: The forward day's ISO date.
        weather_code: The day's WMO code.
        hourly: The nested hourly series, or ``None`` to omit the key
            entirely — which is what a day past ``HOURLY_DAYS`` looks like.
        **overrides: Any other ``ForecastDay`` key to set.

    Returns:
        The entry dict.

    """
    entry: dict[str, object] = {
        "date": date,
        "weather_code": weather_code,
        "sunrise": f"{date}T06:30:00+02:00",
        "sunset": f"{date}T20:15:00+02:00",
        "temperature_2m_max": 3.0,
        "temperature_2m_min": -4.0,
        "snowfall_sum": 12.0,
        "freezing_level_height": 2100.0,
    }
    if hourly is not None:
        entry["hourly"] = hourly
    entry.update(overrides)
    return entry


class TestBuckets:
    """Tests for the two WMO code → bucket maps."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (0, "clear"),
            (2, "partly_cloudy"),
            (48, "fog"),
            (82, "rain"),
            (99, "thunder"),
        ],
    )
    def test_background_bucket_for_known_code(self, code: int, expected: str) -> None:
        """A known WMO code resolves to its background bucket."""
        assert weather_code_bucket(code) == expected

    def test_background_bucket_falls_back_for_unknown_code(self) -> None:
        """An unrecognised code resolves to the neutral default, not an error."""
        assert weather_code_bucket(4) == DEFAULT_BUCKET

    def test_every_background_bucket_is_declared(self) -> None:
        """No mapped code resolves outside the published bucket tuple."""
        assert {weather_code_bucket(code) for code in range(100)} <= set(
            WEATHER_BUCKETS
        )

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (51, "drizzle"),
            (61, "light_rain"),
            (63, "moderate_rain"),
            (65, "heavy_rain"),
            (71, "light_snow"),
            (73, "moderate_snow"),
            (75, "heavy_snow"),
        ],
    )
    def test_icon_bucket_splits_rain_and_snow(self, code: int, expected: str) -> None:
        """The icon map is finer than the background map for rain and snow."""
        assert weather_code_icon_bucket(code) == expected

    def test_icon_bucket_falls_back_for_unknown_code(self) -> None:
        """An unrecognised code resolves to the neutral default icon bucket."""
        assert weather_code_icon_bucket(4) == DEFAULT_ICON_BUCKET

    def test_every_icon_bucket_is_declared(self) -> None:
        """No mapped code resolves outside the published icon-bucket tuple."""
        assert {weather_code_icon_bucket(code) for code in range(100)} <= set(
            WEATHER_ICON_BUCKETS
        )


class TestIconFilename:
    """Tests for the Meteocons filename derivation."""

    def test_day_night_bucket_takes_a_suffix(self) -> None:
        """A bucket with two variants renders the matching one."""
        assert weather_icon_filename("light_snow", "day") == "light_snow-day.svg"
        assert weather_icon_filename("light_snow", "night") == "light_snow-night.svg"

    def test_cloudy_ships_one_file(self) -> None:
        """``cloudy`` reads the same in any light, so it takes no suffix."""
        assert weather_icon_filename("cloudy", "day") == "cloudy.svg"
        assert weather_icon_filename("cloudy", "night") == "cloudy.svg"

    def test_every_referenced_icon_file_exists(self) -> None:
        """Every derivable filename is a file actually shipped under static/."""
        from pathlib import Path

        from django.conf import settings

        icons = Path(settings.BASE_DIR) / "static" / "icons" / "weather"
        for bucket in WEATHER_ICON_BUCKETS:
            for time_of_day in ("day", "night"):
                name = weather_icon_filename(bucket, time_of_day)
                assert (icons / name).is_file(), name


@pytest.mark.django_db
class TestIsDay:
    """Tests for the sunrise-inclusive / sunset-exclusive day window."""

    def _row(self) -> Weather:
        """Build a row whose day window is 06:30–20:15 UTC.

        Returns:
            The Weather row.

        """
        return WeatherFactory.create(
            sunrise=datetime.datetime(2026, 8, 30, 6, 30, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 30, 20, 15, tzinfo=datetime.UTC),
        )

    def test_midday_is_day(self) -> None:
        """An instant inside the window is daytime."""
        weather = self._row()
        now = datetime.datetime(2026, 8, 30, 12, 0, tzinfo=datetime.UTC)

        assert is_day(weather, now) is True

    def test_sunrise_instant_is_day(self) -> None:
        """The window is sunrise-INCLUSIVE."""
        weather = self._row()
        now = datetime.datetime(2026, 8, 30, 6, 30, tzinfo=datetime.UTC)

        assert is_day(weather, now) is True

    def test_sunset_instant_is_night(self) -> None:
        """The window is sunset-EXCLUSIVE."""
        weather = self._row()
        now = datetime.datetime(2026, 8, 30, 20, 15, tzinfo=datetime.UTC)

        assert is_day(weather, now) is False

    def test_time_of_day_is_projected_onto_another_date(self) -> None:
        """Only the time-of-day is compared, never the full instant.

        A viewer reading a page at midday sees daytime whichever date the
        row is of — the sun rose and set on that day too.
        """
        weather = self._row()
        now = datetime.datetime(2030, 1, 1, 12, 0, tzinfo=datetime.UTC)

        assert is_day(weather, now) is True

    def test_the_window_is_read_in_the_rows_own_timezone(self) -> None:
        """A viewer in another timezone sees the REGION's daylight.

        Open-Meteo returns sunrise/sunset carrying the location's own
        offset (+02:00 for Switzerland), and the docstring promises the
        comparison happens there rather than in UTC. This case separates
        the two: 19:00 UTC is 21:00 in the row's zone, which is after a
        20:15 local sunset — so it is night. Comparing the raw UTC clock
        against the local sunset would read 19:00 < 20:15 and wrongly
        say day.
        """
        local = datetime.timezone(datetime.timedelta(hours=2))
        weather = WeatherFactory.create(
            sunrise=datetime.datetime(2026, 8, 30, 6, 30, tzinfo=local),
            sunset=datetime.datetime(2026, 8, 30, 20, 15, tzinfo=local),
        )
        now = datetime.datetime(2026, 8, 30, 19, 0, tzinfo=datetime.UTC)

        assert is_day(weather, now) is False


@pytest.mark.django_db
class TestBuildWeatherDisplay:
    """Tests for the single-day context builder."""

    def test_none_row_yields_none(self) -> None:
        """A missing row degrades to no panel, never an error.

        This is the historical-date case: the estate has no ``Weather`` row
        before the day fetching started, and a bulletin page for such a date
        must simply omit the panel.
        """
        assert build_weather_display(None, timezone.now()) is None

    @freeze_time(MIDDAY)
    def test_fields_are_read_directly_off_the_row(self) -> None:
        """Every scalar the panel renders comes straight off the model."""
        weather = WeatherFactory.create(
            weather_code=71,
            temperature_2m_max=1.5,
            temperature_2m_min=-6.0,
            snowfall_sum=14.0,
            freezing_level_height=1900.0,
            sunrise=datetime.datetime(2026, 8, 30, 6, 30, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 30, 20, 15, tzinfo=datetime.UTC),
        )

        display = build_weather_display(weather, timezone.now())

        assert display is not None
        assert display["bucket"] == "snow"
        assert display["icon_bucket"] == "light_snow"
        assert display["condition_label"] == "Light snow"
        assert display["icon_filename"] == "light_snow-day.svg"
        assert display["time_of_day"] == "day"
        assert display["sunrise_local"] == "06:30"
        assert display["sunset_local"] == "20:15"
        assert display["temp_max"] == 1.5
        assert display["temp_min"] == -6.0
        assert display["snowfall_sum"] == 14.0
        assert display["freezing_level_height"] == 1900.0

    @freeze_time(MIDDAY)
    def test_wind_fields_reach_the_panel(self) -> None:
        """Speed, bearing and a qualifying gust are all handed over."""
        weather = WeatherFactory.create(
            wind_speed_10m_max=24.0,
            wind_gusts_10m_max=41.0,
            wind_direction_10m_dominant=270.0,
        )

        display = build_weather_display(weather, timezone.now())

        assert display is not None
        assert display["wind_speed_max"] == 24.0
        assert display["wind_bearing"] == 270.0
        assert display["wind_gusts_max"] == 41.0

    @freeze_time(MIDDAY)
    @pytest.mark.parametrize(
        ("speed", "gusts"),
        [
            (24.0, 24.0),  # Equal — "24 gusting 24" says nothing.
            (24.0, 20.0),  # Below the sustained max; not a gust worth naming.
            (24.0, None),  # Open-Meteo omitted it.
            (None, 41.0),  # No speed means no wind item to qualify.
        ],
    )
    def test_gusts_are_dropped_when_they_add_nothing(
        self, speed: float | None, gusts: float | None
    ) -> None:
        """A gust only renders when it exceeds the sustained speed.

        The stat row already carries four other values, so a gust that
        merely restates the maximum is dropped rather than shown.
        """
        weather = WeatherFactory.create(
            wind_speed_10m_max=speed, wind_gusts_10m_max=gusts
        )

        display = build_weather_display(weather, timezone.now())

        assert display is not None
        assert display["wind_gusts_max"] is None

    @freeze_time("2026-08-30T23:00:00+00:00")
    def test_night_selects_the_night_icon(self) -> None:
        """After sunset the same code resolves to the night variant."""
        weather = WeatherFactory.create(
            weather_code=0,
            sunrise=datetime.datetime(2026, 8, 30, 6, 30, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 30, 20, 15, tzinfo=datetime.UTC),
        )

        display = build_weather_display(weather, timezone.now())

        assert display is not None
        assert display["icon_filename"] == "clear-night.svg"


@pytest.mark.django_db
class TestBuildPointForecastPanel:
    """Tests for the multi-day outlook built from one row."""

    def test_none_row_yields_none(self) -> None:
        """A missing row degrades to no panel."""
        assert build_point_forecast_panel(None, timezone.now()) is None

    @freeze_time(MIDDAY)
    def test_the_rows_own_day_leads_then_its_forecast(self) -> None:
        """The row's day comes first; the forward days follow in stored order."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            forecast=[
                _forecast_day(date="2026-08-31"),
                _forecast_day(date="2026-09-01"),
            ],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 8, 30),
            datetime.date(2026, 8, 31),
            datetime.date(2026, 9, 1),
        ]

    @freeze_time(MIDDAY)
    def test_wind_reaches_the_lead_day_and_the_forward_days(self) -> None:
        """Both build sites populate wind, not just the lead one.

        The lead day is read off the ``Weather`` row and the forward days
        off their ``forecast[]`` entries, in two separate constructor
        calls. Updating one and not the other would show wind on today
        and nothing after it — which is why this asserts across the whole
        strip rather than on ``days[0]``.
        """
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            wind_speed_10m_max=24.0,
            wind_direction_10m_dominant=270.0,
            forecast=[
                _forecast_day(
                    date="2026-08-31",
                    wind_speed_10m_max=31.0,
                    wind_direction_10m_dominant=45.0,
                ),
            ],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["wind_speed_max"] for day in panel["days"]] == [24.0, 31.0]
        assert [day["wind_bearing"] for day in panel["days"]] == [270.0, 45.0]

    @freeze_time(MIDDAY)
    def test_a_forward_day_missing_wind_degrades_to_none(self) -> None:
        """Open-Meteo drops variables per model, so absence is ordinary."""
        weather = WeatherFactory.create(forecast=[_forecast_day()])

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert panel["days"][1]["wind_speed_max"] is None
        assert panel["days"][1]["wind_bearing"] is None

    @freeze_time(MIDDAY)
    def test_a_forward_day_without_hourly_yields_an_empty_list(self) -> None:
        """``hourly`` is optional per entry, so absence is not an error.

        Only the first few forward days carry an hourly series; beyond that
        the key is absent entirely.
        """
        weather = WeatherFactory.create(
            forecast=[
                _forecast_day(date="2026-08-31", hourly=[{"time": "2026-08-31T09:00"}]),
                _forecast_day(date="2026-09-05"),
            ],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert len(panel["days"][1]["hourly"]) == 1
        assert panel["days"][2]["hourly"] == []

    @freeze_time(MIDDAY)
    def test_a_malformed_forward_day_is_dropped_not_raised(self) -> None:
        """One unparseable entry cannot take the whole outlook out."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            forecast=[{"date": "not-a-date", "weather_code": 3}, _forecast_day()],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 8, 30),
            datetime.date(2026, 8, 31),
        ]

    @freeze_time(MIDDAY)
    def test_a_forward_day_without_a_weather_code_is_dropped_not_raised(self) -> None:
        """A code-less entry is as malformed as a date-less one.

        The date/sunrise/sunset guard once stopped short of the icon-bucket
        lookup, so an entry that parsed its dates but carried no
        ``weather_code`` raised ``KeyError`` past the caller and took the
        page down — the opposite of what the docstring promised. Unlike
        ``hourly``, ``weather_code`` is required on every entry, so its
        absence is corruption rather than an expected gap.
        """
        entry = _forecast_day(date="2026-08-31")
        del entry["weather_code"]
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            forecast=[entry, _forecast_day(date="2026-09-01")],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 8, 30),
            datetime.date(2026, 9, 1),
        ]

    @freeze_time(MIDDAY)
    def test_daylight_reaches_the_lead_day_and_the_forward_days(self) -> None:
        """SNOW-789: every column carries its OWN daylight window.

        The selected-day line states the daylight for whichever day the
        picker has chosen, so a pair set only on the lead day would print
        today's sunrise under Friday's date. The two build sites are
        separate constructor calls, which is why this asserts across the
        strip rather than on ``days[0]``.

        Both are formatted in the entry's own stored offset — the forward
        day's ``+02:00`` sunrise reads 06:30 whatever the active
        ``TIME_ZONE`` is.
        """
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            sunrise=datetime.datetime(2026, 8, 30, 7, 5, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 30, 19, 40, tzinfo=datetime.UTC),
            forecast=[_forecast_day(date="2026-08-31")],
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["sunrise_local"] for day in panel["days"]] == ["07:05", "06:30"]
        assert [day["sunset_local"] for day in panel["days"]] == ["19:40", "20:15"]

    @freeze_time(MIDDAY)
    def test_forward_day_scalars_reach_the_column(self) -> None:
        """A forward day renders its own temps, snowfall and freezing level."""
        weather = WeatherFactory.create(forecast=[_forecast_day(weather_code=73)])

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        forward = panel["days"][1]
        assert forward["weekday_label"] == "Mon"
        assert forward["icon_filename"] == "moderate_snow-day.svg"
        assert forward["condition_label"] == "Snow"
        assert forward["temp_max"] == 3.0
        assert forward["temp_min"] == -4.0
        assert forward["snowfall_sum"] == 12.0
        assert forward["freezing_level_height"] == 2100.0


@pytest.mark.django_db
class TestBuildPointWeatherDays:
    """Tests for the map feed's ``days`` projection."""

    def test_none_row_yields_an_empty_dict(self) -> None:
        """A location with no row contributes no days, not an error."""
        assert build_point_weather_days(None) == {}

    def test_row_and_forecast_are_keyed_by_iso_date(self) -> None:
        """The row's own day and every forward day appear, keyed by date."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            weather_code=3,
            temperature_2m_max=4.5,
            forecast=[
                _forecast_day(
                    date="2026-08-31", weather_code=71, temperature_2m_max=1.0
                )
            ],
        )

        days = build_point_weather_days(weather)

        assert days == {
            "2026-08-30": {"code": 3, "tmax": 4.5},
            "2026-08-31": {"code": 71, "tmax": 1.0},
        }

    def test_a_malformed_forward_day_is_skipped(self) -> None:
        """An unparseable entry drops out rather than taking the feed down."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            forecast=[{"weather_code": 3}, _forecast_day(date="2026-08-31")],
        )

        assert sorted(build_point_weather_days(weather)) == [
            "2026-08-30",
            "2026-08-31",
        ]
