"""
apps/weather/services/weather_display.py — Render-time helpers for ``Weather``.

Turns ``apps.weather.models.Weather`` rows — one day each, and the rows that
follow a day for its outlook — into the context dicts the server-rendered
surfaces read, and into the lean ``days`` dict the map's weather feed serves.

Three consumers, one derivation:

  ``build_weather_display``      the single day on the favourite card and
                                 the help page's weather illustration.
  ``build_point_forecast_panel`` the multi-day outlook: the row's own day
                                 followed by the next rows for the place.
                                 Drives both the day picker and the
                                 selected-day line on the location
                                 forecast page.
  ``build_point_weather_days``   the map feed's ``{date: {code, icon,
                                 tmax}}`` projection (``apps.public.api``).

**A day is a row, and so is every day after it** (SNOW-1337). Each fetch
writes one row per day of its seven-day window, so the outlook from a day is
the rows that follow it for the same place — read in one query
(``following_rows``), or handed in by a caller that already holds them
for many places at once. Every read is a direct attribute access on a
``Weather``. The caller's read path for the lead day is always
``Weather.objects.filter(location=…, observed_on=…).first()`` on the unique
constraint — never ``.order_by("-fetched_at").first()``, since today's row is
updated in place.

Two bucket maps sit on the WMO weather interpretation code (0–99):

1. **Background buckets** (``WEATHER_BUCKETS``, 7 entries) — the coarse
   grouping the ``--color-weather-*`` design tokens are keyed by.
2. **Icon buckets** (``WEATHER_ICON_BUCKETS``, 12 entries) — the finer
   grouping that selects a Yr / MET Norway SVG. Rain splits into drizzle /
   light / moderate / heavy and snow into light / moderate / heavy, so the
   icon tells the reader more than the colour band alone.

Both fall back to ``cloudy`` for an unrecognised code — a neutral default
rather than a missing-data sentinel, so one rogue code can never take a page
out.

WMO code reference:
  0           Clear sky
  1, 2        Mainly clear, partly cloudy
  3           Overcast
  45, 48      Fog
  51–57       Drizzle (incl. freezing)
  61–67       Rain (incl. freezing)
  80–82       Rain showers
  71–77       Snowfall and snow grains
  85, 86      Snow showers
  95          Thunderstorm
  96, 99      Thunderstorm with hail

The code → icon-bucket table is the weather service's, not this module's:
``WMO_CODE_TO_ICON_BUCKET`` and ``icon_bucket_for_code`` live in
``apps.ingest.normalise.weather_icons`` (SNOW-1343), and every day's bucket
is derived from its row's ``weather_code`` through it. The map no longer
carries a mirror of the table: the feed serves each day's ``icon``
filename. Which variant of a day/night pair a panel shows is still decided
here, at read time, from the day's stored sunrise and sunset.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import TypedDict

from apps.ingest.public import (
    FORECAST_DAYS,
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,
    icon_bucket_for_code,
)
from apps.weather.models import Weather
from apps.weather.types import HourlyRow

# ---------------------------------------------------------------------------
# Background buckets
# ---------------------------------------------------------------------------

# Bucket identifiers — kept short and dash-free so they sit cleanly inside
# CSS class / data-attribute selectors. Exposed as a tuple so call sites can
# enumerate them (tests, admin help text) without importing the private dict.
WEATHER_BUCKETS: tuple[str, ...] = (
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "rain",
    "snow",
    "thunder",
)

DEFAULT_BUCKET: str = "cloudy"

_WMO_CODE_TO_BUCKET: dict[int, str] = {
    0: "clear",
    1: "partly_cloudy",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rain",
    53: "rain",
    55: "rain",
    56: "rain",
    57: "rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}


def weather_code_bucket(code: int) -> str:
    """Return the display bucket for a WMO weather interpretation code.

    Unknown codes resolve to :data:`DEFAULT_BUCKET` so a surface always has
    a renderable bucket — there is no "unknown" visual state.

    Args:
        code: A WMO weather interpretation code (0–99).

    Returns:
        One of the bucket identifiers in :data:`WEATHER_BUCKETS`.

    """
    return _WMO_CODE_TO_BUCKET.get(code, DEFAULT_BUCKET)


# ---------------------------------------------------------------------------
# Icon buckets
# ---------------------------------------------------------------------------

WEATHER_ICON_BUCKETS: tuple[str, ...] = (
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "light_rain",
    "moderate_rain",
    "heavy_rain",
    "light_snow",
    "moderate_snow",
    "heavy_snow",
    "thunder",
)

# WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT — the buckets drawn as a day/night
# pair — is the weather service's, imported above and re-exported here for
# the readers that pick a filename (SNOW-1343).
DEFAULT_ICON_BUCKET: str = "cloudy"

# En-GB condition labels displayed alongside the icon.
_ICON_BUCKET_LABEL: dict[str, str] = {
    "clear": "Clear",
    "partly_cloudy": "Partly cloudy",
    "cloudy": "Overcast",
    "fog": "Fog",
    "drizzle": "Drizzle",
    "light_rain": "Light rain",
    "moderate_rain": "Rain",
    "heavy_rain": "Heavy rain",
    "light_snow": "Light snow",
    "moderate_snow": "Snow",
    "heavy_snow": "Heavy snow",
    "thunder": "Thunderstorm",
}


def weather_code_icon_bucket(code: int) -> str:
    """Return the icon bucket for a WMO weather interpretation code.

    A finer split than :func:`weather_code_bucket` (12 buckets vs 7),
    separating rain into drizzle / light / moderate / heavy and snow into
    light / moderate / heavy. Unknown codes resolve to
    :data:`DEFAULT_ICON_BUCKET` — the same safe-fallback posture.

    Args:
        code: A WMO weather interpretation code (0–99).

    Returns:
        One of the bucket identifiers in :data:`WEATHER_ICON_BUCKETS`.

    """
    return icon_bucket_for_code(code)


def weather_icon_filename(icon_bucket: str, time_of_day: str) -> str:
    """Return the weather SVG basename for a bucket and time of day.

    Only the buckets in :data:`WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT` ship
    separate day/night variants; every other bucket resolves to a single
    file and ignores ``time_of_day``.

    Args:
        icon_bucket: One of the identifiers in :data:`WEATHER_ICON_BUCKETS`.
        time_of_day: ``"day"`` or ``"night"``.

    Returns:
        The SVG basename, e.g. ``"clear-day.svg"`` or ``"light_snow.svg"``.

    """
    if icon_bucket in WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT:
        return f"{icon_bucket}-{time_of_day}.svg"
    return f"{icon_bucket}.svg"


# ---------------------------------------------------------------------------
# The location's clock
# ---------------------------------------------------------------------------


def local_zone(weather: "Weather") -> datetime.tzinfo:
    """Return the fixed offset of the location's own clock for ``weather``.

    A ``DateTimeField`` loses the provider's offset on a database round trip
    (``USE_TZ`` hands it back in UTC), so ``sunrise`` read from a stored row
    cannot say what the clock reads at the location. The row's hourly series
    can: each hour keeps the provider's timestamp as a string, in the
    point's own offset (``timezone=auto``). That offset is the answer; a row
    with no such hour falls back to the one ``sunrise`` carries — the
    provider's own on a row built in memory, UTC on one read back.

    Args:
        weather: The row whose location's clock is wanted.

    Returns:
        A tzinfo for formatting the row's instants as wall-clock times.

    """
    for hour in weather.hourly or []:
        try:
            offset = datetime.datetime.fromisoformat(hour["time"]).tzinfo
        except KeyError, TypeError, ValueError:
            continue
        if offset is not None:
            return offset
    return weather.sunrise.tzinfo or datetime.UTC


def _clock(instant: datetime.datetime, zone: datetime.tzinfo) -> str:
    """Return ``instant`` as "HH:MM" on the clock ``zone`` keeps."""
    return instant.astimezone(zone).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Day / night
# ---------------------------------------------------------------------------


def _is_day_between(
    sunrise: datetime.datetime,
    sunset: datetime.datetime,
    now: datetime.datetime,
) -> bool:
    """Return whether the wall-clock ``now`` sits inside the day window.

    The body of :func:`is_day`, kept separate so the rule can be read and
    tested on its own.

    Args:
        sunrise: Sunrise, as an aware datetime.
        sunset: Sunset, as an aware datetime.
        now: The reference instant.

    Returns:
        ``True`` for daytime, ``False`` for night.

    """
    local_now = now.astimezone(sunrise.tzinfo)
    return sunrise.time() <= local_now.time() < sunset.time()


def is_day(weather: "Weather", now: datetime.datetime) -> bool:
    """Return ``True`` if the wall-clock ``now`` sits inside the day window.

    Compares **time-of-day only**, not full instants. The user's current
    wall-clock time is projected onto the row's day — so at 11:00 local,
    every past or future date the user navigates to renders as daytime; at
    23:00 local, every date renders as night. This matches user expectation
    for a calendar dominated by historical pages: the sun rose and set on
    those days too, and the visual should track the time the user is
    *looking* at the page, not the real-world instant the row was written.

    Comparison is done in the tzinfo the row's ``sunrise`` carries, so a
    viewer browsing from a different timezone still sees a visual that lines
    up with the location's own daylight.

    Daylight is sunrise-inclusive and sunset-exclusive, so the boundary
    instants land in night only on the sunset side.

    Only the signature narrowed in the SNOW-761 rebuild: the logic is the
    one that shipped, because it is the one that was got right.

    Args:
        weather: The :class:`apps.weather.models.Weather` row to evaluate.
        now: The reference instant (typically ``timezone.now()``).

    Returns:
        ``True`` for daytime, ``False`` for night.

    """
    return _is_day_between(weather.sunrise, weather.sunset, now)


# ---------------------------------------------------------------------------
# Single-day display
# ---------------------------------------------------------------------------


class WeatherDisplay(TypedDict):
    """Context dict consumed by ``includes/_weather_panel.html``.

    Everything a surface needs to draw one day at one location, pre-computed
    here so the partial only emits what it is handed.
    """

    weather: "Weather"
    bucket: str
    is_day: bool
    time_of_day: str  # "day" or "night" — pre-computed for template clarity.
    sunrise_local: str  # "HH:MM" on the location's clock (local_zone).
    sunset_local: str  # "HH:MM" in the row's stored offset.
    icon_bucket: str  # One of WEATHER_ICON_BUCKETS (finer than ``bucket``).
    condition_label: str  # En-GB human label, e.g. "Light snow".
    icon_filename: str  # Basename of the SVG in static/icons/weather/.
    temp_max: float | None  # Daily max air temp at 2m, °C.
    temp_min: float | None  # Daily min air temp at 2m, °C.
    snowfall_sum: float | None  # Daily snowfall total, cm.
    freezing_level_height: float | None  # Day's maximum freezing level, m.
    wind_speed_max: float | None  # Daily max sustained wind at 10m, km/h.
    wind_bearing: float | None  # Dominant direction at 10m, degrees FROM.
    # Gusts only when they exceed the sustained speed — an equal value adds
    # nothing to the reading, and "24 gusting 24" is noise on a stat row.
    wind_gusts_max: float | None


def _gusts_worth_showing(speed: float | None, gusts: float | None) -> float | None:
    """Return the gust value only when it says more than the speed alone.

    Open-Meteo reports a gust maximum for every day, and on a still day it
    sits at or barely above the sustained maximum. Rendering it regardless
    produces "24 gusting 24" on a stat row that is already carrying four
    other values, so the panel drops it unless it is genuinely higher.

    A missing sustained speed means there is no wind item at all, so the
    gust has nothing to qualify and is dropped with it.

    Args:
        speed: The day's maximum sustained wind at 10m, or ``None``.
        gusts: The day's maximum gust at 10m, or ``None``.

    Returns:
        The gust value, or ``None`` when it is absent, unqualifiable, or
        not above the sustained speed.

    """
    if speed is None or gusts is None:
        return None
    return gusts if gusts > speed else None


def build_weather_display(
    weather: "Weather | None", now: datetime.datetime
) -> WeatherDisplay | None:
    """Build the template context for one day at one location.

    Returns ``None`` when no row is available, so every caller short-circuits
    to "no panel" rather than to an error. That is the ordinary case for a
    historical date: ``Weather`` rows start at the day the estate was first
    fetched and the SNOW-731 backfill is deferred, so a bulletin page for
    last February has nothing to show and must simply show nothing.

    Args:
        weather: The row for the calendar day, or ``None``.
        now: The reference instant for the day/night decision.

    Returns:
        A :class:`WeatherDisplay`, or ``None`` when ``weather`` is ``None``.

    """
    if weather is None:
        return None
    daytime = is_day(weather, now)
    time_of_day = "day" if daytime else "night"
    icon_bucket = weather_code_icon_bucket(weather.weather_code)
    return WeatherDisplay(
        weather=weather,
        bucket=weather_code_bucket(weather.weather_code),
        is_day=daytime,
        time_of_day=time_of_day,
        # The wall-clock time of sunrise/sunset AT THE LOCATION, not in the
        # Django-active TIME_ZONE — read from the row's hourly offset, since
        # a stored DateTimeField comes back in UTC (local_zone).
        sunrise_local=_clock(weather.sunrise, zone := local_zone(weather)),
        sunset_local=_clock(weather.sunset, zone),
        icon_bucket=icon_bucket,
        condition_label=_ICON_BUCKET_LABEL[icon_bucket],
        icon_filename=weather_icon_filename(icon_bucket, time_of_day),
        temp_max=weather.temperature_2m_max,
        temp_min=weather.temperature_2m_min,
        snowfall_sum=weather.snowfall_sum,
        freezing_level_height=weather.freezing_level_height,
        wind_speed_max=weather.wind_speed_10m_max,
        wind_bearing=weather.wind_direction_10m_dominant,
        wind_gusts_max=_gusts_worth_showing(
            weather.wind_speed_10m_max, weather.wind_gusts_10m_max
        ),
    )


# ---------------------------------------------------------------------------
# Multi-day outlook
# ---------------------------------------------------------------------------


class ForecastPanelDay(TypedDict):
    """One day's worth of context for the multi-day outlook panel."""

    date: datetime.date
    weekday_label: str  # e.g. "Mon" — short, locale-independent for now.
    icon_bucket: str
    icon_filename: str
    condition_label: str
    temp_max: float | None
    temp_min: float | None
    snowfall_sum: float | None
    freezing_level_height: float | None
    wind_speed_max: float | None  # Daily max sustained wind at 10m, km/h.
    wind_bearing: float | None  # Dominant direction at 10m, degrees FROM.
    # SNOW-1105: the weather sheet states the chance of precipitation, and
    # on a forward day it reads this column rather than a row of its own.
    precipitation_probability_max: int | None
    # SNOW-789: the day's daylight window, "HH:MM" in the day's OWN
    # offset — the same rule as ``WeatherDisplay.sunrise_local``. The day
    # picker's selected-day line states the daylight for whichever day is
    # chosen, so every column has to carry its own pair rather than the
    # lead day's.
    sunrise_local: str
    sunset_local: str
    hourly: list[HourlyRow]  # That day's hourly rows, or [].
    # SNOW-787, narrowed by SNOW-789: whether this day has a meteogram.
    # Every column in the day picker is a control now, so this no longer
    # decides which ones are; it decides only whether selecting this day
    # reveals an ``_hourly_chart.html`` beneath the picker. Presence of an
    # hourly series, NOT truthiness of some other field — a row with a null
    # or empty ``hourly`` has no detail to show. Kept as its own field rather than left
    # to the template testing ``day.hourly``, so the rule lives where it
    # can be tested.
    selectable: bool


