"""
apps/weather/services/hourly_chart.py — one day's hourly series as chart geometry.

Turns a list of ``apps.weather.types.HourlyRow`` into the coordinate strings
``includes/_hourly_chart.html`` renders, replacing the 24-row table that
``_forecast_hourly_body.html`` draws today. The chart is server-rendered
inline SVG: no client-side charting library, no data fetch, and nothing to
run before the shape is on the page.

**Transportable by construction.** ``build_hourly_chart`` takes a plain
mapping — the day's own keys plus its ``hourly`` list — and never touches
the ORM, ``settings`` or the request. Anything that can produce that mapping
can render the chart: the component library's fixtures, a ``Weather`` row's
own day, an entry in its ``forecast`` column, or a JSON payload posted from
somewhere else entirely.

Three charts, one x-axis
------------------------
This is not one chart with three bands. It is **three charts stacked**, each
with its own header, its own summary, its own vertical scale and its own
resolution. They share the time axis and nothing else, and the layout says
so rather than implying a single plot that happens to be tall:

  TEMPERATURE     Air temperature at the station, and the altitude at which
                  the air reaches 0 °C. Two scales, °C on the left and
                  metres on the right, plus two rules — the fixed 0 °C
                  reference and the variable station elevation. **Hourly.**
  PRECIPITATION   New snow in centimetres and precipitation in millimetres,
                  each on its own baseline. **Hourly.**
  WIND            Sustained speed, gusts and the direction the wind comes
                  from. **Three-hourly** — a gust is a peak over a span, and
                  a bearing is only meaningful averaged over one.

The resolutions differ because the quantities do, and pretending otherwise
would be the dishonest part. A temperature is an instant; a gust is a worst
case over a window.

Hourly lines, three-hourly columns
----------------------------------
The two resolutions are deliberate and they do different jobs.

**Lines are drawn at all 24 hours** — temperature, freezing level, wind and
gusts each plot a point per hour. A line is read as a shape, and a shape
built from eight points loses exactly what the reader is looking for: the
hour the temperature crossed zero, the sharpness of a frontal wind rise.
Those cost nothing to draw, because a line needs no room per point.

**The accumulation bars are hourly too**, with a single figure on the
tallest — see ``_bars`` for why one number beats twenty-four or eight.

**The wind figures and direction arrows stay on eight three-hour blocks.**
These need horizontal room, and 24 columns of them do not fit the content
column at a legible size.

**The axis marks clock instants**, not columns. Three positioning helpers,
and confusing them is how a chart lies about time:

  ``_hour_x(h)``     the centre of hour *h*'s slot — where a value measured
                     over that hour belongs.
  ``_block_x(b)``    the centre of block *b*, equal to ``_hour_x`` of its
                     middle hour — where a three-hour aggregate belongs.
  ``_instant_x(h)``  the moment hour *h* begins — where a tick belongs.

``_block_x(0)`` and ``_instant_x(0)`` differ by 32.5 units, an hour and a
half. The axis labels were drawn at block centres until the bars went
hourly, which put "00:00" where 01:30 falls and made every reading taken off
the temperature curve late by that much.

Aggregation, where a block still needs one value, is by the operation that
is true of the quantity — sum for accumulations, max for wind, circular mean
for bearings (see ``_circular_mean``).

Scales are derived, never fixed
-------------------------------
Both vertical domains come from the data. The design handoff originally
specified fixed domains (-6…+4 °C, 1000…2600 m) so that two days would be
directly comparable, but the three real days committed under
``apps/weather/sample_days/`` show what that costs: a January high sits
entirely below the temperature floor and an April thaw entirely above its
ceiling, and both would have rendered as a flat line pinned to the edge of
the box. A chart that cannot draw two of three real days is not comparable,
it is broken.

Three consequences of deriving the scale, all deliberate:

* The **0 °C reference line** is drawn only when zero falls inside the
  temperature domain. On a day that never approaches freezing there is no
  crossing to mark, and forcing zero into the domain would flatten the line
  that carries the actual information.
* The **station elevation line** is drawn only when the location's own
  elevation is near the day's freezing levels — precisely, when including it
  would not stretch the domain past ``_ELEVATION_DOMAIN_LIMIT`` times the
  range the data itself needs. On the spring-thaw day the freezing level
  runs 1500 m above the village all day; drawing that gap to scale would
  compress the day's own 300 m of movement into nothing.
* A **dry day collapses its two accumulation bands entirely** rather than
  drawing two empty rulers, and says so in words instead. See
  ``_precipitation_layer``.

The light lives in the axis
---------------------------
SNOW-723 shaded the hours before sunrise and after sunset across all three
plots, then removed the wash: a bar is a bar and a gust is a gust whether
the sun is up or not, so the shading earned its place on the temperature
plot and earned nothing on the other two.

SNOW-790 brings sunrise and sunset back, in the one row that answers *when*
rather than *how much* — the temperature chart's **hour axis**, thickened
into a bar carrying a lit segment between them. This is the same finding
read the other way round: the light is a property of the axis, not of the
data, so it belongs in the axis furniture rather than over the series. The
precipitation and wind axes keep their bare label rows, their charts having
no more use for the sun than they had for the wash.

The current time joins it there as a pin crossing the same bar. It used to
be a full-height hairline through all four plots, crossing every series —
the one mark on the drawing that was not data but was drawn like it.

Both are read from the day by ``_daylight`` and ``_now_hour``, which between
them tolerate every shape a caller passes: ``sunrise_local``/``sunset_local``
as ``"HH:MM"`` (what ``apps.weather.services.weather_display`` puts on a
``ForecastPanelDay``) and ``sunrise``/``sunset`` as an ISO string or a
``datetime`` (what the committed sample days and a ``Weather`` row carry).
A day that supplies neither loses its band, not its chart.

Wind arrows point at the source
-------------------------------
An arrow is rotated to its **source** bearing: a 270° wind (from the west)
draws an arrowhead pointing west, at the weather coming towards you. The
base glyph points north at zero rotation, so the rotation applied is the
bearing itself.

``apps.public.templatetags.weather_tags``'s ``wind_arrow_rotation`` now
does the same. It used to add 180° so its glyph flew downwind, which meant
the two conventions could not share a page — SNOW-785 settled it on the
source, following this module's design handoff and the legend copy it ships
with ("arrows point where the wind comes from"). ``tests/public/
test_weather_tags.py`` asserts the two agree for the same bearing, so they
cannot drift apart again.

Labels are HTML, positioned by percentage
-----------------------------------------
Every number, unit and axis label is HTML positioned over the SVG, not
``<text>`` inside it. Text inside a viewBox that scales to its container
scales with the drawing, so no font-size is correct at more than one width —
a 12-unit label in a 606-unit box renders around 14px on a desktop card and
around 6px on a phone. Positions are therefore emitted as **percentages**,
which track the scaling while the CSS font-size does not.

The wind band's figures sit on **fixed rows** rather than floating beside
their own lines: three aligned rows (gusts, sustained, arrows) read as a
table under the plot, where the same numbers tracking two crossing lines
read as scatter.

Every coordinate leaves here as an already-formatted ``str``. Django
localises floats on output, so a bare float would emit ``x="12,5"`` under a
comma-decimal locale and the whole drawing would silently vanish; formatting
in Python puts that beyond reach without the template having to remember
``{% localize off %}``.
"""

