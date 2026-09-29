"""
tests/templates/includes/test_hourly_chart.py — the meteogram's markup.

Renders ``includes/_hourly_chart.html`` with ``render_to_string`` — no test
client, no database — and checks what SNOW-1049 changed about how the chart
fills its column:

  - the card no longer stops at ``max-w-narrow``;
  - the three plot SVGs stretch horizontally (``preserveAspectRatio="none"``)
    and are sized by the aspect ratio and height cap the service emits;
  - every stroked line and polyline is ``non-scaling-stroke``, so a
    stretched plot keeps its line weights;
  - the wind arrows are fixed-size HTML glyphs rotated by CSS, not paths
    inside a viewBox that would squash them sideways.

What a browser does with those attributes is not observable here; this
module pins that the markup asks for it.
"""

from __future__ import annotations

import re

from django.template.loader import render_to_string

from apps.weather.services.hourly_chart import HourlyChart, build_hourly_chart

TEMPLATE = "includes/_hourly_chart.html"
CHART_DATE = "2026-02-16"


def _chart() -> HourlyChart:
    """
    Build a chart whose day crosses freezing and carries snow and wind.

    Returns:
        The geometry, with every optional layer present.

    """
    hourly = [
        {
            "time": f"{CHART_DATE}T{hour:02d}:00",
            "temperature_2m": -4.0 + hour * 0.4,
            "precipitation": 0.2,
            "snowfall": 0.5,
            "wind_speed_10m": 12.0,
            "wind_gusts_10m": 24.0,
            "wind_direction_10m": 225.0,
            "freezing_level_height": 1500.0 + hour * 10,
        }
        for hour in range(24)
    ]
    chart = build_hourly_chart(
        {"date": CHART_DATE, "hourly": hourly},
        elevation=1436.0,
        location_label="Verbier village",
    )
    assert chart is not None
    return chart


def _render() -> str:
    """Render the chart partial and return its HTML."""
    return render_to_string(TEMPLATE, {"chart": _chart()})


def _plot_svg(html: str, testid: str) -> str:
    """
    Return the opening tag of the plot SVG carrying ``testid``.

    Args:
        html: The rendered partial.
        testid: The plot's ``data-testid``.

    Returns:
        The ``<svg …>`` opening tag.

    """
    match = re.search(rf'<svg[^>]*data-testid="{testid}"[^>]*>', html)
    assert match is not None, testid
    return match.group(0)


def _plot_body(html: str, testid: str) -> str:
    """
    Return the whole plot SVG carrying ``testid``, children included.

    Scoped to the plot because the legend below it draws its own key
    glyphs at a fixed size, where a non-scaling stroke has nothing to do.

    Args:
        html: The rendered partial.
        testid: The plot's ``data-testid``.

    Returns:
        The ``<svg …>…</svg>`` element.

    """
    start = html.index(_plot_svg(html, testid))
    return html[start : html.index("</svg>", start)]


def _direction_row(html: str) -> str:
    """
    Return the direction row's markup, up to the wind hour axis after it.

    Args:
        html: The rendered partial.

    Returns:
        The row's HTML.

    """
    start = html.index('data-testid="hourly-chart-direction"')
    return html[start : html.index('data-testid="hourly-chart-wind-axis"', start)]


class TestTheCardSpansTheColumn:
    """The chart no longer stops at the 640px column."""

    def test_the_card_carries_no_width_cap(self) -> None:
        """``max-w-narrow`` held the chart narrower than the day picker."""
        html = _render()
        assert "max-w-narrow" not in html


class TestPlotsStretchSidewaysOnly:
    """Each plot widens with the column and keeps its authored height."""

    def test_every_plot_ignores_the_viewbox_aspect_ratio(self) -> None:
        """Without ``none`` the drawing would scale up in proportion."""
        html = _render()
        for testid, height in (
            ("hourly-chart-temp", 200),
            ("hourly-chart-precip", 110),
            ("hourly-chart-wind", 92),
        ):
            tag = _plot_svg(html, testid)
            assert 'preserveAspectRatio="none"' in tag
            assert f"aspect-ratio: 606 / {height}" in tag
            assert f"max-height: {height}px" in tag
            assert "h-auto" not in tag

    def test_every_stroke_keeps_its_weight(self) -> None:
        """A stretched line would otherwise thicken along one axis only."""
        html = _render()
        for testid in ("hourly-chart-temp", "hourly-chart-precip", "hourly-chart-wind"):
            strokes = re.findall(
                r"<(?:line|polyline)\b[^>]*>", _plot_body(html, testid)
            )
            assert strokes, testid
            for tag in strokes:
                assert 'vector-effect="non-scaling-stroke"' in tag, tag

    def test_the_wind_ticks_reach_the_plot_foot(self) -> None:
        """Every wind hour tick ends at the plot's 92-unit foot."""
        ticks = re.findall(r"<line\b[^>]*>", _plot_body(_render(), "hourly-chart-wind"))
        assert len(ticks) == 9
        assert all('y2="92"' in tag for tag in ticks)


class TestArrowsAreGlyphs:
    """The wind arrows keep their shape at every width."""

    def test_each_arrow_is_an_html_glyph_rotated_by_css(self) -> None:
        """
        One glyph per block, placed by per-cent and rotated by the bearing.

        225° all day, so every arrow is rotated 225 degrees.
        """
        html = _render()
        arrows = re.findall(r'<svg[^>]*data-testid="hourly-chart-arrow"[^>]*>', html)
        assert len(arrows) == 8
        for tag in arrows:
            assert "transform: rotate(225deg)" in tag
            assert re.search(r"left: \d+\.\d{2}%", tag)

    def test_the_direction_row_is_not_a_stretched_svg(self) -> None:
        """The old row drew paths in a 606-wide viewBox, which would squash."""
        row = _direction_row(_render())
        assert 'viewBox="0 0 606' not in row
        assert 'transform="rotate(' not in row
        assert 'viewBox="0 0 30 30"' in row