class ForecastPanel(TypedDict):
    """The outlook window: one entry per day, the row's own day leading.

    Read by ``includes/_weather_day_picker.html`` (the seven cells) and by
    ``includes/_weather_day_line.html`` (the selected day's own line) — the
    same list, drawn twice on the location forecast page.
    """

    days: list[ForecastPanelDay]


def following_rows(weather: Weather) -> list[Weather]:
    """Return the rows after ``weather``'s day for the same place, in order.

    The outlook from a day is the next ``FORECAST_DAYS - 1`` days' rows
    (SNOW-1337): for today, the six days ahead the last fetch wrote; for a
    past day, the rows that stand for the days after it. A day with no row
    is simply absent.

    The icon is always the day variant (SNOW-1298): a tile summarises a
    whole day, as the map's symbol does, so the hour the reader looks says
    nothing about it.

    Args:
        weather: The lead day's row.

    Returns:
        Up to ``FORECAST_DAYS - 1`` rows, oldest day first.

    """
    return list(
        Weather.objects.filter(location_id=weather.location_id).window(
            weather.observed_on + datetime.timedelta(days=1), FORECAST_DAYS - 1
        )
    )


def _row_day_context(weather: Weather) -> ForecastPanelDay:
    """Build one outlook column from one row.

    The icon is always the day variant (SNOW-1298): a tile summarises a
    whole day, as the map's symbol does, so the hour the reader looks says
    nothing about it.

    Args:
        weather: The row for the day.

    Returns:
        The day's :class:`ForecastPanelDay`.

    """
    bucket = weather_code_icon_bucket(weather.weather_code)
    return ForecastPanelDay(
        date=weather.observed_on,
        weekday_label=weather.observed_on.strftime("%a"),
        icon_bucket=bucket,
        icon_filename=weather_icon_filename(bucket, "day"),
        condition_label=_ICON_BUCKET_LABEL[bucket],
        temp_max=weather.temperature_2m_max,
        temp_min=weather.temperature_2m_min,
        snowfall_sum=weather.snowfall_sum,
        freezing_level_height=weather.freezing_level_height,
        wind_speed_max=weather.wind_speed_10m_max,
        wind_bearing=weather.wind_direction_10m_dominant,
        precipitation_probability_max=weather.precipitation_probability_max,
        # Same rule as WeatherDisplay: the wall-clock time AT THE LOCATION,
        # in each day's own offset (local_zone), not the lead day's.
        sunrise_local=_clock(weather.sunrise, zone := local_zone(weather)),
        sunset_local=_clock(weather.sunset, zone),
        hourly=(hourly := list(weather.hourly or [])),
        selectable=bool(hourly),
    )