from __future__ import annotations

import datetime
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# ── Geometry, in viewBox units ───────────────────────────────────────────
# The drawing is authored against a 606-unit content box and scaled to its
# container by the viewBox, so these are ratios rather than pixels.

CHART_WIDTH = 606
PAD_LEFT = 40  # left gutter: unit and axis labels
PAD_RIGHT = 46  # right gutter: metre axis and band icons
PLOT_WIDTH = CHART_WIDTH - PAD_LEFT - PAD_RIGHT  # 520
PLOT_RIGHT = PAD_LEFT + PLOT_WIDTH
DAY_HOURS = 24
BLOCK_COUNT = 8
BLOCK_HOURS = DAY_HOURS // BLOCK_COUNT
BLOCK_WIDTH = PLOT_WIDTH / BLOCK_COUNT  # 65
HOUR_WIDTH = PLOT_WIDTH / DAY_HOURS

# THREE CHARTS, ONE X-AXIS. Temperature, precipitation and wind measure
# different things in different units at different resolutions, and each gets
# its own band, its own header and its own summary. What they share is the
# time axis and nothing else — see this module's docstring.

# Temperature: two lines on two scales, plus the 0 °C and station rules.
TEMP_HEIGHT = 200
# The line region starts well below the band's top so the "°C" and "m" unit
# labels, which sit at the very top of each gutter, clear the highest axis
# tick. Splitting the old single band into three shortened this one and the
# two collided until the headroom went back in.
LINE_TOP = 44
LINE_BOTTOM = 172

# Precipitation: two accumulation series, hourly bars on their own baselines.
PRECIP_HEIGHT = 110
SNOW_BASELINE = 62
SNOW_MAX_HEIGHT = 40
PRECIP_BASELINE = 100
PRECIP_MAX_HEIGHT = 10
BAR_WIDTH = 18  # one per hour, leaving ~4 units of gap in a 21.7-unit column
MIN_BAR_HEIGHT = 2  # a non-zero value always draws something

# Half the width of a peak label ("2.6 mm") in viewBox units, used to keep a
# label centred on hour 0 or hour 23 from hanging over the gutter.
_PEAK_LABEL_HALF = 24

# Wind band. The two figure rows are fixed heights above and below the line
# region so the numbers align across the day.
WIND_HEIGHT = 92
GUST_ROW_Y = 4
WIND_TOP = 28
WIND_BOTTOM = 66
SPEED_ROW_Y = 72
DIRECTION_HEIGHT = 30
ARROW_LENGTH = 14

# Headroom above the tallest bar, so a value label never touches the series
# above it. The design's own figures: snow 1.25, precipitation 1.2.
_SNOW_HEADROOM = 1.25
_PRECIP_HEADROOM = 1.2

# How far the freezing-level domain may be stretched to admit the station
# elevation line before the line is dropped instead. 2.5x keeps the line on
# a day whose freezing level merely brackets the village, and drops it on a
# day that runs a kilometre above.
_ELEVATION_DOMAIN_LIMIT = 2.5

# The station caption flips below its line rather than above when the line
# sits within this much of the plot's ceiling, where there is no room.
_CAPTION_FLIP_MARGIN = 26


class ChartLabel(TypedDict):
    """One HTML label positioned over a band, in per-cent of that band."""

    text: str
    left: str
    top: str


class AxisTick(TypedDict):
    """
    A gutter axis tick: the value, its label position, and the mark itself.

    ``top`` is a per-cent of the band, for the HTML label; ``y`` is the same
    height in viewBox units, for the tick line drawn in the SVG beside it.
    The two differ by more than their units — ``top`` carries the offset
    that centres a text line on the value, while ``y`` is the value's own
    height. Using one for the other puts every tick half a line out.
    """

    text: str
    top: str
    y: str


class ChartBar(TypedDict):
    """One bar, in viewBox units."""

    x: str
    y: str
    width: str
    height: str


class ChartBand(TypedDict):
    """
    A horizontal run along the hour axis, in per-cent of the drawing's width.

    Percentages rather than viewBox units because the axis bar is HTML laid
    over the same x-scale as the SVG, on the same footing as the hour labels
    — see the module docstring on why every label here is HTML.
    """

    left: str
    width: str


class ChartArrow(TypedDict):
    """One wind-direction arrow: a path plus its rotation about its centre."""

    d: str
    transform: str
    label: str


class HourlyChart(TypedDict):
    """Everything ``includes/_hourly_chart.html`` needs to draw one day."""

    # Framing — one viewBox per chart, all on the same x-scale
    view_box_temp: str
    view_box_precip: str
    view_box_wind: str
    view_box_direction: str
    temp_height: str
    precip_height: str

    # Headers — one per chart
    temp_summary: str
    snow_total: str
    precip_total: str
    wind_summary: str
    aria_label: str

    # Temperature chart
    zero_line_y: str | None
    elevation_line_y: str | None
    station_caption: ChartLabel | None
    temp_segments: list[str]
    freezing_segments: list[str]
    temp_ticks: list[AxisTick]
    freezing_ticks: list[AxisTick]

    # Precipitation chart — the plot is dropped entirely on a dry day
    has_precipitation: bool
    snow_bars: list[ChartBar]
    snow_labels: list[ChartLabel]
    precip_bars: list[ChartBar]
    precip_labels: list[ChartLabel]
    snow_baseline_y: str
    precip_baseline_y: str
    snow_icon_top: str
    precip_icon_top: str

    # Wind
    wind_segments: list[str]
    gust_segments: list[str]
    wind_labels: list[ChartLabel]
    gust_labels: list[ChartLabel]
    arrows: list[ChartArrow]

    # Shared
    hour_ticks: list[str]
    minor_ticks: list[str]
    left_tick_x1: str
    left_tick_x2: str
    right_tick_x1: str
    right_tick_x2: str
    plot_edges: list[str]
    temp_edge_top: str
    temp_tick_top: str
    precip_tick_top: str
    wind_tick_top: str
    temp_minor_top: str
    precip_minor_top: str
    hour_labels: list[ChartLabel]

    # The temperature axis alone thickens into a daylight bar (SNOW-790).
    # ``daylight_band`` is None when the day carries no readable sunrise and
    # sunset; ``now_marker`` is None unless the chart is today.
    axis_track: ChartBand
    daylight_band: ChartBand | None
    now_marker: str | None

    rows: list[dict[str, str]]


# ── Formatting ───────────────────────────────────────────────────────────


