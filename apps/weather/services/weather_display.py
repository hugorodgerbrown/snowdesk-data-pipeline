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

``build_hourly_chart`` sits under the outlook: it turns one day's hourly
series into the SVG geometry ``includes/_forecast_hourly_chart.html`` draws
(SNOW-776). The arithmetic is here, in Python, because the chart carries no
JavaScript — it renders under the service worker offline, and its geometry
is covered by pytest rather than by a browser test.

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
    )


# ---------------------------------------------------------------------------
# Hourly chart
# ---------------------------------------------------------------------------

# Geometry for one day's meteogram, in SVG user units. ONE fixed viewBox for
# every chart, so the three bands line up from day to day and the drawing
# scales to whatever width the card gives it.
#
# The x axis is always a full 24 hours: an hour's position comes from the
# hour parsed out of its ``time`` string, never from its index in the list.
# A series missing 03:00 therefore leaves a gap at 03:00 rather than
# shifting every later hour an hour to the left.
CHART_WIDTH = 240.0
CHART_HEIGHT = 176.0
HOURS_PER_DAY = 24
HOUR_WIDTH = CHART_WIDTH / HOURS_PER_DAY
BAR_WIDTH = 6.0

# Floor for a drawn bar, so an hour that has a value but nearly no magnitude
# (0 °C against the isotherm, 0.05 mm of drizzle) still marks the axis. It
# applies only to bars that are drawn at all — a null hour contributes none.
MIN_BAR_HEIGHT = 1.0

# The three bands, top to bottom: temperature (tall, it carries the
# freezing-level overlay too), precipitation, wind.
TEMPERATURE_TOP = 4.0
TEMPERATURE_HEIGHT = 84.0
PRECIPITATION_TOP = 96.0
PRECIPITATION_HEIGHT = 26.0
WIND_TOP = 130.0
WIND_HEIGHT = 26.0

# Hour ticks under the wind band, every third hour.
AXIS_LABEL_Y = 170.0
AXIS_LABEL_INTERVAL = 3

# The variables a chart can draw. A day whose every hour is null across all
# five has nothing to show, and produces no chart rather than an empty frame.
_CHART_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "freezing_level_height",
)


class TemperatureBar(TypedDict):
    """One hour's temperature bar, drawn from the zero isotherm."""

    x: float
    y: float
    width: float
    height: float
    is_warm: bool  # At or above 0 °C — the bar rises rather than hangs.


class PrecipitationBar(TypedDict):
    """One hour's precipitation bar, hanging from the top of its band."""

    x: float
    y: float
    width: float
    height: float
    is_snow: bool  # That hour's ``snowfall`` is non-zero.


class TemperatureBand(TypedDict):
    """Band A — temperature bars, with the freezing level laid over them.

    The bars and the line have **different scales**: one is °C and one is
    metres, and forcing them onto a shared axis would be meaningless. The
    bar scale always includes 0 °C so ``zero_y`` sits inside the band and a
    below-zero day reads as below zero rather than as short bars.

    ``freezing_level`` is a list of ``points`` strings — one per unbroken
    run of hours — rather than a single string, so the line BREAKS across a
    null hour instead of interpolating straight through it.
    """

    top: float
    height: float
    zero_y: float
    bars: list[TemperatureBar]
    freezing_level: list[str]
    temp_max: float | None
    temp_min: float | None
    freezing_max: float | None
    freezing_min: float | None


class PrecipitationBand(TypedDict):
    """Band B — precipitation bars hanging from the top of the band.

    An hour with no precipitation contributes no bar: a row of zero-height
    stubs down a dry day is noise, and the empty band already says "dry".
    """

    top: float
    height: float
    bars: list[PrecipitationBar]
    total: float | None
    max_value: float | None


class WindBand(TypedDict):
    """Band C — wind speed, gusts above it, and the gap between shaded.

    The gap IS the gustiness, which is the reason the band exists. Every
    entry in the three lists is a run of unbroken hours, so all three break
    across a null hour rather than spanning it.
    """

    top: float
    height: float
    speed: list[str]
    gusts: list[str]
    gust_gap: list[str]  # Polygon ``points`` strings, gust over speed.
    max_value: float | None