def build_point_forecast_panel(
    weather: Weather | None,
    following: Sequence[Weather] | None = None,
) -> ForecastPanel | None:
    """Build the multi-day outlook for one location from its rows.

    The row's own day leads, followed by the rows for the days after it
    (SNOW-1337), in date order.

    Args:
        weather: The lead day's row, or ``None``.
        following: The rows after the lead day, oldest first, when the
            caller already holds them. Defaults to None, which reads them
            with :func:`following_rows` — one query.

    Returns:
        A :class:`ForecastPanel`, or ``None`` when ``weather`` is ``None``.

    """
    if weather is None:
        return None
    rows = following_rows(weather) if following is None else following
    return ForecastPanel(days=[_row_day_context(row) for row in (weather, *rows)])


# ---------------------------------------------------------------------------
# Map weather feed
# ---------------------------------------------------------------------------


class PointWeatherDay(TypedDict):
    """One date's entry in the ``days`` dict of the map weather feed.

    Three keys and no more. The map draws a condition icon and a label: the
    icon is the filename served here (SNOW-1343 — the map no longer holds
    its own code table) and the label is the day's max temperature beside
    the station's altitude. ``code`` stays on the wire for any reader of the
    raw condition. Nothing else on the row reaches the map, so nothing else
    is serialised — the payload is fetched once and carries every location
    for a week.
    """

    code: int  # WMO weather interpretation code (0–99).
    icon: str  # Weather SVG basename, always the day variant.
    tmax: float | None  # Daily max air temp at 2m, °C.


def build_point_weather_days(
    weather: Weather | None,
    following: Sequence[Weather] = (),
) -> dict[str, PointWeatherDay]:
    """Project a lead row and the rows after it into the map feed's ``days``.

    Takes no ``now``: there is no day/night decision here. A map symbol
    summarises a whole calendar day, so where the set draws a day/night
    pair the map always takes the DAY variant — switching every station to
    a moon because the reader is up late would describe the reader, not the
    day.

    Args:
        weather: The lead day's row, or ``None``.
        following: The rows after it, oldest first. The map feed reads the
            whole week for every place in one query and hands each place's
            rows in here, so this never queries.

    Returns:
        A dict keyed by ISO date string (``"2026-08-30"``). Empty when
        ``weather`` is ``None``.

    """
    if weather is None:
        return {}
    return {
        row.observed_on.isoformat(): PointWeatherDay(
            code=row.weather_code,
            icon=weather_icon_filename(
                weather_code_icon_bucket(row.weather_code), "day"
            ),
            tmax=row.temperature_2m_max,
        )
        for row in (weather, *following)
    }
