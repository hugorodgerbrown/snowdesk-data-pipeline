"""
apps/weather/services/weather_display.py — Render-time helpers for ``Weather``.

Turns one ``apps.weather.models.Weather`` row — the day it is *of*, plus the
forward days in its ``forecast`` column — into the context dicts the
server-rendered surfaces read, and into the lean ``days`` dict the map's
weather feed serves.

Three consumers, one derivation:

  ``build_weather_display``      the single day on the bulletin masthead,
                                 the resort page and the favourite card.
  ``build_point_forecast_panel`` the multi-day outlook: the row's own day
                                 followed by every entry in ``forecast``.
  ``build_point_weather_days``   the map feed's ``{date: {code, tmax}}``
                                 projection (``apps.public.api``).

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
   grouping that selects a Meteocons SVG. Rain splits into drizzle / light /
   moderate / heavy and snow into light / moderate / heavy, so the icon tells
   the reader more than the colour band alone.

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

``_WMO_CODE_TO_ICON_BUCKET`` is mirrored in ``static/js/map_weather_core.js``
so the map can resolve an icon from the code the feed carries without a
second server round trip. The two tables are held together by
``tests/weather/services/test_icon_table_parity.py``, which parses the JS
table and asserts equality — a mirror with no guard is drift waiting to
happen.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, TypedDict

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

# Every icon bucket that ships separate day/night SVG variants. ``cloudy`` is
# the only bucket without a day/night distinction — it reads the same
# regardless of light, so it ships as a single ``cloudy.svg``.
WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT: frozenset[str] = frozenset(
    WEATHER_ICON_BUCKETS
) - {"cloudy"}

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

# Mirrored in static/js/map_weather_core.js — see the module docstring.
_WMO_CODE_TO_ICON_BUCKET: dict[int, str] = {
    0: "clear",
    1: "partly_cloudy",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "light_rain",
    63: "moderate_rain",
    65: "heavy_rain",
    66: "light_rain",
    67: "heavy_rain",
    71: "light_snow",
    73: "moderate_snow",
    75: "heavy_snow",
    77: "light_snow",
    80: "light_rain",
    81: "moderate_rain",
    82: "heavy_rain",
    85: "light_snow",
    86: "heavy_snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
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
    return _WMO_CODE_TO_ICON_BUCKET.get(code, DEFAULT_ICON_BUCKET)


def weather_icon_filename(icon_bucket: str, time_of_day: str) -> str:
    """Return the Meteocons SVG basename for a bucket and time of day.

    Buckets in :data:`WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT` ship separate
    day/night variants; ``cloudy`` is the lone exception that ships as one
    file regardless of light.

    Args:
        icon_bucket: One of the identifiers in :data:`WEATHER_ICON_BUCKETS`.
        time_of_day: ``"day"`` or ``"night"``.

    Returns:
        The SVG basename, e.g. ``"light_snow-day.svg"`` or ``"cloudy.svg"``.

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
    hourly: list[HourlyRow]  # That day's hourly rows, or [].


class ForecastPanel(TypedDict):
    """Context dict consumed by ``includes/_forecast_panel.html``."""

    days: list[ForecastPanelDay]