class HourLabel(TypedDict):
    """One x-axis tick under the wind band."""

    x: float
    label: str  # "00", "03", … — numeric, so it needs no translation.


class HourlyChart(TypedDict):
    """One day's meteogram: three bands sharing one x axis."""

    view_box: str
    width: float
    height: float
    label_y: float  # Baseline the hour ticks sit on, under the wind band.
    temperature: TemperatureBand
    precipitation: PrecipitationBand
    wind: WindBand
    hour_labels: list[HourLabel]


def _chart_hour(row: HourlyRow) -> int | None:
    """Return the local hour an hourly row belongs to.

    ``time`` is the provider's own local ISO-8601 string, so the hour it
    carries is the hour as experienced at the location — which is the hour
    the chart plots against.

    Args:
        row: One :class:`apps.weather.types.HourlyRow`.

    Returns:
        The hour 0–23, or ``None`` when ``time`` is missing or unparseable —
        one malformed hour drops out of the chart rather than taking it out.

    """
    try:
        return datetime.datetime.fromisoformat(row["time"]).hour
    except KeyError, TypeError, ValueError:
        return None


def _hour_centre(hour: int) -> float:
    """Return the x centre of one hour's slot, in SVG user units.

    Args:
        hour: The hour 0–23.

    Returns:
        The x coordinate.

    """
    return round(hour * HOUR_WIDTH + HOUR_WIDTH / 2, 1)


def _project(value: float, low: float, high: float, top: float, height: float) -> float:
    """Map one value onto a band's y axis, ``high`` at the top of the band.

    A flat series (``high == low``) is drawn down the middle of its band
    rather than dividing by zero — a windless day and a day of constant
    30 km/h both have something true to show, and neither has a range.

    Args:
        value: The value to place.
        low: The bottom of the value domain.
        high: The top of the value domain.
        top: The band's top edge, in user units.
        height: The band's height, in user units.

    Returns:
        The y coordinate, rounded to one decimal place.

    """
    span = high - low
    if span <= 0:
        return round(top + height / 2, 1)
    return round(top + (high - value) / span * height, 1)


def _runs[T](points: list[tuple[float, T | None]]) -> list[list[tuple[float, T]]]:
    """Split a series into its unbroken runs of present values.

    Generic in the value type so the wind band can run it over a
    ``(gust, speed)`` pair and get the same break behaviour the two single
    lines get, without a second copy of this loop.

    Args:
        points: ``(x, value)`` pairs in x order, ``value`` ``None`` where
            the hour is missing or its value is null.

    Returns:
        One list per run of two or more consecutive present values. A lone
        point cannot draw a line, so it contributes no run.

    """
    runs: list[list[tuple[float, T]]] = []
    current: list[tuple[float, T]] = []
    for x, y in points:
        if y is None:
            if len(current) > 1:
                runs.append(current)
            current = []
            continue
        current.append((x, y))
    if len(current) > 1:
        runs.append(current)
    return runs


def _polyline(run: list[tuple[float, float]]) -> str:
    """Format one run as an SVG ``points`` string.

    Formatted here rather than in the template because ``points`` is a
    single attribute value, and because a formatted string cannot be
    localised into ``12,5`` by a non-English active locale.

    Args:
        run: ``(x, y)`` pairs.

    Returns:
        ``"x,y x,y …"``.

    """
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in run)


