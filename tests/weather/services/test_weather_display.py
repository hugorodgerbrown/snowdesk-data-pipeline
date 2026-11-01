"""
tests/weather/services/test_weather_display.py — Tests for the display service.

Covers the four things this module actually decides:

* the two WMO bucket maps and their unknown-code fallback;
* ``weather_icon_filename``'s day/night split and the ten buckets exempt
  from it;
* ``is_day``'s sunrise-inclusive / sunset-exclusive boundary, compared
  time-of-day only;
* the three context builders, including the two degradations that matter —
  a ``None`` row produces ``None`` rather than an error, and a row with no
  ``hourly`` produces an empty list;
* the outlook as the rows after the lead day (SNOW-1337): a week at most,
  the place's own, read once or handed in.

Every assertion that names an icon filename runs under ``freeze_time`` at
midday. The day/night suffix projects the *current* wall-clock time onto the
page date, so an unfrozen assertion passes locally and fails in CI after
sunset.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.weather.models import Weather
from apps.weather.services.fetch import FORECAST_DAYS
from apps.weather.services.weather_display import (
    DEFAULT_BUCKET,
    DEFAULT_ICON_BUCKET,
    WEATHER_BUCKETS,
    WEATHER_ICON_BUCKETS,
    build_point_forecast_panel,
    build_point_weather_days,
    build_weather_display,
    is_day,
    local_zone,
    weather_code_bucket,
    weather_code_icon_bucket,
    weather_icon_filename,
)
from tests.factories import WeatherFactory

# A midday reference instant. Every icon assertion below is anchored here.
MIDDAY = "2026-08-30T12:00:00+00:00"


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
    """Tests for the weather icon filename derivation."""

    def test_day_night_bucket_takes_a_suffix(self) -> None:
        """A bucket with two variants renders the matching one."""
        assert weather_icon_filename("clear", "day") == "clear-day.svg"
        assert weather_icon_filename("clear", "night") == "clear-night.svg"

    def test_precipitation_buckets_ship_one_file(self) -> None:
        """Weather in front of a cloud looks the same by either light.

        The icon set draws a day/night pair only where a sun or a moon
        appears (SNOW-791), so ten of the twelve buckets take no suffix.
        """
        assert weather_icon_filename("cloudy", "day") == "cloudy.svg"
        assert weather_icon_filename("cloudy", "night") == "cloudy.svg"
        assert weather_icon_filename("light_snow", "day") == "light_snow.svg"
        assert weather_icon_filename("light_snow", "night") == "light_snow.svg"

    def test_every_referenced_icon_file_exists_in_every_set(self) -> None:
        """Every derivable filename is a real file in EVERY icon set on disk.

        SNOW-791 serves one of several candidate sets, chosen per request,
        and they share one filename scheme precisely so switching is a
        directory change. A set missing a file would render a broken image
        only for whoever had that set pinned — so all of them are checked,
        not just the configured one.
        """
        from pathlib import Path

        from django.conf import settings

        from apps.weather.icon_sets import ICON_SETS, available_icon_sets

        sets = available_icon_sets()
        assert sets, "no icon set directories found — the walk is broken"
        missing: list[str] = []
        for set_name in sets:
            root = Path(settings.BASE_DIR) / "static" / ICON_SETS[set_name]
            for bucket in WEATHER_ICON_BUCKETS:
                for time_of_day in ("day", "night"):
                    name = weather_icon_filename(bucket, time_of_day)
                    if not (root / name).is_file():
                        missing.append(f"{set_name}/{name}")
        assert not missing, f"icon sets are missing files: {sorted(set(missing))}"


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
        assert display["icon_filename"] == "light_snow.svg"
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
    """Tests for the multi-day outlook built from a day and the rows after it."""

    def test_none_row_yields_none(self) -> None:
        """A missing row degrades to no panel."""
        assert build_point_forecast_panel(None, timezone.now()) is None

    @freeze_time(MIDDAY)
    def test_the_rows_own_day_leads_then_the_following_rows(self) -> None:
        """The lead day comes first; the next rows for the place follow in order."""
        weather = WeatherFactory.create(observed_on=datetime.date(2026, 8, 30))
        _following(weather, "2026-09-01", "2026-08-31")

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 8, 30),
            datetime.date(2026, 8, 31),
            datetime.date(2026, 9, 1),
        ]

    @freeze_time(MIDDAY)
    def test_the_outlook_stops_at_a_week_and_at_the_place(self) -> None:
        """Seven days in all, and never another place's rows."""
        weather = WeatherFactory.create(observed_on=datetime.date(2026, 8, 30))
        _following(weather, *[f"2026-08-{day}" for day in range(31, 32)])
        _following(weather, *[f"2026-09-0{day}" for day in range(1, 8)])
        WeatherFactory.create(observed_on=datetime.date(2026, 8, 31))

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["date"] for day in panel["days"]] == [
            datetime.date(2026, 8, 30) + datetime.timedelta(days=offset)
            for offset in range(FORECAST_DAYS)
        ]

    @freeze_time(MIDDAY)
    def test_rows_handed_in_are_used_without_a_query(
        self, django_assert_num_queries: Any
    ) -> None:
        """A caller holding the week for many places passes it in."""
        weather = WeatherFactory.create(observed_on=datetime.date(2026, 8, 30))
        following = _following(weather, "2026-08-31")

        with django_assert_num_queries(0):
            panel = build_point_forecast_panel(weather, timezone.now(), following)

        assert panel is not None
        assert len(panel["days"]) == 2

    @freeze_time(MIDDAY)
    def test_wind_reaches_the_lead_day_and_the_following_days(self) -> None:
        """Every column carries its own row's wind."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            wind_speed_10m_max=24.0,
            wind_direction_10m_dominant=270.0,
        )
        _following(
            weather,
            "2026-08-31",
            wind_speed_10m_max=31.0,
            wind_direction_10m_dominant=45.0,
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["wind_speed_max"] for day in panel["days"]] == [24.0, 31.0]
        assert [day["wind_bearing"] for day in panel["days"]] == [270.0, 45.0]

    @freeze_time(MIDDAY)
    def test_every_following_day_carries_its_own_hourly_series(self) -> None:
        """Each row's hours reach its own column (SNOW-1337), or [] when null."""
        weather = WeatherFactory.create(observed_on=datetime.date(2026, 8, 30))
        _following(weather, "2026-08-31", hourly=[{"time": "2026-08-31T09:00"}])
        _following(weather, "2026-09-01")

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert len(panel["days"][1]["hourly"]) == 1
        assert panel["days"][1]["selectable"] is True
        assert panel["days"][2]["hourly"] == []
        assert panel["days"][2]["selectable"] is False

    @freeze_time(MIDDAY)
    def test_daylight_is_each_days_own(self) -> None:
        """SNOW-789: every column carries its OWN daylight window."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            sunrise=datetime.datetime(2026, 8, 30, 7, 5, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 30, 19, 40, tzinfo=datetime.UTC),
        )
        _following(
            weather,
            "2026-08-31",
            sunrise=datetime.datetime(2026, 8, 31, 6, 30, tzinfo=datetime.UTC),
            sunset=datetime.datetime(2026, 8, 31, 20, 15, tzinfo=datetime.UTC),
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        assert [day["sunrise_local"] for day in panel["days"]] == ["07:05", "06:30"]
        assert [day["sunset_local"] for day in panel["days"]] == ["19:40", "20:15"]

    @freeze_time(MIDDAY)
    def test_following_day_scalars_reach_the_column(self) -> None:
        """A following day renders its own icon, temps, snowfall and freezing level."""
        weather = WeatherFactory.create(observed_on=datetime.date(2026, 8, 30))
        _following(
            weather,
            "2026-08-31",
            weather_code=73,
            temperature_2m_max=3.0,
            temperature_2m_min=-4.0,
            snowfall_sum=12.0,
            freezing_level_height=2100.0,
        )

        panel = build_point_forecast_panel(weather, timezone.now())

        assert panel is not None
        forward = panel["days"][1]
        assert forward["weekday_label"] == "Mon"
        assert forward["icon_filename"] == "moderate_snow.svg"
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

    def test_every_row_is_keyed_by_iso_date(self) -> None:
        """The lead day and every following row appear, keyed by date."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30),
            weather_code=3,
            temperature_2m_max=4.5,
        )
        following = _following(
            weather, "2026-08-31", weather_code=71, temperature_2m_max=1.0
        )

        days = build_point_weather_days(weather, following)

        assert days == {
            "2026-08-30": {"code": 3, "icon": "cloudy.svg", "tmax": 4.5},
            "2026-08-31": {"code": 71, "icon": "light_snow.svg", "tmax": 1.0},
        }

    @freeze_time("2026-08-30T23:30:00+00:00")
    def test_the_map_always_takes_the_day_variant(self) -> None:
        """A map symbol summarises the day, so a pair is never a moon."""
        weather = WeatherFactory.create(
            observed_on=datetime.date(2026, 8, 30), weather_code=0
        )
        following = _following(weather, "2026-08-31", weather_code=2)

        days = build_point_weather_days(weather, following)

        assert days["2026-08-30"]["icon"] == "clear-day.svg"
        assert days["2026-08-31"]["icon"] == "partly_cloudy-day.svg"


