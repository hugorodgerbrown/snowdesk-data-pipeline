"""
apps/weather/services/weather_chart.py — SVG geometry for the outlook chart.

Turns a :class:`~apps.weather.services.weather_display.ForecastPanel` into
the numbers an SVG needs: a filled band between each day's max and min
temperature, the two lines that bound it, a point per day, and the y of the
0 °C line when the window crosses it.

**Geometry only — no markup.** The template owns every colour, stroke width
and label, because those are design decisions and this module has no
business holding them. What it owns is arithmetic that would otherwise be
written in a template language with no arithmetic, or in JavaScript that
would then need the data a second time.

**One series, not two.** Freezing level is the other number a skier reads
off an outlook, and it is deliberately absent: it is measured in metres
against a temperature axis in degrees, so plotting both means two scales in
one small chart. It stays where it already is — a figure per column in
``includes/_forecast_panel.html``, which is directly under this chart.

The chart is a fixed viewBox scaled by CSS, so every coordinate here is in
viewBox units rather than pixels and nothing needs to know the rendered
width.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from apps.weather.services.weather_display import ForecastPanel

logger = logging.getLogger(__name__)

# The viewBox the template declares. Coordinates below are in these units.
VIEW_WIDTH = 320
VIEW_HEIGHT = 132

# Plot area inside the viewBox. The bottom inset leaves room for the day
# labels, which sit under the plot rather than inside it.
PLOT_TOP = 12
PLOT_BOTTOM = 96
PLOT_LEFT = 26
PLOT_RIGHT = VIEW_WIDTH - 10

# Degrees of headroom added above the warmest and below the coldest reading,
# so a line never runs along the edge of its own plot.
#
# It is also what makes ``_scale`` total. A completely flat window — every
# day the same temperature to the degree — has no range of its own, but the
# padding is applied to both ends before anything is divided, so the span is
# never smaller than twice this and never zero. A separate minimum-span
# floor was written for that case and removed: it was set at 2.0 against a
# padded span that is always at least 3.0, so it could not fire.
SCALE_PADDING_C = 1.5


class ChartPoint(TypedDict):
    """One day's plotted position and the values behind it."""

    x: float
    y_max: float
    y_min: float
    label: str
    temp_max: float
    temp_min: float


class ForecastChart(TypedDict):
    """Context dict consumed by ``includes/_forecast_chart.html``."""

    view_width: int
    view_height: int
    plot_top: int
    plot_bottom: int
    plot_left: int
    plot_right: int
    points: list[ChartPoint]
    band: str
    max_line: str
    min_line: str
    label_y: float
    warmest: float
    coldest: float
    #: y of the 0 °C line, or ``None`` when the window sits wholly above or
    #: below freezing and the line would be off-plot.
    zero_y: float | None


def _scale(value: float, coldest: float, warmest: float) -> float:
    """Map a temperature onto a y coordinate in the plot area.

    SVG y grows downward, so the warmest reading takes the SMALLEST y.

    Args:
        value: The temperature to place, in °C.
        coldest: The bottom of the scale, in °C.
        warmest: The top of the scale, in °C.

    Returns:
        A y coordinate in viewBox units, within the plot area.

    """
    span = warmest - coldest
    fraction = (value - coldest) / span
    return PLOT_BOTTOM - fraction * (PLOT_BOTTOM - PLOT_TOP)


def build_forecast_chart(panel: "ForecastPanel | None") -> ForecastChart | None:
    """Build the outlook chart's geometry from a forecast panel.

    A day missing either bound is skipped rather than interpolated: an
    invented point on a temperature chart is a claim about the weather that
    nobody made.

    Args:
        panel: The panel whose days to plot, or ``None``.

    Returns:
        A :class:`ForecastChart`, or ``None`` when there is nothing to draw
        — no panel, or fewer than two days with both bounds. One point is
        not a line, and a chart of it says less than the day strip beneath
        it already does.

    """
    if panel is None:
        return None

    # Unpack to concrete floats in one pass rather than filtering and
    # re-reading the optional fields later — the narrowing is then a fact
    # about `plotted`, not something every subsequent line has to restate.
    plotted: list[tuple[str, float, float]] = []
    for day in panel["days"]:
        temp_max = day["temp_max"]
        temp_min = day["temp_min"]
        if temp_max is None or temp_min is None:
            continue
        plotted.append((day["weekday_label"], float(temp_max), float(temp_min)))
    if len(plotted) < 2:
        return None

    # Padded on both ends, which also guarantees a non-zero span for
    # ``_scale`` to divide by — see SCALE_PADDING_C.
    warmest = max(entry[1] for entry in plotted) + SCALE_PADDING_C
    coldest = min(entry[2] for entry in plotted) - SCALE_PADDING_C

    step = (PLOT_RIGHT - PLOT_LEFT) / (len(plotted) - 1)
    points: list[ChartPoint] = [
        ChartPoint(
            x=round(PLOT_LEFT + index * step, 2),
            y_max=round(_scale(day_max, coldest, warmest), 2),
            y_min=round(_scale(day_min, coldest, warmest), 2),
            label=label,
            temp_max=day_max,
            temp_min=day_min,
        )
        for index, (label, day_max, day_min) in enumerate(plotted)
    ]

    max_line = " ".join(f"{p['x']},{p['y_max']}" for p in points)
    min_line = " ".join(f"{p['x']},{p['y_min']}" for p in points)
    # The band is the max line out and the min line back, closed — one
    # polygon rather than two paths, so the fill cannot leak if a later
    # change reorders the points.
    band = max_line + " " + " ".join(f"{p['x']},{p['y_min']}" for p in reversed(points))

    zero_y: float | None = None
    if coldest < 0 < warmest:
        zero_y = round(_scale(0.0, coldest, warmest), 2)

    return ForecastChart(
        view_width=VIEW_WIDTH,
        view_height=VIEW_HEIGHT,
        plot_top=PLOT_TOP,
        plot_bottom=PLOT_BOTTOM,
        plot_left=PLOT_LEFT,
        plot_right=PLOT_RIGHT,
        points=points,
        band=band,
        max_line=max_line,
        min_line=min_line,
        label_y=PLOT_BOTTOM + 16,
        warmest=round(warmest, 1),
        coldest=round(coldest, 1),
        zero_y=zero_y,
    )