def _temperature_band(by_hour: dict[int, HourlyRow]) -> TemperatureBand:
    """Build band A from one day's hours.

    Args:
        by_hour: The day's rows keyed by their local hour.

    Returns:
        The :class:`TemperatureBand`.

    """
    temps = {hour: row.get("temperature_2m") for hour, row in by_hour.items()}
    present = [value for value in temps.values() if value is not None]
    # 0 °C is always in the domain, so the isotherm is always on the chart.
    high = max([*present, 0.0])
    low = min([*present, 0.0])
    zero_y = _project(0.0, low, high, TEMPERATURE_TOP, TEMPERATURE_HEIGHT)
    bars: list[TemperatureBar] = []
    for hour in sorted(temps):
        value = temps[hour]
        if value is None:
            continue
        value_y = _project(value, low, high, TEMPERATURE_TOP, TEMPERATURE_HEIGHT)
        bars.append(
            TemperatureBar(
                x=round(_hour_centre(hour) - BAR_WIDTH / 2, 1),
                y=min(value_y, zero_y),
                width=BAR_WIDTH,
                height=max(abs(value_y - zero_y), MIN_BAR_HEIGHT),
                is_warm=value >= 0,
            )
        )
    freezing = {hour: row.get("freezing_level_height") for hour, row in by_hour.items()}
    freezing_present = [value for value in freezing.values() if value is not None]
    freezing_high = max(freezing_present) if freezing_present else 0.0
    freezing_low = min(freezing_present) if freezing_present else 0.0

    def place(value: float | None) -> float | None:
        """Project one freezing level onto the band, or pass a null through."""
        if value is None:
            return None
        return _project(
            value, freezing_low, freezing_high, TEMPERATURE_TOP, TEMPERATURE_HEIGHT
        )

    line: list[tuple[float, float | None]] = [
        (_hour_centre(hour), place(freezing.get(hour))) for hour in range(HOURS_PER_DAY)
    ]
    return TemperatureBand(
        top=TEMPERATURE_TOP,
        height=TEMPERATURE_HEIGHT,
        zero_y=zero_y,
        bars=bars,
        freezing_level=[_polyline(run) for run in _runs(line)],
        temp_max=max(present) if present else None,
        temp_min=min(present) if present else None,
        freezing_max=freezing_high if freezing_present else None,
        freezing_min=freezing_low if freezing_present else None,
    )


def _precipitation_band(by_hour: dict[int, HourlyRow]) -> PrecipitationBand:
    """Build band B from one day's hours.

    Args:
        by_hour: The day's rows keyed by their local hour.

    Returns:
        The :class:`PrecipitationBand`.

    """
    values = {hour: row.get("precipitation") for hour, row in by_hour.items()}
    present = [value for value in values.values() if value is not None]
    high = max(present) if present else 0.0
    bars: list[PrecipitationBar] = []
    for hour in sorted(values):
        value = values[hour]
        if not value or high <= 0:
            continue
        height = max(value / high * PRECIPITATION_HEIGHT, MIN_BAR_HEIGHT)
        bars.append(
            PrecipitationBar(
                x=round(_hour_centre(hour) - BAR_WIDTH / 2, 1),
                y=PRECIPITATION_TOP,
                width=BAR_WIDTH,
                height=round(height, 1),
                is_snow=bool(by_hour[hour].get("snowfall")),
            )
        )
    return PrecipitationBand(
        top=PRECIPITATION_TOP,
        height=PRECIPITATION_HEIGHT,
        bars=bars,
        total=round(sum(present), 1) if present else None,
        max_value=high if present else None,
    )


