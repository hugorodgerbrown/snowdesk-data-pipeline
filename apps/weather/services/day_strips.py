"""
apps/weather/services/day_strips.py — one day's weather as four linked strips.

The map's weather sheet shows a day as four short charts on one time axis —
temperature, freezing level, snow / rain and wind — each with a row of
figures under it, one per three-hour block (SNOW-1219). One time selector
picks a block, and a band through all four strips, the bold figure in each
row and the four headers follow it. It is the route point card's idea
applied to a day: one cursor, every reading at that cursor.

It replaces ``_hourly_chart.html`` in the sheet only. The location page
keeps the meteogram until it gets the same treatment at hourly resolution.

Four strips, one axis
---------------------
  TEMPERATURE     The hourly line, with the 0 °C rule dashed.
  FREEZING LEVEL  Its own strip, split out of the temperature chart where
                  it shared a plot on a second scale. The location's own
                  elevation is a dotted rule labelled with its height: the
                  freezing level is read against it, and the gap between
                  the two lines is the answer to "rain or snow here".
  SNOW / RAIN     One bar per hour, snow and rain coloured apart.
  WIND            Sustained speed and gusts, hourly, with one direction
                  arrow per block.

What a block's figure is
------------------------
Each quantity is reduced by the operation that is true of it, and the
temperature and freezing level are reduced to a range rather than a mean,
because a mean is a reading nobody experiences (Hugo, 24 Oct 2026):

  temperature, freezing level   max over min
  snow / rain                   the block's total, as snow (cm) when most
                                of it fell as snow, else as rain (mm)
  wind                          the strongest sustained speed over the
                                strongest gust, and the mean bearing

Geometry
--------
The plots are SVG in a 288-unit-wide viewBox, twelve units an hour, drawn
with ``preserveAspectRatio="none"`` so they stretch horizontally only and
keep their authored height in pixels; every stroke is non-scaling. Labels,
figures and arrows are HTML, because text and arrows inside a stretched
viewBox distort — the same rule ``hourly_chart.py`` follows. The figure
rows are an eight-column grid with the plots' own left gutter, so a column
of figures sits under its block exactly.

Every coordinate leaves as a formatted ``str``, for the localisation
reason ``hourly_chart.py``'s header gives.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.weather.services.hourly_chart import (
    BLOCK_COUNT,
    BLOCK_HOURS,
    DAY_HOURS,
    _as_float,
    _block_of,
    _by_hour,
    _circular_mean,
    _domain,
    _num,
    _projector,
    _ticks,
)

logger = logging.getLogger(__name__)

# ── Geometry ─────────────────────────────────────────────────────────────
PLOT_WIDTH = 288
HOUR_WIDTH = PLOT_WIDTH / DAY_HOURS  # 12
BLOCK_WIDTH = PLOT_WIDTH / BLOCK_COUNT  # 36
PAD = 8  # headroom above the highest and below the lowest point

TEMP_HEIGHT = 90
FREEZING_HEIGHT = 90
PRECIP_HEIGHT = 60
WIND_HEIGHT = 70

BAR_GAP = 2
MIN_BAR_HEIGHT = 1.5

# Open-Meteo reports snowfall in cm and precipitation in mm of water; it
# converts with 0.7 cm of snow per mm, so this is the ratio that tells how
# much of an hour's water fell as snow.
SNOW_CM_PER_MM = 0.7

# The block a day opens on when it is not today: a skier's morning.
DEFAULT_BLOCK = 3


class StripTick(TypedDict):
    """A gutter label at a value's height."""

    top: str  # px from the plot's top
    label: str


class StripBar(TypedDict):
    """One hour's precipitation bar."""

    x: str
    y: str
    width: str
    height: str
    snow: bool


class Block(TypedDict):
    """One three-hour block's figures, already formatted."""

    index: int
    start: str  # "09"
    end: str  # "12"
    temp_max: str
    temp_min: str
    freezing_max: str
    freezing_min: str
    precip: str  # "4.5", or "" for none
    precip_is_snow: bool
    precip_summary: str  # "4.5 cm snow" / "1.8 mm rain" / "None"
    wind: str
    gust: str
    bearing: str | None  # degrees FROM, for the arrow's rotation
    wind_summary: str
    temp_summary: str
    freezing_summary: str