def _forecast_day_context(
    entry: ForecastDay, now: datetime.datetime
) -> ForecastPanelDay | None:
    """Build one outlook column from a ``forecast[]`` entry.

    ``hourly`` is **optional** on a forward day — only the first few entries
    carry one (see ``apps.weather.services.fetch``) — so it is read with
    ``.get()`` and falls back to an empty list rather than being assumed.

    Args:
        entry: One :class:`apps.weather.types.ForecastDay` dict.
        now: The reference instant for the day/night icon decision.

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
        # build_point_weather_days below): a day missing its weather_code is
        # as malformed as one missing its date, and must drop out the same
        # way rather than raising KeyError past the caller.
        icon_bucket = weather_code_icon_bucket(entry["weather_code"])
    except KeyError, TypeError, ValueError:
        return None
    time_of_day = "day" if _is_day_between(sunrise, sunset, now) else "night"
    return ForecastPanelDay(
        date=day_date,
        weekday_label=day_date.strftime("%a"),
        icon_bucket=icon_bucket,
        icon_filename=weather_icon_filename(icon_bucket, time_of_day),
        condition_label=_ICON_BUCKET_LABEL[icon_bucket],
        temp_max=entry.get("temperature_2m_max"),
        temp_min=entry.get("temperature_2m_min"),
        snowfall_sum=entry.get("snowfall_sum"),
        freezing_level_height=entry.get("freezing_level_height"),
        wind_speed_max=entry.get("wind_speed_10m_max"),
        wind_bearing=entry.get("wind_direction_10m_dominant"),
        # NotRequired on ForecastDay — a forward day past HOURLY_DAYS has no
        # 'hourly' key at all, so this is a presence check, not a null check.
        hourly=list(entry.get("hourly") or []),
    )


def build_point_forecast_panel(
    weather: "Weather | None", now: datetime.datetime
) -> ForecastPanel | None:
    """Build the multi-day outlook for one location from one row.

    The row's own day leads, followed by each entry in its ``forecast``
    column in stored order — which is the provider's own ``daily.time``
    order, ascending (SNOW-466). No second query: the forward days were
    written alongside the day they were known on, which is the whole point
    of the column.

    Args:
        weather: The row whose day and forward days to render, or ``None``.
        now: The reference instant for each day's day/night icon decision.

    Returns:
        A :class:`ForecastPanel`, or ``None`` when ``weather`` is ``None``.

    """
    if weather is None:
        return None
    lead_time_of_day = "day" if is_day(weather, now) else "night"
    lead_bucket = weather_code_icon_bucket(weather.weather_code)
    days: list[ForecastPanelDay] = [
        ForecastPanelDay(
            date=weather.observed_on,
            weekday_label=weather.observed_on.strftime("%a"),
            icon_bucket=lead_bucket,
            icon_filename=weather_icon_filename(lead_bucket, lead_time_of_day),
            condition_label=_ICON_BUCKET_LABEL[lead_bucket],
            temp_max=weather.temperature_2m_max,
            temp_min=weather.temperature_2m_min,
            snowfall_sum=weather.snowfall_sum,
            freezing_level_height=weather.freezing_level_height,
            wind_speed_max=weather.wind_speed_10m_max,
            wind_bearing=weather.wind_direction_10m_dominant,
            hourly=list(weather.hourly or []),
        )
    ]
    for entry in weather.forecast or []:
        day = _forecast_day_context(entry, now)
        if day is not None:
            days.append(day)
    return ForecastPanel(days=days)


# ---------------------------------------------------------------------------
# Map weather feed
# ---------------------------------------------------------------------------


#: Hours between the rows a thinned hourly series keeps. Three is the
#: granularity most public forecasts publish at, so 00:00, 03:00, 06:00 …
#: reads as a normal outlook rather than as a series with gaps in it.
HOURLY_THIN_STEP_HOURS = 3


def _hour_of(row: HourlyRow) -> int | None:
    """Read the local hour out of an hourly row's ``time`` string.

    ``time`` is Open-Meteo's own ISO-8601 local string, not a datetime —
    the same fixed format ``_forecast_hourly_body.html`` slices for display.

    Args:
        row: The hourly row to read.

    Returns:
        The hour as ``0``–``23``, or ``None`` when ``time`` is absent or
        does not parse.

    """
    # ``time`` is a required key on the TypedDict, but these rows come out
    # of a JSON column rather than a constructor — the contract is a
    # description of what is written, not something the DB enforces. The
    # KeyError/TypeError arms are for a row that predates or violates it.
    try:
        return datetime.datetime.fromisoformat(row["time"]).hour
    except KeyError, TypeError, ValueError:
        return None


def thin_panel_hourly(
    panel: ForecastPanel, step_hours: int = HOURLY_THIN_STEP_HOURS
) -> ForecastPanel:
    """Return the panel with every day's hourly series thinned by step.

    **This is a payload decision, not a display one.** The hourly rows are
    ~79% of the map sheet's HTML — 48 rows at roughly 1.2 kB each, because
    every row carries its own utility classes — and they arrive on a tap,
    not on a page load. An hour-by-hour series is the right resolution on
    the resort page, which is a destination; it is the wrong one behind a
    map symbol, where the question is what the day looks like.

    Rows are kept on the WALL-CLOCK hour rather than by position, so a
    series that starts late still lines up on 00:00/03:00/06:00 and two
    days never disagree about which hours they show. A row whose ``time``
    will not parse is kept rather than dropped — losing a reading is worse
    than showing one off-step.

    Does not mutate ``panel``: the caller may be holding a panel that other
    surfaces render at full resolution.

    Args:
        panel: The panel to thin.
        step_hours: Keep rows whose hour is a multiple of this. ``1`` or
            less returns an equivalent panel unthinned.

    Returns:
        A new :class:`ForecastPanel` whose days carry the kept rows.

    """
    if step_hours <= 1:
        return ForecastPanel(days=list(panel["days"]))
    days: list[ForecastPanelDay] = []
    for day in panel["days"]:
        kept = [
            row
            for row in day["hourly"]
            if (hour := _hour_of(row)) is None or hour % step_hours == 0
        ]
        days.append(ForecastPanelDay({**day, "hourly": kept}))
    return ForecastPanel(days=days)


class PointWeatherDay(TypedDict):
    """One date's entry in the ``days`` dict of the map weather feed.

    Two keys and no more. The map draws a condition icon and a label; the
    icon is resolved from ``code`` client-side (``map_weather_core.js``) and
    the label is the day's max temperature beside the station's altitude.
    Nothing else on the row reaches the map, so nothing else is serialised —
    the payload is fetched once and carries every location for a week.
    """

    code: int  # WMO weather interpretation code (0–99).
    tmax: float | None  # Daily max air temp at 2m, °C.


def build_point_weather_days(
    weather: "Weather | None",
) -> dict[str, PointWeatherDay]:
    """Project one row and its forward days into the map feed's ``days`` dict.

    Takes no ``now``: there is no day/night decision here. The feed carries
    the code, and the map resolves the icon at draw time.

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
            code=code, tmax=entry.get("temperature_2m_max")
        )
    return days