@pytest.mark.django_db
class TestLocalClock:
    """Sunrise and sunset read on the location's clock after a round trip.

    A stored ``DateTimeField`` comes back in UTC, so the offset is read from
    the row's hourly series (``local_zone``), which keeps the provider's.
    """

    def _alpine(self, day: datetime.date, *, hourly: bool = True) -> Weather:
        """Store an Alpine row (+02:00) and read it back from the database."""
        offset = datetime.timezone(datetime.timedelta(hours=2))
        row = WeatherFactory.create(
            observed_on=day,
            sunrise=datetime.datetime.combine(day, datetime.time(6, 30), offset),
            sunset=datetime.datetime.combine(day, datetime.time(20, 5), offset),
            hourly=[{"time": f"{day.isoformat()}T00:00+02:00"}] if hourly else None,
        )
        return Weather.objects.get(pk=row.pk)

    @freeze_time(MIDDAY)
    def test_a_following_day_read_back_keeps_the_local_clock(self) -> None:
        """A following row loaded from the database shows 06:30, not 04:30."""
        lead = self._alpine(datetime.date(2026, 8, 30))
        offset = datetime.timezone(datetime.timedelta(hours=2))
        WeatherFactory.create(
            location=lead.location,
            observed_on=datetime.date(2026, 8, 31),
            sunrise=datetime.datetime(2026, 8, 31, 6, 31, tzinfo=offset),
            sunset=datetime.datetime(2026, 8, 31, 20, 3, tzinfo=offset),
            hourly=[{"time": "2026-08-31T00:00+02:00"}],
        )

        panel = build_point_forecast_panel(lead, timezone.now())

        assert panel is not None
        assert [(d["sunrise_local"], d["sunset_local"]) for d in panel["days"]] == [
            ("06:30", "20:05"),
            ("06:31", "20:03"),
        ]

    @freeze_time(MIDDAY)
    def test_the_lead_day_read_back_keeps_the_local_clock(self) -> None:
        """Today's strip reads the same clock."""
        display = build_weather_display(
            self._alpine(datetime.date(2026, 8, 30)), timezone.now()
        )

        assert display is not None
        assert (display["sunrise_local"], display["sunset_local"]) == (
            "06:30",
            "20:05",
        )

    def test_a_row_without_hours_falls_back_to_its_own_offset(self) -> None:
        """No hourly offset to read: the stored instant's own zone is used."""
        row = self._alpine(datetime.date(2026, 8, 30), hourly=False)

        assert local_zone(row) == row.sunrise.tzinfo


def _following(lead: Weather, *dates: str, **fields: Any) -> list[Weather]:
    """Create one row per ISO date for ``lead``'s place, with ``fields``."""
    return [
        WeatherFactory.create(
            location=lead.location,
            observed_on=datetime.date.fromisoformat(day),
            **fields,
        )
        for day in dates
    ]