class DayStrips(TypedDict):
    """Everything ``_day_strips.html`` draws for one day."""

    blocks: list[Block]
    summaries: dict[str, list[str]]  # each strip's header reading, per block
    selected: int
    boundaries: list[str]  # "00", "03", … "24"
    temp_segments: list[str]
    temp_ticks: list[StripTick]
    temp_zero_y: str | None
    freezing_segments: list[str]
    freezing_area: list[str]
    freezing_ticks: list[StripTick]
    elevation_y: str | None
    elevation_label: str
    precip_bars: list[StripBar]
    precip_top_label: str
    wind_speed_segments: list[str]
    wind_gust_segments: list[str]
    wind_ticks: list[StripTick]
    dividers: list[str]  # x of each block boundary inside the plot
    temp_height: int
    freezing_height: int
    precip_height: int
    wind_height: int


def _hour_x(hour: float) -> float:
    """Return the centre x of ``hour``'s slot."""
    return HOUR_WIDTH * (hour + 0.5)


def _segments(slots: Sequence[float | None], at: Any) -> list[str]:
    """
    Build polyline point-strings, breaking at a missing hour.

    Args:
        slots: Twenty-four values, ``None`` where absent.
        at: The vertical projection.

    Returns:
        One ``"x,y x,y"`` string per run of two or more hours.

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


def _areas(slots: Sequence[float | None], at: Any, floor: float) -> list[str]:
    """
    Close each run of a line down to ``floor`` as a filled polygon.

    Args:
        slots: Twenty-four values, ``None`` where absent.
        at: The vertical projection.
        floor: The band coordinate the fill closes along.

    Returns:
        One polygon point-string per run of two or more hours.

    """
    out = []
    for run in _segments(slots, at):
        points = run.split(" ")
        first_x = points[0].split(",")[0]
        last_x = points[-1].split(",")[0]
        out.append(f"{run} {last_x},{_num(floor)} {first_x},{_num(floor)}")
    return out


def _ticks_for(
    values: Sequence[float],
    *,
    step: float,
    minimum_span: float,
    height: int,
    label: Any,
) -> tuple[list[StripTick], Any]:
    """
    Derive a strip's domain, its gutter labels and its projection.

    Args:
        values: Every value the strip must hold.
        step: The smallest tick interval.
        minimum_span: The flattest domain allowed.
        height: The strip's height in px.
        label: Formats a tick value.

    Returns:
        The ticks and the projection function.

    """
    low, high = _domain(values, step=step, minimum_span=minimum_span)
    at = _projector(low, high, PAD, height - PAD)
    ticks = [
        StripTick(top=_num(at(value)), label=label(value))
        for value in _ticks(low, high, step)
    ]
    return ticks, at


def _degrees(value: float) -> str:
    """Format a temperature as whole degrees with a true minus sign."""
    rounded = round(value)
    if rounded == 0:
        return "0°"
    return f"{'−' if rounded < 0 else ''}{abs(rounded)}°"


def _metres(value: float) -> str:
    """Format a height to the nearest ten metres."""
    return f"{round(value, -1):.0f}"


def _kilometres(value: float) -> str:
    """Format a gutter height in thousands of metres, e.g. ``1.5k``."""
    return f"{value / 1000:.1f}k"


def _selected_block(day: Mapping[str, Any], now: datetime.datetime | None) -> int:
    """
    Choose the block a day opens on: the current one today, else a morning.

    Args:
        day: The day, read for its ``date``.
        now: The current instant, or ``None``.

    Returns:
        The block index, 0-7.

    """
    date = day.get("date")
    if isinstance(date, str):
        try:
            date = datetime.date.fromisoformat(date[:10])
        except ValueError:
            date = None
    if now is not None and isinstance(date, datetime.date):
        local = timezone.localtime(now) if timezone.is_aware(now) else now
        if local.date() == date:
            return local.hour // BLOCK_HOURS
    return DEFAULT_BLOCK


def _block(
    index: int,
    temp: Sequence[float | None],
    freezing: Sequence[float | None],
    snow: Sequence[float | None],
    precip: Sequence[float | None],
    wind: Sequence[float | None],
    gust: Sequence[float | None],
    bearing: Sequence[float | None],
) -> Block:
    """
    Reduce one block's hours to its figures.

    Args:
        index: The block, 0-7.
        temp: Hourly temperature.
        freezing: Hourly freezing level.
        snow: Hourly snowfall, cm.
        precip: Hourly precipitation, mm.
        wind: Hourly sustained wind.
        gust: Hourly gusts.
        bearing: Hourly wind direction.

    Returns:
        The block, every figure formatted; an absent reading is a dash.

    """
    dash = "–"
    temps = _block_of(temp, index)
    levels = _block_of(freezing, index)
    snows = _block_of(snow, index)
    waters = _block_of(precip, index)
    winds = _block_of(wind, index)
    gusts = _block_of(gust, index)

    water = sum(waters)
    snow_cm = sum(snows)
    is_snow = water > 0 and snow_cm / SNOW_CM_PER_MM >= water / 2
    if not waters:
        # No reading is not a dry forecast: say nothing, as the other rows do.
        precip_text, summary = "", dash
    elif water < 0.05:
        precip_text, summary = "", _("Dry")
    elif is_snow:
        precip_text = f"{snow_cm:.1f}"
        summary = _("%(cm)s cm snow") % {"cm": precip_text}
    else:
        precip_text = f"{water:.1f}"
        summary = _("%(mm)s mm rain") % {"mm": precip_text}

    temp_max = _degrees(max(temps)) if temps else dash
    temp_min = _degrees(min(temps)) if temps else dash
    level_max = _metres(max(levels)) if levels else dash
    level_min = _metres(min(levels)) if levels else dash
    wind_text = f"{round(max(winds))}" if winds else dash
    gust_text = f"{round(max(gusts))}" if gusts else dash
    mean = _circular_mean(_block_of(bearing, index))

    freezing_summary = (
        _("max %(max)s · min %(min)s") % {"max": level_max, "min": level_min}
        if levels
        else dash
    )

    start = index * BLOCK_HOURS
    return Block(
        index=index,
        start=f"{start:02d}",
        end=f"{start + BLOCK_HOURS:02d}",
        temp_max=temp_max,
        temp_min=temp_min,
        freezing_max=level_max,
        freezing_min=level_min,
        precip=precip_text,
        precip_is_snow=is_snow,
        precip_summary=summary,
        wind=wind_text,
        gust=gust_text,
        bearing=_num(mean) if mean is not None else None,
        wind_summary=(
            _("%(speed)s km/h · gusts %(gust)s")
            % {"speed": wind_text, "gust": gust_text}
            if winds
            else dash
        ),
        temp_summary=(
            _("max %(max)s · min %(min)s") % {"max": temp_max, "min": temp_min}
            if temps
            else dash
        ),
        freezing_summary=freezing_summary,
    )


def build_day_strips(
    day: Mapping[str, Any],
    *,
    elevation: float | None = None,
    now: datetime.datetime | None = None,
) -> DayStrips | None:
    """
    Build one day's four strips and eight blocks.

    Transportable like ``build_hourly_chart``: it reads a plain mapping —
    the day's ``date`` and its ``hourly`` list — and never the ORM.

    Args:
        day: The day, with an ``hourly`` list of ``HourlyRow`` mappings.
        elevation: The location's height in metres, drawn as a rule on the
            freezing-level strip.
        now: The current instant, which picks today's opening block.

    Returns:
        The strips, or ``None`` when the day has no temperature reading to
        draw — nothing else on the drawing makes sense without one.

    """
    hourly = day.get("hourly") or []
    temp = _by_hour(hourly, "temperature_2m")
    readable_temps = [v for v in temp if v is not None]
    if not readable_temps:
        return None
    freezing = _by_hour(hourly, "freezing_level_height")
    snow = _by_hour(hourly, "snowfall")
    precip = _by_hour(hourly, "precipitation")
    wind = _by_hour(hourly, "wind_speed_10m")
    gust = _by_hour(hourly, "wind_gusts_10m")
    bearing = _by_hour(hourly, "wind_direction_10m")
    elevation = _as_float(elevation)

    # Temperature: the 0 °C rule only when zero is inside the domain.
    temp_ticks, temp_at = _ticks_for(
        readable_temps, step=1, minimum_span=4, height=TEMP_HEIGHT, label=_degrees
    )
    low_t, high_t = _domain(readable_temps, step=1, minimum_span=4)
    temp_zero = _num(temp_at(0)) if low_t <= 0 <= high_t else None

    # Freezing level: the domain admits the elevation only when it is near,
    # so a valley station does not flatten a high freezing level to a line.
    levels = [v for v in freezing if v is not None]
    freezing_ticks: list[StripTick] = []
    freezing_segments: list[str] = []
    freezing_area: list[str] = []
    elevation_y: str | None = None
    if levels:
        domain_values = list(levels)
        if elevation is not None and abs(elevation - min(levels)) < 800:
            domain_values.append(elevation)
        freezing_ticks, freezing_at = _ticks_for(
            domain_values,
            step=100,
            minimum_span=400,
            height=FREEZING_HEIGHT,
            label=_kilometres,
        )
        low_f, high_f = _domain(domain_values, step=100, minimum_span=400)
        freezing_segments = _segments(freezing, freezing_at)
        freezing_area = _areas(freezing, freezing_at, FREEZING_HEIGHT - PAD)
        if elevation is not None and low_f <= elevation <= high_f:
            elevation_y = _num(freezing_at(elevation))

    # Precipitation: hourly bars on a scale that starts at 3 mm, so a
    # drizzle stays small and a downpour still fits.
    waters = [v for v in precip if v is not None]
    precip_top = max(3.0, max(waters, default=0.0))
    bars: list[StripBar] = []
    for hour in range(DAY_HOURS):
        water = precip[hour]
        if water is None or water <= 0:
            continue
        height = max(MIN_BAR_HEIGHT, water / precip_top * (PRECIP_HEIGHT - 2 * PAD))
        snow_cm = snow[hour] or 0.0
        bars.append(
            StripBar(
                x=_num(hour * HOUR_WIDTH + BAR_GAP / 2),
                y=_num(PRECIP_HEIGHT - PAD - height),
                width=_num(HOUR_WIDTH - BAR_GAP),
                height=_num(height),
                snow=snow_cm / SNOW_CM_PER_MM >= water / 2,
            )
        )

    # Wind: one scale for both lines, from zero.
    wind_values = [v for v in (*wind, *gust) if v is not None]
    wind_ticks: list[StripTick] = []
    wind_speed: list[str] = []
    wind_gust: list[str] = []
    if wind_values:
        high_w = max(40.0, max(wind_values))
        wind_at = _projector(0, high_w, PAD, WIND_HEIGHT - PAD)
        wind_ticks = [
            StripTick(top=_num(wind_at(v)), label=f"{v:.0f}")
            for v in _ticks(0, high_w, 20)[1:]
            if v <= high_w
        ]
        wind_speed = _segments(wind, wind_at)
        wind_gust = _segments(gust, wind_at)

    blocks = [
        _block(b, temp, freezing, snow, precip, wind, gust, bearing)
        for b in range(BLOCK_COUNT)
    ]
    return DayStrips(
        blocks=blocks,
        summaries={
            "temp": [b["temp_summary"] for b in blocks],
            "freezing": [b["freezing_summary"] for b in blocks],
            "precip": [b["precip_summary"] for b in blocks],
            "wind": [b["wind_summary"] for b in blocks],
        },
        selected=_selected_block(day, now),
        boundaries=[f"{h:02d}" for h in range(0, DAY_HOURS + 1, BLOCK_HOURS)],
        temp_segments=_segments(temp, temp_at),
        temp_ticks=temp_ticks,
        temp_zero_y=temp_zero,
        freezing_segments=freezing_segments,
        freezing_area=freezing_area,
        freezing_ticks=freezing_ticks,
        elevation_y=elevation_y,
        elevation_label=f"{elevation:,.0f} m" if elevation is not None else "",
        precip_bars=bars,
        precip_top_label=f"{precip_top:.0f}",
        wind_speed_segments=wind_speed,
        wind_gust_segments=wind_gust,
        wind_ticks=wind_ticks,
        dividers=[_num(b * BLOCK_WIDTH) for b in range(1, BLOCK_COUNT)],
        temp_height=TEMP_HEIGHT,
        freezing_height=FREEZING_HEIGHT,
        precip_height=PRECIP_HEIGHT,
        wind_height=WIND_HEIGHT,
    )
