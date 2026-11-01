"""
apps/weather/services/weather_display.py — Render-time helpers for ``Weather``.

Turns one ``apps.weather.models.Weather`` row — the day it is *of*, plus the
forward days in its ``forecast`` column — into the context dicts the
server-rendered surfaces read, and into the lean ``days`` dict the map's
weather feed serves.

Three consumers, one derivation:

  ``build_weather_display``      the single day on the favourite card and
                                 the help page's weather illustration.
  ``build_point_forecast_panel`` the multi-day outlook: the row's own day
                                 followed by every entry in ``forecast``.
                                 Drives both the day picker and the
                                 selected-day line on the location
                                 forecast page.
  ``build_point_weather_days``   the map feed's ``{date: {code, icon,
                                 tmax}}`` projection (``apps.public.api``).

**One row in, not a list.** The pre-SNOW-762 version of this module took a
``WeatherSnapshot | ForecastCellWeather`` and hedged every field read behind
``getattr(..., None)`` because the two models did not agree on what they
carried. There is one model now, so every read here is a direct attribute
access and the forward days come from that row's own ``forecast`` list
rather than from N sibling rows. The caller's read path is always
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
``apps.ingest.normalise.weather_icons`` (SNOW-1343), which writes each
forward day's ``icon_bucket`` into the ``forecast[]`` entry it stores. A
reader here prefers that stored bucket and falls back to deriving one from
``weather_code`` — every day stored before SNOW-1343 lacks the key, and no
past row is rewritten. The row's own day has no stored bucket (there is no
column for it) and always derives. The map no longer carries a mirror of the
table: the feed serves each day's ``icon`` filename. Which variant of a
day/night pair a panel shows is still decided here, at read time, from the
day's stored sunrise and sunset.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, TypedDict

from apps.ingest.public import (
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,
    icon_bucket_for_code,
)
from apps.weather.types import ForecastDay, HourlyRow

if TYPE_CHECKING:
    from apps.weather.models import Weather


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


def _stored_icon_bucket(entry: ForecastDay) -> str:
    """Return a forward day's icon bucket, preferring the stored one.

    A day stored since SNOW-1343 carries the service's ``icon_bucket``; one
    stored before it does not, and is never rewritten, so it falls back to
    deriving the bucket from ``weather_code``. A stored value naming no
    known drawing is treated as absent rather than trusted.

    Args:
        entry: One :class:`apps.weather.types.ForecastDay` dict.

    Returns:
        One of the bucket identifiers in :data:`WEATHER_ICON_BUCKETS`.

    Raises:
        KeyError: When the entry has neither a usable ``icon_bucket`` nor a
            ``weather_code`` — the caller drops such a day as malformed.

    """
    stored = entry.get("icon_bucket")
    if stored is not None and stored in _ICON_BUCKET_LABEL:
        return stored
    return weather_code_icon_bucket(entry["weather_code"])


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
# Day / night
# ---------------------------------------------------------------------------


def _is_day_between(
    sunrise: datetime.datetime,
    sunset: datetime.datetime,
    now: datetime.datetime,
) -> bool:
    """Return whether the wall-clock ``now`` sits inside the day window.

    The shared body of :func:`is_day` and the per-forecast-day decision in
    :func:`build_point_forecast_panel`, which reads its sunrise/sunset off a
    ``ForecastDay`` rather than off a model instance. The rule is identical
    in both, so it is written once.

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
    sunrise_local: str  # "HH:MM" in the row's stored offset.
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
        # Formatted in the row's stored offset so the strip shows the
        # wall-clock time of sunrise/sunset AT THE LOCATION, not in the
        # Django-active TIME_ZONE.
        sunrise_local=weather.sunrise.strftime("%H:%M"),
        sunset_local=weather.sunset.strftime("%H:%M"),
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
    # SNOW-789: the day's daylight window, "HH:MM" in the day's OWN stored
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
    # hourly series, NOT truthiness of some other field — a forward day
    # past HOURLY_DAYS has no 'hourly' key at all, and an empty list is
    # equally "no detail to show". Kept as its own field rather than left
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