def _num(value: float) -> str:
    """
    Format a coordinate for an SVG attribute.

    One decimal place, with a trailing ``.0`` stripped, so the output is
    stable and free of binary float noise — the difference between
    ``height="40.1"`` and ``height="40.099999999999994"``.

    Args:
        value: The coordinate, in viewBox units.

    Returns:
        The formatted coordinate.

    """
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _pct(value: float, extent: float) -> str:
    """
    Express a viewBox coordinate as a per-cent of its band.

    Args:
        value: The coordinate, in viewBox units.
        extent: The band's full size on that axis, in viewBox units.

    Returns:
        A CSS percentage, e.g. ``"31.4%"``.

    """
    return f"{(value / extent) * 100:.2f}%"


def _hour_x(hour: float) -> float:
    """Return the centre x of ``hour``'s slot, in viewBox units."""
    return PAD_LEFT + HOUR_WIDTH * (hour + 0.5)


def _block_x(index: int) -> float:
    """
    Return the centre x of block ``index``, in viewBox units.

    Equal to ``_hour_x`` of the block's middle hour, which is what keeps the
    hourly lines and the three-hourly columns on one axis.
    """
    return PAD_LEFT + BLOCK_WIDTH * (index + 0.5)


# ── Reading the series ───────────────────────────────────────────────────


def _hour_of(row: Mapping[str, Any]) -> int | None:
    """
    Read the hour-of-day from a row's ``time`` string.

    ``time`` is the provider's own ``YYYY-MM-DDTHH:MM`` local-time string,
    not a datetime, and the hour is taken from it rather than from the row's
    position in the list — a series short of an hour would otherwise shift
    every later point along the axis.

    Args:
        row: The hourly row.

    Returns:
        The hour, 0-23, or ``None`` when ``time`` is unreadable.

    """
    raw = row.get("time")
    if not isinstance(raw, str) or len(raw) < 13:
        logger.warning("Unreadable hourly time value: %r", raw)
        return None
    try:
        hour = int(raw[11:13])
    except ValueError:
        logger.warning("Unreadable hourly time value: %r", raw)
        return None
    return hour if 0 <= hour < DAY_HOURS else None