def _wind_band(by_hour: dict[int, HourlyRow]) -> WindBand:
    """Build band C from one day's hours.

    Both lines share one scale anchored at 0 km/h, because the vertical
    distance between them is the reading — a gust line on its own scale
    would put the same gap above every speed.

    Args:
        by_hour: The day's rows keyed by their local hour.

    Returns:
        The :class:`WindBand`.

    """
    speeds = {hour: row.get("wind_speed_10m") for hour, row in by_hour.items()}
    gusts = {hour: row.get("wind_gusts_10m") for hour, row in by_hour.items()}
    present = [
        value for value in (*speeds.values(), *gusts.values()) if value is not None
    ]
    high = max(present) if present else 0.0

    def place(value: float | None) -> float | None:
        """Project one wind value onto the band, or pass a null through."""
        if value is None:
            return None
        return _project(value, 0.0, high, WIND_TOP, WIND_HEIGHT)

    speed_points: list[tuple[float, float | None]] = [
        (_hour_centre(hour), place(speeds.get(hour))) for hour in range(HOURS_PER_DAY)
    ]
    gust_points: list[tuple[float, float | None]] = [
        (_hour_centre(hour), place(gusts.get(hour))) for hour in range(HOURS_PER_DAY)
    ]
    # The shaded gap needs BOTH values for the same hour, so it breaks
    # wherever EITHER line does.
    paired: list[tuple[float, tuple[float, float] | None]] = [
        (x, None if speed_y is None or gust_y is None else (gust_y, speed_y))
        for (x, gust_y), (_, speed_y) in zip(gust_points, speed_points, strict=True)
    ]
    # Each polygon walks the gust line forwards and the speed line back.
    gaps = [
        _polyline(
            [(x, gust_y) for x, (gust_y, _) in run]
            + [(x, speed_y) for x, (_, speed_y) in reversed(run)]
        )
        for run in _runs(paired)
    ]
    return WindBand(
        top=WIND_TOP,
        height=WIND_HEIGHT,
        speed=[_polyline(run) for run in _runs(speed_points)],
        gusts=[_polyline(run) for run in _runs(gust_points)],
        gust_gap=gaps,
        max_value=high if present else None,
    )


def build_hourly_chart(hourly: list[HourlyRow]) -> HourlyChart | None:
    """Build one day's meteogram from its hourly series.

    Everything is computed here rather than in the browser: the chart is
    inline SVG with no JavaScript, so it draws under the service worker
    offline and its arithmetic is covered by pytest rather than by a
    browser test.

    Args:
        hourly: The day's :class:`apps.weather.types.HourlyRow` list.

    Returns:
        An :class:`HourlyChart`, or ``None`` when there is nothing to draw —
        an empty series, a series whose every ``time`` is unparseable, or a
        day whose every value is null. An empty frame says less than no
        frame does.

    """
    by_hour: dict[int, HourlyRow] = {}
    for row in hourly:
        hour = _chart_hour(row)
        if hour is not None:
            by_hour[hour] = row
    if not by_hour:
        return None
    if not any(
        row.get(variable) is not None
        for row in by_hour.values()
        for variable in _CHART_VARIABLES
    ):
        return None
    return HourlyChart(
        view_box=f"0 0 {CHART_WIDTH:.0f} {CHART_HEIGHT:.0f}",
        width=CHART_WIDTH,
        height=CHART_HEIGHT,
        label_y=AXIS_LABEL_Y,
        temperature=_temperature_band(by_hour),
        precipitation=_precipitation_band(by_hour),
        wind=_wind_band(by_hour),
        hour_labels=[
            HourLabel(x=_hour_centre(hour), label=f"{hour:02d}")
            for hour in range(0, HOURS_PER_DAY, AXIS_LABEL_INTERVAL)
        ],
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
    hourly: list[HourlyRow]  # That day's hourly rows, or [].
    chart: HourlyChart | None  # That day's meteogram, or None past the horizon.
    is_focus: bool  # The day the panel opens on — the first with a chart.


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
    # NotRequired on ForecastDay — a forward day past HOURLY_DAYS has no
    # 'hourly' key at all, so this is a presence check, not a null check.
    hourly = list(entry.get("hourly") or [])
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
        hourly=hourly,
        chart=build_hourly_chart(hourly),
        is_focus=False,
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
    lead_hourly = list(weather.hourly or [])
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
            hourly=lead_hourly,
            chart=build_hourly_chart(lead_hourly),
            is_focus=False,
        )
    ]
    for entry in weather.forecast or []:
        day = _forecast_day_context(entry, now)
        if day is not None:
            days.append(day)
    # The panel opens on the first day it can actually draw, which is
    # normally the lead day but need not be: a row whose own hourly column
    # is empty still has tomorrow's series nested in its forecast.
    for day in days:
        if day["chart"] is not None:
            day["is_focus"] = True
            break
    return ForecastPanel(days=days)


# ---------------------------------------------------------------------------
# Map weather feed
# ---------------------------------------------------------------------------


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