def _forecast_day_context(entry: ForecastDay) -> ForecastPanelDay | None:
    """Build one outlook column from a ``forecast[]`` entry.

    ``hourly`` is **optional** on a forward day — only the first few entries
    carry one (see ``apps.weather.services.fetch``) — so it is read with
    ``.get()`` and falls back to an empty list rather than being assumed.

    The icon is always the day variant (SNOW-1298): a tile summarises a
    whole day, as the map's symbol does, so the hour the reader looks says
    nothing about it.

    Args:
        entry: One :class:`apps.weather.types.ForecastDay` dict.

    Returns:
        A :class:`ForecastPanelDay`, or ``None`` when the entry's ``date``,
        ``sunrise``, ``sunset`` or ``weather_code`` cannot be read — a
        malformed forward day drops out of the strip rather than taking the
        page with it.

    """
    try:
        day_date = datetime.date.fromisoformat(entry["date"])
        sunrise = datetime.datetime.fromisoformat(entry["sunrise"])
        sunset = datetime.datetime.fromisoformat(entry["sunset"])
        # Inside the guard alongside the date parsing (and matching
        # build_point_weather_days below): a day with neither a stored
        # icon_bucket nor a weather_code is as malformed as one missing its
        # date, and must drop out the same way rather than raising KeyError
        # past the caller.
        icon_bucket = _stored_icon_bucket(entry)
    except KeyError, TypeError, ValueError:
        return None
    return ForecastPanelDay(
        date=day_date,
        weekday_label=day_date.strftime("%a"),
        icon_bucket=icon_bucket,
        icon_filename=weather_icon_filename(icon_bucket, "day"),
        condition_label=_ICON_BUCKET_LABEL[icon_bucket],
        temp_max=entry.get("temperature_2m_max"),
        temp_min=entry.get("temperature_2m_min"),
        snowfall_sum=entry.get("snowfall_sum"),
        freezing_level_height=entry.get("freezing_level_height"),
        wind_speed_max=entry.get("wind_speed_10m_max"),
        wind_bearing=entry.get("wind_direction_10m_dominant"),
        precipitation_probability_max=entry.get("precipitation_probability_max"),
        # Formatted off the already-parsed locals, so the offset shown is
        # the one the provider stamped on THAT day — not the Django-active
        # TIME_ZONE, and not the lead day's.
        sunrise_local=sunrise.strftime("%H:%M"),
        sunset_local=sunset.strftime("%H:%M"),
        # NotRequired on ForecastDay — a forward day past HOURLY_DAYS has no
        # 'hourly' key at all, so this is a presence check, not a null check.
        hourly=(hourly := list(entry.get("hourly") or [])),
        selectable=bool(hourly),
    )


def build_point_forecast_panel(weather: "Weather | None") -> ForecastPanel | None:
    """Build the multi-day outlook for one location from one row.

    The row's own day leads, followed by each entry in its ``forecast``
    column in stored order — which is the provider's own ``daily.time``
    order, ascending (SNOW-466). No second query: the forward days were
    written alongside the day they were known on, which is the whole point
    of the column.

    Args:
        weather: The row whose day and forward days to render, or ``None``.

    Returns:
        A :class:`ForecastPanel`, or ``None`` when ``weather`` is ``None``.

    """
    if weather is None:
        return None
    lead_bucket = weather_code_icon_bucket(weather.weather_code)
    days: list[ForecastPanelDay] = [
        ForecastPanelDay(
            date=weather.observed_on,
            weekday_label=weather.observed_on.strftime("%a"),
            icon_bucket=lead_bucket,
            icon_filename=weather_icon_filename(lead_bucket, "day"),
            condition_label=_ICON_BUCKET_LABEL[lead_bucket],
            temp_max=weather.temperature_2m_max,
            temp_min=weather.temperature_2m_min,
            snowfall_sum=weather.snowfall_sum,
            freezing_level_height=weather.freezing_level_height,
            wind_speed_max=weather.wind_speed_10m_max,
            wind_bearing=weather.wind_direction_10m_dominant,
            precipitation_probability_max=weather.precipitation_probability_max,
            # Same rule as WeatherDisplay: the wall-clock time AT THE
            # LOCATION, in the row's stored offset.
            sunrise_local=weather.sunrise.strftime("%H:%M"),
            sunset_local=weather.sunset.strftime("%H:%M"),
            hourly=(lead_hourly := list(weather.hourly or [])),
            selectable=bool(lead_hourly),
        )
    ]
    for entry in weather.forecast or []:
        day = _forecast_day_context(entry)
        if day is not None:
            days.append(day)
    return ForecastPanel(days=days)


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
    weather: "Weather | None",
) -> dict[str, PointWeatherDay]:
    """Project one row and its forward days into the map feed's ``days`` dict.

    Takes no ``now``: there is no day/night decision here. A map symbol
    summarises a whole calendar day, so where the set draws a day/night
    pair the map always takes the DAY variant — switching every station to
    a moon because the reader is up late would describe the reader, not the
    day. A forward day's bucket is its stored ``icon_bucket`` when it has
    one, else derived from its ``weather_code``; the row's own day always
    derives.

    Args:
        weather: The row to project, or ``None``.

    Returns:
        A dict keyed by ISO date string (``"2026-08-30"``). Empty when
        ``weather`` is ``None``.

    """
    if weather is None:
        return {}
    days: dict[str, PointWeatherDay] = {
        weather.observed_on.isoformat(): PointWeatherDay(
            code=weather.weather_code,
            icon=weather_icon_filename(
                weather_code_icon_bucket(weather.weather_code), "day"
            ),
            tmax=weather.temperature_2m_max,
        )
    }
    for entry in weather.forecast or []:
        try:
            date_key = datetime.date.fromisoformat(entry["date"]).isoformat()
            code = int(entry["weather_code"])
        except KeyError, TypeError, ValueError:
            continue
        days[date_key] = PointWeatherDay(
            code=code,
            icon=weather_icon_filename(_stored_icon_bucket(entry), "day"),
            tmax=entry.get("temperature_2m_max"),
        )
    return days