def _as_float(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _by_hour(
    hourly: Sequence[Mapping[str, Any]],
    key: str,
) -> list[float | None]:
    """
    Project one variable onto a 24-slot array indexed by hour of day.

    An hour the series does not carry, or carries as null, stays ``None`` —
    which is what breaks the line there rather than bridging the gap.

    Args:
        hourly: The day's hourly rows, in any order.
        key: The ``HourlyRow`` key to read.

    Returns:
        Twenty-four slots.

    """
    slots: list[float | None] = [None] * DAY_HOURS
    for row in hourly:
        hour = _hour_of(row)
        if hour is not None:
            slots[hour] = _as_float(row.get(key))
    return slots


def _block_of(slots: Sequence[float | None], index: int) -> list[float]:
    """Return the readable values of block ``index`` from an hourly array."""
    start = index * BLOCK_HOURS
    return [v for v in slots[start : start + BLOCK_HOURS] if v is not None]


def _block_peaks(slots: Sequence[float | None]) -> list[float | None]:
    """Return each block's maximum, or ``None`` where it has no reading."""
    return [
        max(values) if (values := _block_of(slots, b)) else None
        for b in range(BLOCK_COUNT)
    ]


def _circular_mean(bearings: Sequence[float]) -> float | None:
    """
    Return the mean of a set of bearings, or ``None`` when it has none.

    Bearings are directions on a circle, so the arithmetic mean is wrong at
    the wrap: 350° and 10° average to 180°, due south, when the answer is
    360°, due north. Averaging the unit vectors and taking the angle back
    off the result gets the wrap right.

    A set whose bearings cancel out — a genuine box of the compass — has no
    meaningful mean, and returns ``None`` rather than an arbitrary angle.

    Args:
        bearings: The bearings in degrees.

    Returns:
        The mean bearing in degrees, 0-360, or ``None``.

    """
    if not bearings:
        return None
    east = sum(math.sin(math.radians(b)) for b in bearings)
    north = sum(math.cos(math.radians(b)) for b in bearings)
    if abs(east) < 1e-9 and abs(north) < 1e-9:
        return None
    return math.degrees(math.atan2(east, north)) % 360


def _block_bearings(slots: Sequence[float | None]) -> list[float | None]:
    """Return each block's mean bearing, or ``None`` where it has none."""
    return [_circular_mean(_block_of(slots, b)) for b in range(BLOCK_COUNT)]


# ── Scales ───────────────────────────────────────────────────────────────


def _domain(
    values: Sequence[float],
    *,
    step: float,
    minimum_span: float,
) -> tuple[float, float]:
    """
    Derive a padded, outward-rounded domain from the values it must hold.

    Args:
        values: Every value the axis has to show.
        step: Round the bounds out to a multiple of this.
        minimum_span: Widen a flatter domain than this symmetrically, so a
            near-constant series draws as a level line rather than dividing
            by zero or filling the band with noise.

    Returns:
        The ``(low, high)`` bounds.

    """
    low, high = min(values), max(values)
    if high - low < minimum_span:
        middle = (low + high) / 2
        low, high = middle - minimum_span / 2, middle + minimum_span / 2
    return math.floor(low / step) * step, math.ceil(high / step) * step


def _ticks(low: float, high: float, step: float) -> list[float]:
    """
    Return the tick values across a domain at a readable density.

    The step doubles until at most six ticks remain, so a wide domain does
    not stack unreadable labels down the gutter. Six rather than eight
    because the gutter is only the 130 units of the line region: the
    cold-clear sample day widens its metre domain to 400–1600 m to admit
    the station, and at a 200 m step that printed seven labels into a space
    with room for four.

    Args:
        low: The domain's lower bound.
        high: Its upper bound.
        step: The smallest acceptable interval.

    Returns:
        The tick values, ascending.

    """
    while (high - low) / step > 5:
        step *= 2
    count = int(round((high - low) / step))
    return [low + step * i for i in range(count + 1)]


def _projector(
    low: float, high: float, top: float, bottom: float
) -> Callable[[float], float]:
    """
    Return a function mapping a data value onto a vertical band coordinate.

    Args:
        low: The domain's lower bound.
        high: Its upper bound.
        top: The band coordinate the upper bound sits at.
        bottom: The band coordinate the lower bound sits at.

    Returns:
        The projection function.

    """
    span = high - low or 1.0

    def project(value: float) -> float:
        return bottom - ((value - low) / span) * (bottom - top)

    return project


# ── Paths ────────────────────────────────────────────────────────────────


def _line(
    slots: Sequence[float | None],
    at: Callable[[float], float],
) -> list[str]:
    """
    Build an hourly line as polyline point-strings, breaking across gaps.

    A missing hour breaks the line rather than being bridged: a straight
    segment drawn across a gap is a claim about weather nobody forecast.
    A run of one point is dropped — a polyline of a single point draws
    nothing, and a stray dot reads as data.

    Args:
        slots: Twenty-four hourly values, ``None`` where absent.
        at: The vertical projection for this series.

    Returns:
        One ``"x,y x,y"`` string per contiguous run of two or more hours.

    """
    out: list[str] = []
    run: list[str] = []
    for hour, value in enumerate(slots):
        if value is None:
            if len(run) > 1:
                out.append(" ".join(run))
            run = []
            continue
        run.append(f"{_num(_hour_x(hour))},{_num(at(value))}")
    if len(run) > 1:
        out.append(" ".join(run))
    return out


def _arrow_path(x: float, y: float, length: float) -> str:
    """
    Return the path for an upward arrow centred on a point.

    The glyph points north at zero rotation — a shaft with a chevron head at
    its top — so rotating it by a bearing points it at that bearing.

    Args:
        x: The centre's x, in viewBox units.
        y: Its y.
        length: The shaft's full length.

    Returns:
        The SVG path data.

    """
    half = length / 2
    head = y - half
    return (
        f"M {_num(x)} {_num(y + half)} L {_num(x)} {_num(head)} "
        f"M {_num(x - 3.5)} {_num(head + 4)} L {_num(x)} {_num(head)} "
        f"L {_num(x + 3.5)} {_num(head + 4)}"
    )


# ── Time ─────────────────────────────────────────────────────────────────


def _hour_of_day(value: Any) -> float | None:
    """
    Read a time of day as fractional hours from whichever shape it arrives in.

    Three shapes reach here and all three are legitimate, because the chart
    is deliberately transportable and its callers hold different objects:
    a ``datetime`` (a ``Weather`` row's own ``sunrise``), an ISO timestamp
    string (the committed sample days), and a bare ``"HH:MM"`` (what
    ``weather_display`` formats onto a ``ForecastPanelDay``, already in the
    location's own offset).

    Args:
        value: The candidate, of any type — anything unreadable is ``None``.

    Returns:
        Hours past midnight as a float, or ``None``.

    """
    if isinstance(value, datetime.datetime):
        return value.hour + value.minute / 60
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for parse in (datetime.datetime.fromisoformat, datetime.time.fromisoformat):
        try:
            parsed = parse(text)
        except ValueError:
            continue
        return parsed.hour + parsed.minute / 60
    return None


def _daylight(day: Mapping[str, Any]) -> tuple[float, float] | None:
    """
    Return the day's sunrise and sunset as fractional hours.

    ``sunrise_local``/``sunset_local`` are tried first because they are what
    the production caller supplies and they are already in the location's
    own offset; ``sunrise``/``sunset`` are the fallback for a raw day mapping.
    Never mix the two pairs — a day is read from one or the other.

    Args:
        day: The day being charted.

    **Both values are read as times of day, and the date is discarded.**
    ``sunrise_local`` is a bare ``"HH:MM"`` and carries no date to read, so
    the raw pair is treated the same way for consistency. The consequence is
    a real one and worth stating: a sunset falling on the FOLLOWING date —
    which Open-Meteo returns above the Arctic Circle in summer — reads as an
    early-morning hour and would draw a short band at the start of the day
    instead of a full one. Snowdesk covers the Alps and the Pyrenees, where
    every sunset shares its sunrise's date, so this is a limit rather than a
    bug; a ticket that takes the product north needs to carry the date
    through here.

    Both values are inside the drawn day by construction — every shape
    ``_hour_of_day`` accepts is a time of day, so nothing it returns can
    fall outside ``0..24`` and there is nothing to clamp.

    Returns:
        ``(sunrise, sunset)`` in hours past midnight, or ``None`` when the
        day carries neither pair readably or the two do not bracket any
        daylight. A polar night has no lit segment to draw, and a malformed
        day loses its band rather than its chart.

    """
    for rise_key, set_key in (("sunrise_local", "sunset_local"), ("sunrise", "sunset")):
        rise = _hour_of_day(day.get(rise_key))
        fall = _hour_of_day(day.get(set_key))
        if rise is None or fall is None:
            continue
        return (rise, fall) if fall > rise else None
    return None


def _now_hour(
    day: Mapping[str, Any],
    now: datetime.datetime | None,
) -> float | None:
    """
    Return the current time as a fractional hour, when the chart is today's.

    The marker this feeds carries no clock of its own. Its position against
    the hour axis says what time it is to the precision the chart works at,
    and a label there would fight the axis label beneath it.

    Args:
        day: The day being charted, read for its ``date``.
        now: The current time, or ``None`` to suppress the marker.

    Returns:
        Hours past midnight, or ``None`` — a forecast for tomorrow has no
        current hour to mark.

    """
    if now is None:
        return None
    date = day.get("date")
    if isinstance(date, str):
        try:
            date = datetime.date.fromisoformat(date[:10])
        except ValueError:
            return None
    if not isinstance(date, datetime.date) or date != now.date():
        return None
    return now.hour + now.minute / 60


def _axis_track() -> ChartBand:
    """
    Return the axis bar's ground — the plot's width, placed in the drawing.

    The one band measured against ``CHART_WIDTH``, because it is the only
    one positioned in the drawing. Everything drawn ON the bar is measured
    against the bar; see ``_within_track``.

    Returns:
        The track, as CSS percentages of the drawing's width.

    """
    return {
        "left": _pct(PAD_LEFT, CHART_WIDTH),
        "width": _pct(PLOT_WIDTH, CHART_WIDTH),
    }


def _within_track(start_hour: float, end_hour: float) -> ChartBand:
    """
    Return the run between two clock instants, in per-cent OF THE TRACK.

    Of the track and not of the drawing, because the lit segment is nested
    inside the track element so the track's rounded ends can clip it — a
    sibling with square corners would otherwise overhang them on a day whose
    sun is up at midnight. A percentage resolves against its own containing
    block, so nesting changes the denominator, and mixing the two would put
    sunrise most of two hours early.

    Built on ``_instant_x``, not ``_hour_x``: a run starts at the moment an
    hour begins, where a value measured over that hour would sit half a slot
    later.

    Args:
        start_hour: The opening instant, in hours past midnight.
        end_hour: The closing instant, in hours past midnight.

    Returns:
        The run, as CSS percentages of the track's width.

    """
    return {
        "left": _pct(_instant_x(start_hour) - PAD_LEFT, PLOT_WIDTH),
        "width": _pct(_instant_x(end_hour) - _instant_x(start_hour), PLOT_WIDTH),
    }


def _clock(hour: float) -> str:
    """
    Format a fractional hour as ``HH:MM``, for the spoken summary.

    Args:
        hour: Hours past midnight.

    Returns:
        The wall-clock time, e.g. ``"08:32"``.

    """
    minutes = int(round(hour * 60))
    return f"{(minutes // 60) % DAY_HOURS:02d}:{minutes % 60:02d}"


# ── Layers ───────────────────────────────────────────────────────────────


class _FreezingLayer(TypedDict):
    """The freezing-level line and everything keyed to its own scale."""

    segments: list[str]
    ticks: list[AxisTick]
    elevation_line_y: str | None
    station_caption: ChartLabel | None


def _freezing_layer(
    slots: Sequence[float | None],
    elevation: float | None,
    location_label: str,
) -> _FreezingLayer:
    """
    Build the freezing-level line, its metre axis, and the station mark.

    The station elevation shares this axis, so whether it can be drawn is
    decided here rather than by the caller: admitting it is allowed to widen
    the domain, but only up to ``_ELEVATION_DOMAIN_LIMIT``. Past that the
    line is dropped, because a freezing level a kilometre clear of the
    village would otherwise squash the day's own movement into a flat line
    to make room for a mark that only says "a long way below".

    The station's name and its elevation are one caption inside the plot —
    "Verbier village · 1436 m" — rather than a caption on the left and a
    figure out in the metre gutter. Two halves of one fact read as two
    facts, and the gutter half had to fight the axis ticks for room.

    Args:
        slots: The 24 hourly freezing levels, ``None`` where absent.
        elevation: The location's own elevation in metres, if known.
        location_label: The station's name; without one there is no caption,
            since an unlabelled rule on a chart explains nothing.

    Returns:
        The layer. Every field is empty or ``None`` when no hour has a
        freezing level at all.

    """
    known = [f for f in slots if f is not None]
    empty: _FreezingLayer = {
        "segments": [],
        "ticks": [],
        "elevation_line_y": None,
        "station_caption": None,
    }
    if not known:
        return empty

    low, high = _domain(known, step=200, minimum_span=400)
    data_span = high - low
    show_elevation = False
    if elevation is not None:
        widened = _domain([*known, elevation], step=200, minimum_span=400)
        if widened[1] - widened[0] <= data_span * _ELEVATION_DOMAIN_LIMIT:
            low, high = widened
            show_elevation = True

    at = _projector(low, high, LINE_TOP, LINE_BOTTOM)
    segments = _line(slots, at)
    ticks: list[AxisTick] = [
        {
            "text": f"{value:.0f}",
            "top": _pct(at(value) - 8, TEMP_HEIGHT),
            "y": _num(at(value)),
        }
        for value in _ticks(low, high, 200)
    ]

    if not show_elevation or elevation is None:
        return {**empty, "segments": segments, "ticks": ticks}

    elevation_y = at(elevation)
    # Sit the caption above its line, unless the line is high enough in the
    # plot that there is no room, in which case flip it underneath.
    offset = 8 if elevation_y - LINE_TOP < _CAPTION_FLIP_MARGIN else -20
    caption: ChartLabel | None = None
    if location_label:
        caption = {
            "text": f"{location_label} · {elevation:,.0f} m",
            "left": _pct(PAD_LEFT + 12, CHART_WIDTH),
            "top": _pct(elevation_y + offset, TEMP_HEIGHT),
        }
    return {
        "segments": segments,
        "ticks": ticks,
        "elevation_line_y": _num(elevation_y),
        "station_caption": caption,
    }


class _PrecipitationLayer(TypedDict):
    """The two accumulation bands, or the fact that there are none."""

    present: bool
    snow_bars: list[ChartBar]
    snow_labels: list[ChartLabel]
    precip_bars: list[ChartBar]
    precip_labels: list[ChartLabel]
    snow_total: str
    precip_total: str


def _bars(
    slots: Sequence[float | None],
    *,
    baseline: float,
    max_height: float,
    headroom: float,
    unit: str,
) -> tuple[list[ChartBar], list[ChartLabel]]:
    """
    Build one accumulation series as hourly bars with a single peak label.

    **One bar per hour, and exactly one number.** The bars are hourly like
    the lines above them, which is what shows a storm's arc — when it built,
    where it paused, when the second pulse came — none of which survives
    eight three-hour blocks. But 24 figures do not fit the column, and a
    figure on every third bar would sit over one bar of three while reading
    as the group's total, which is worse than no figure at all.

    So only the tallest bar is labelled, and it carries its unit: it is the
    scale, and the eye reads every other bar against it. The day's own
    totals are in the header, so nothing is lost by leaving the rest bare —
    and with a single figure per band there is no series for a gutter unit
    to govern, which is why the label spells "1.7 cm" rather than "1.7".

    An hour with nothing in it draws nothing at all — a row of flat stubs
    down a quiet day is noise. Any non-zero value draws at least
    ``MIN_BAR_HEIGHT``, so a trace amount is visible rather than rounding
    away to a bare line.

    Args:
        slots: The 24 hourly values, ``None`` where the hour has no reading.
        baseline: The band coordinate the bars stand on.
        max_height: The tallest a bar may draw.
        headroom: Divide by the peak times this, leaving room for the label.
        unit: The unit the label spells out, e.g. ``"cm"``.

    Returns:
        The bars, and a list holding at most one label.

    """
    readings = [v for v in slots if v is not None]
    peak = max(readings) if readings else 0.0
    if peak <= 0:
        return [], []
    scale = peak * headroom

    bars: list[ChartBar] = []
    tallest: tuple[float, float, float] | None = None
    for hour, value in enumerate(slots):
        if not value or value <= 0:
            continue
        height = max(MIN_BAR_HEIGHT, (value / scale) * max_height)
        centre = _hour_x(hour)
        bars.append(
            {
                "x": _num(centre - BAR_WIDTH / 2),
                "y": _num(baseline - height),
                "width": _num(BAR_WIDTH),
                "height": _num(height),
            }
        )
        # First past the post on a tie: two identical peaks make the choice
        # arbitrary either way, and the label marks the scale, not the hour.
        if tallest is None or value > tallest[0]:
            tallest = (value, centre, height)

    if tallest is None:
        return bars, []
    value, centre, height = tallest
    # A label centred on hour 0 or hour 23 would hang into the gutter and
    # collide with the axis, so it is pulled back inside the plot.
    centre = min(
        max(centre, PAD_LEFT + _PEAK_LABEL_HALF), PLOT_RIGHT - _PEAK_LABEL_HALF
    )
    return bars, [
        {
            "text": f"{value:.1f} {unit}",
            "left": _pct(centre, CHART_WIDTH),
            "top": _pct(baseline - height - 17, PRECIP_HEIGHT),
        }
    ]


def _precipitation_layer(
    snow: Sequence[float | None],
    precip: Sequence[float | None],
) -> _PrecipitationLayer:
    """
    Build the snow and precipitation bands, or report that the day is dry.

    **A dry day collapses both bands entirely.** Two empty rulers with their
    unit labels and icons still attached take a third of the chart's height
    to say nothing, and worse, they read at a glance as bands whose bars
    happen to be too small to see. The chart shortens instead.

    The header still carries both totals, as ``0.0 cm`` and ``0.0 mm``. A
    figure of zero is the same kind of thing as a figure of 15.7, so the
    header reads the same way on every day and the reader never has to
    work out whether a missing line means "none" or "not known". A phrase
    in place of the numbers would be a second thing to parse for a fact
    the numbers already state.

    Args:
        snow: The 24 hourly snowfall values, in centimetres.
        precip: The 24 hourly precipitation values, in millimetres.

    Returns:
        The layer, with ``present`` False on a dry day.

    """
    snow_total = sum(v for v in snow if v)
    precip_total = sum(v for v in precip if v)
    if snow_total <= 0 and precip_total <= 0:
        return {
            "present": False,
            "snow_bars": [],
            "snow_labels": [],
            "precip_bars": [],
            "precip_labels": [],
            "snow_total": "0.0 cm",
            "precip_total": "0.0 mm",
        }

    snow_bars, snow_labels = _bars(
        snow,
        baseline=SNOW_BASELINE,
        max_height=SNOW_MAX_HEIGHT,
        headroom=_SNOW_HEADROOM,
        unit="cm",
    )
    precip_bars, precip_labels = _bars(
        precip,
        baseline=PRECIP_BASELINE,
        max_height=PRECIP_MAX_HEIGHT,
        headroom=_PRECIP_HEADROOM,
        unit="mm",
    )
    return {
        "present": True,
        "snow_bars": snow_bars,
        "snow_labels": snow_labels,
        "precip_bars": precip_bars,
        "precip_labels": precip_labels,
        "snow_total": f"{snow_total:.1f} cm",
        "precip_total": f"{precip_total:.1f} mm",
    }


class _WindLayer(TypedDict):
    """The wind band's two hourly lines and their three-hourly figures."""

    wind_segments: list[str]
    gust_segments: list[str]
    wind_labels: list[ChartLabel]
    gust_labels: list[ChartLabel]


def _wind_layer(
    wind_slots: Sequence[float | None],
    gust_slots: Sequence[float | None],
) -> _WindLayer:
    """
    Build the wind band: two hourly lines, and block figures on fixed rows.

    Both series share one scale anchored at zero — wind is a magnitude, and
    a band that floated its own floor would make a calm day look like a
    blowing one.

    The figures sit on **fixed rows**, gusts above the plot and sustained
    speeds below it, rather than floating beside their own lines. Two lines
    this close together drag their labels across each other wherever they
    converge; on fixed rows the numbers align down the day and read as a
    table, which is also what lets them line up with the arrows beneath.

    Args:
        wind_slots: The 24 hourly sustained speeds.
        gust_slots: The 24 hourly gusts.

    Returns:
        The layer.

    """
    known = [v for v in [*wind_slots, *gust_slots] if v is not None]
    low, high = _domain([0.0, *known], step=10, minimum_span=20)
    at = _projector(low, high, WIND_TOP, WIND_BOTTOM)

    def figures(slots: Sequence[float | None], row_y: float) -> list[ChartLabel]:
        out: list[ChartLabel] = []
        for index, peak in enumerate(_block_peaks(slots)):
            if peak is None:
                continue
            out.append(
                {
                    "text": f"{peak:.0f}",
                    "left": _pct(_block_x(index), CHART_WIDTH),
                    "top": _pct(row_y, WIND_HEIGHT),
                }
            )
        return out

    return {
        "wind_segments": _line(wind_slots, at),
        "gust_segments": _line(gust_slots, at),
        "wind_labels": figures(wind_slots, SPEED_ROW_Y),
        "gust_labels": figures(gust_slots, GUST_ROW_Y),
    }


def _direction_arrows(bearings: Sequence[float | None]) -> list[ChartArrow]:
    """
    Build one arrow per block whose bearings had a usable mean.

    The rotation is the bearing itself, which points the arrowhead at the
    weather's source — the same convention the ``wind_arrow_rotation``
    filter uses since SNOW-785, and ``tests/public/test_weather_tags.py``
    asserts the two agree.

    Args:
        bearings: The per-block mean bearings, ``None`` where there is none.

    Returns:
        The arrows, one per block that has a bearing.

    """
    centre_y = DIRECTION_HEIGHT / 2
    return [
        {
            "d": _arrow_path(_block_x(index), centre_y, ARROW_LENGTH),
            "transform": (
                f"rotate({_num(bearing)} {_num(_block_x(index))} {_num(centre_y)})"
            ),
            "label": _compass(bearing),
        }
        for index, bearing in enumerate(bearings)
        if bearing is not None
    ]


# ── Assembly ─────────────────────────────────────────────────────────────


def build_hourly_chart(
    day: Mapping[str, Any],
    *,
    elevation: float | None = None,
    location_label: str = "",
    now: datetime.datetime | None = None,
) -> HourlyChart | None:
    """
    Build one day's chart geometry from its hourly series.

    Args:
        day: The day's mapping — its ``hourly`` list, and optionally
            ``date`` for the accessible summary and the now-marker, plus a
            sunrise/sunset pair for the axis band (see ``_daylight`` for the
            shapes accepted). Any mapping with those keys will do; this
            function never reaches for the ORM.
        elevation: The location's own elevation in metres, marked on the
            freezing-level scale when the day's freezing levels run near it.
        location_label: The station's name, e.g. "Verbier village".
        now: The current time. The marker on the hour axis is drawn only
            when this falls on the day being charted.

    Returns:
        The geometry, or ``None`` when the day carries no usable hourly
        series. A caller renders nothing at all in that case rather than an
        empty chart frame.

    """
    hourly = day.get("hourly") or []
    if not hourly:
        return None

    temps = _by_hour(hourly, "temperature_2m")
    freezing = _by_hour(hourly, "freezing_level_height")
    snow = _by_hour(hourly, "snowfall")
    precip = _by_hour(hourly, "precipitation")
    winds = _by_hour(hourly, "wind_speed_10m")
    gusts = _by_hour(hourly, "wind_gusts_10m")
    bearings = _by_hour(hourly, "wind_direction_10m")

    known_temps = [t for t in temps if t is not None]
    if not known_temps:
        return None

    # ── Temperature scale ────────────────────────────────────────────
    t_low, t_high = _domain(known_temps, step=2, minimum_span=4)
    t_at = _projector(t_low, t_high, LINE_TOP, LINE_BOTTOM)
    temp_ticks: list[AxisTick] = [
        {
            "text": f"+{value:.0f}" if value > 0 else f"{value:.0f}",
            "top": _pct(t_at(value) - 8, TEMP_HEIGHT),
            "y": _num(t_at(value)),
        }
        for value in _ticks(t_low, t_high, 2)
    ]

    freeze = _freezing_layer(freezing, elevation, location_label)
    wet = _precipitation_layer(snow, precip)
    wind = _wind_layer(winds, gusts)
    daylight = _daylight(day)
    now_hour = _now_hour(day, now)

    return {
        "view_box_temp": f"0 0 {CHART_WIDTH} {TEMP_HEIGHT}",
        "view_box_precip": f"0 0 {CHART_WIDTH} {PRECIP_HEIGHT}",
        "view_box_wind": f"0 0 {CHART_WIDTH} {WIND_HEIGHT}",
        "view_box_direction": f"0 0 {CHART_WIDTH} {DIRECTION_HEIGHT}",
        "temp_height": str(TEMP_HEIGHT),
        "precip_height": str(PRECIP_HEIGHT),
        "temp_summary": _temp_summary(known_temps, freezing),
        "snow_total": wet["snow_total"],
        "precip_total": wet["precip_total"],
        "wind_summary": _wind_summary(winds, gusts),
        "aria_label": _aria_label(
            day, known_temps, snow, freezing, winds, gusts, wet, daylight
        ),
        "zero_line_y": _num(t_at(0)) if t_low <= 0 <= t_high else None,
        "elevation_line_y": freeze["elevation_line_y"],
        "station_caption": freeze["station_caption"],
        "temp_segments": _line(temps, t_at),
        "freezing_segments": freeze["segments"],
        "temp_ticks": temp_ticks,
        "freezing_ticks": freeze["ticks"],
        "has_precipitation": wet["present"],
        "snow_bars": wet["snow_bars"],
        "snow_labels": wet["snow_labels"],
        "precip_bars": wet["precip_bars"],
        "precip_labels": wet["precip_labels"],
        "snow_baseline_y": _num(SNOW_BASELINE),
        "precip_baseline_y": _num(PRECIP_BASELINE),
        "snow_icon_top": _pct(SNOW_BASELINE - 26, PRECIP_HEIGHT),
        "precip_icon_top": _pct(PRECIP_BASELINE - 26, PRECIP_HEIGHT),
        "wind_segments": wind["wind_segments"],
        "gust_segments": wind["gust_segments"],
        "wind_labels": wind["wind_labels"],
        "gust_labels": wind["gust_labels"],
        "arrows": _direction_arrows(_block_bearings(bearings)),
        # Every chart gets its own foot ticks. They are the only thing the
        # three share besides the axis itself, and a chart that has to
        # borrow the one below it to place a point in time is not standing
        # on its own.
        "hour_ticks": [_num(_instant_x(hour)) for hour in _AXIS_HOURS],
        # A minor tick on every unlabelled hour, for the two charts that are
        # drawn hourly. Without them the axis says the data is three-hourly,
        # which for those two it is not — an hourly bar sits between labelled
        # ticks with nothing to place it against. The wind chart gets none:
        # it IS three-hourly, and hour ticks under it would promise a
        # resolution its figures do not have.
        "minor_ticks": [
            _num(_instant_x(hour)) for hour in range(DAY_HOURS) if hour % BLOCK_HOURS
        ],
        # Where a gutter tick starts and stops on each side of the plot.
        "left_tick_x1": _num(PAD_LEFT - AXIS_TICK_LENGTH),
        "left_tick_x2": _num(PAD_LEFT),
        "right_tick_x1": _num(PLOT_RIGHT),
        "right_tick_x2": _num(PLOT_RIGHT + AXIS_TICK_LENGTH),
        # The temperature plot's own left and right edges. The night
        # shading SNOW-723 removed had been carrying them for free: a
        # reader took the plot's bounds from where the shaded blocks
        # stopped. They go back only on this chart, which is the only one
        # with vertical scales for them to close off.
        "plot_edges": [_num(PAD_LEFT), _num(PLOT_RIGHT)],
        "temp_edge_top": _num(LINE_TOP - _EDGE_HEADROOM),
        "temp_tick_top": _num(TEMP_HEIGHT - TICK_LENGTH),
        "precip_tick_top": _num(PRECIP_HEIGHT - TICK_LENGTH),
        "wind_tick_top": _num(WIND_HEIGHT - TICK_LENGTH),
        "temp_minor_top": _num(TEMP_HEIGHT - MINOR_TICK_LENGTH),
        "precip_minor_top": _num(PRECIP_HEIGHT - MINOR_TICK_LENGTH),
        # Hours alone, no ":00". Every label on the axis is on the hour, so
        # the minutes are the same four characters repeated eight times and
        # buy the reader nothing; dropping them halves the label width and
        # takes the crowding out of the axis.
        "hour_labels": [
            {
                "text": f"{hour:02d}",
                "left": _pct(_instant_x(hour), CHART_WIDTH),
                "top": "0%",
            }
            # The closing 24:00 gets a tick but no label: it is the same
            # instant as the next day's 00:00, and naming it invites the
            # reader to look for a value there.
            for hour in _AXIS_HOURS[:-1]
        ],
        # The temperature axis bar. The track spans the plot whether or not
        # the day yields a lit segment, because the marker needs a ground to
        # sit on even on a day whose sunrise cannot be read.
        "axis_track": _axis_track(),
        "daylight_band": _within_track(*daylight) if daylight is not None else None,
        # Against the DRAWING, not the track — unlike the lit segment. The
        # marker is a pin whose ends stand clear of the bar above and
        # below, so it cannot live inside the track element (whose
        # overflow-hidden clips the lit segment's corners) and is a sibling
        # of it instead. Different containing block, different denominator.
        "now_marker": (
            _pct(_instant_x(now_hour), CHART_WIDTH) if now_hour is not None else None
        ),
        "rows": _sr_rows(temps, freezing, snow, precip, winds, gusts, bearings),
    }


# ── Copy ─────────────────────────────────────────────────────────────────

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _compass(bearing: float) -> str:
    """Return the eight-point compass name a bearing blows *from*."""
    return _COMPASS[int((bearing + 22.5) % 360 // 45)]


# Hours the axis marks: every third, plus the closing edge of the day.
_AXIS_HOURS = tuple(range(0, DAY_HOURS + 1, BLOCK_HOURS))

# How far a tick hangs below the plot, in viewBox units. The minor ticks are
# half the length and a lighter ink, so the three-hourly grid the labels sit
# on stays the one the eye counts by.
TICK_LENGTH = 7
MINOR_TICK_LENGTH = 3.5

# Gutter ticks on the temperature chart's two vertical scales (SNOW-790).
# Without them the °C and metre figures float beside the plot with nothing
# joining them to the height they name, and a reader has to take it on
# trust that "1,600" lines up with where they think it does.
AXIS_TICK_LENGTH = 5

# How far above its topmost tick the temperature plot's left/right edge
# begins, so the edge clears the highest gridline rather than starting flush
# with it.
_EDGE_HEADROOM = 10


def _instant_x(hour: float) -> float:
    """
    Return the x of a clock instant — not of an hour's slot.

    ``_hour_x`` places the CENTRE of an hour's slot, which is where a value
    measured over that hour belongs. An axis tick marks the moment the hour
    begins, which is half a slot earlier, and the two must not be confused:
    labelling a slot centre "00:00" puts the label 1.5 hours right of the
    instant it names, and every reading taken off the curve against it comes
    out late by that much.

    Args:
        hour: The hour of day, 0-24 — 24 being the day's closing edge.

    Returns:
        The x coordinate in viewBox units.

    """
    return PAD_LEFT + HOUR_WIDTH * hour


def _extremes(values: Sequence[float | None]) -> tuple[float, float] | None:
    """Return the ``(min, max)`` of a series, or ``None`` when it is empty."""
    known = [v for v in values if v is not None]
    return (min(known), max(known)) if known else None


def _temp_summary(
    temps: Sequence[float],
    freezing: Sequence[float | None],
) -> str:
    """
    Return the temperature chart's one-line summary.

    The chart carries two scales — the air temperature at the station, and
    the altitude at which the air reaches 0 °C — so the header names both.
    A range of metres beside a range of degrees is the whole point of
    putting the two on one plot: it is where a reader sees that a warm
    afternoon and a freezing level above the summit are the same fact.

    Args:
        temps: The known hourly temperatures.
        freezing: The hourly freezing levels, which may all be absent.

    Returns:
        The summary text.

    """
    text = f"{max(temps):+.1f} / {min(temps):+.1f} °C"
    levels = _extremes(freezing)
    if levels is not None:
        text += f" · {levels[0]:,.0f}–{levels[1]:,.0f} m"
    return text


def _wind_summary(
    winds: Sequence[float | None],
    gusts: Sequence[float | None],
) -> str:
    """Return the wind band's one-line summary."""
    speed = _extremes(winds)
    if speed is None:
        return ""
    text = f"{speed[0]:.0f}–{speed[1]:.0f} km/h"
    gust = _extremes(gusts)
    if gust is not None:
        text += f" · gusts to {gust[1]:.0f}"
    return text


def _wind_clause(
    winds: Sequence[float | None],
    gusts: Sequence[float | None],
) -> str | None:
    """
    Return the spoken wind phrase for the accessible summary.

    Its own function only to keep ``_aria_label`` under the complexity
    ceiling; the gust half is a clause of the wind sentence rather than a
    sentence of its own, so the two are built together.

    Args:
        winds: The hourly sustained speeds.
        gusts: The hourly gusts.

    Returns:
        The phrase, or ``None`` when the day carries no wind figures.

    """
    speed = _extremes(winds)
    if speed is None:
        return None
    text = f"wind {speed[0]:.0f} to {speed[1]:.0f} kilometres per hour"
    gust = _extremes(gusts)
    if gust is not None:
        text += f", gusting {gust[1]:.0f}"
    return text


def _aria_label(
    day: Mapping[str, Any],
    temps: Sequence[float],
    snow: Sequence[float | None],
    freezing: Sequence[float | None],
    winds: Sequence[float | None],
    gusts: Sequence[float | None],
    wet: _PrecipitationLayer,
    daylight: tuple[float, float] | None,
) -> str:
    """
    Return the chart's spoken summary.

    The chart is one image to a screen reader, so this sentence carries the
    day at the same altitude a sighted reader takes from the shape. The
    per-block numbers stay reachable in the visually-hidden table beneath.

    **This is where the daylight band is spoken.** The bar itself is
    ``aria-hidden`` decoration over the hour axis, and the legend's "About
    this forecast" block — the other candidate home for the pair — is
    supplied by the component library alone and never renders on the page
    that ships the chart. Saying it here is what puts it in front of a
    reader who cannot see the band.

    Args:
        day: The day being charted, read for its ``date``.
        temps: The known hourly temperatures.
        snow: The hourly snowfall values.
        freezing: The hourly freezing levels.
        winds: The hourly wind speeds.
        gusts: The hourly gusts.
        wet: The precipitation layer, for whether the day was dry.
        daylight: The sunrise/sunset pair in fractional hours, or ``None``.

    Returns:
        The label text.

    """
    parts: list[str] = []
    date = day.get("date")
    if isinstance(date, str):
        try:
            parts.append(datetime.date.fromisoformat(date[:10]).strftime("%A %-d %B"))
        except ValueError:
            pass
    parts.append(f"{min(temps):.0f} to {max(temps):.0f} degrees")
    if wet["present"]:
        total = sum(v for v in snow if v)
        if total > 0:
            parts.append(f"{total:.1f} cm of new snow")
    else:
        parts.append("no rain or snow")
    levels = _extremes(freezing)
    if levels is not None:
        parts.append(f"freezing level {levels[0]:.0f} to {levels[1]:.0f} metres")
    wind = _wind_clause(winds, gusts)
    if wind is not None:
        parts.append(wind)
    if daylight is not None:
        parts.append(f"daylight {_clock(daylight[0])} to {_clock(daylight[1])}")
    return ", ".join(parts)


def _sr_rows(
    temps: Sequence[float | None],
    freezing: Sequence[float | None],
    snow: Sequence[float | None],
    precip: Sequence[float | None],
    winds: Sequence[float | None],
    gusts: Sequence[float | None],
    bearings: Sequence[float | None],
) -> list[dict[str, str]]:
    """
    Return the block figures as rows for the visually-hidden table.

    The table the chart replaces was readable row by row. Dropping to a
    single ``aria-label`` would lose every number, so the figures stay on
    the page in a table only assistive technology sees. It is three-hourly,
    matching the chart's own columns: this is the chart's text equivalent,
    not the old table hidden.

    Args:
        temps: The hourly temperatures.
        freezing: The hourly freezing levels.
        snow: The hourly snowfall values.
        precip: The hourly precipitation values.
        winds: The hourly wind speeds.
        gusts: The hourly gusts.
        bearings: The hourly bearings.

    Returns:
        One dict per block.

    """
    dash = "—"

    def mean(slots: Sequence[float | None], index: int, spec: str, unit: str) -> str:
        values = _block_of(slots, index)
        return f"{sum(values) / len(values):{spec}} {unit}" if values else dash

    def total(slots: Sequence[float | None], index: int, unit: str) -> str:
        values = _block_of(slots, index)
        return f"{sum(values):.1f} {unit}" if values and sum(values) > 0 else dash

    def peak(slots: Sequence[float | None], index: int, unit: str) -> str:
        values = _block_of(slots, index)
        return f"{max(values):.0f} {unit}" if values else dash

    def compass(index: int) -> str:
        bearing = _circular_mean(_block_of(bearings, index))
        return _compass(bearing) if bearing is not None else dash

    return [
        {
            "time": f"{index * BLOCK_HOURS:02d}:00",
            "temperature": mean(temps, index, ".1f", "°C"),
            "freezing_level": mean(freezing, index, ".0f", "m"),
            "snowfall": total(snow, index, "cm"),
            "precipitation": total(precip, index, "mm"),
            "wind": peak(winds, index, "km/h"),
            "gusts": peak(gusts, index, "km/h"),
            "direction": compass(index),
        }
        for index in range(BLOCK_COUNT)
    ]
