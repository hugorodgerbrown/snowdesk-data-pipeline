"""
tests/weather/services/test_weather_chart.py — the outlook chart's geometry.

Covers ``apps.weather.services.weather_chart.build_forecast_chart``: what it
plots, what it refuses to plot, and the two scale decisions that would
otherwise be silent — the zero line, which appears only when the window
crosses freezing, and the padding, which is what keeps a flat week from
dividing by zero.

The chart is pure arithmetic over a ``ForecastPanel``, so none of this needs
a database.
"""

from __future__ import annotations

import datetime

from apps.weather.services.weather_chart import (
    PLOT_BOTTOM,
    PLOT_LEFT,
    PLOT_RIGHT,
    PLOT_TOP,
    SCALE_PADDING_C,
    build_forecast_chart,
)
from apps.weather.services.weather_display import ForecastPanel, ForecastPanelDay

ANCHOR = datetime.date(2026, 1, 12)


def _panel(*bounds: tuple[float | None, float | None]) -> ForecastPanel:
    """Build a panel with one day per (max, min) pair.

    Args:
        *bounds: One ``(temp_max, temp_min)`` pair per consecutive day.
            Either half may be ``None``, which is what a provider gap looks
            like.

    Returns:
        A panel carrying those days.

    """
    return ForecastPanel(
        days=[
            ForecastPanelDay(
                date=ANCHOR + datetime.timedelta(days=index),
                weekday_label=(ANCHOR + datetime.timedelta(days=index)).strftime("%a"),
                icon_bucket="cloudy",
                icon_filename="cloudy.svg",
                condition_label="Overcast",
                temp_max=temp_max,
                temp_min=temp_min,
                snowfall_sum=None,
                freezing_level_height=None,
                # The chart reads neither, but ForecastPanelDay is total.
                wind_speed_max=None,
                wind_bearing=None,
                hourly=[],
                selectable=False,
            )
            for index, (temp_max, temp_min) in enumerate(bounds)
        ]
    )


class TestRefusesToDraw:
    """The cases that produce no chart at all."""

    def test_returns_none_without_a_panel(self) -> None:
        """A location with no weather row has no chart."""
        assert build_forecast_chart(None) is None

    def test_returns_none_for_a_single_day(self) -> None:
        """One point is not a line.

        A one-day window would draw a dot and two axis labels, which says
        less than the day strip beneath it already does.
        """
        assert build_forecast_chart(_panel((4.0, -1.0))) is None

    def test_returns_none_when_too_few_days_have_both_bounds(self) -> None:
        """A day short of either bound drops out, and may take the chart.

        Three days here, but only one is plottable — so the result is the
        same as a one-day window rather than a chart bridging the gaps.
        Interpolating would put a temperature on the page that nobody
        forecast.
        """
        panel = _panel((4.0, -1.0), (None, -2.0), (6.0, None))

        assert build_forecast_chart(panel) is None


class TestGeometry:
    """Where the points land."""

    def test_plots_one_point_per_plottable_day(self) -> None:
        """Days with both bounds become points, in order."""
        chart = build_forecast_chart(_panel((0.0, -6.0), (3.0, -4.0), (6.0, 1.0)))

        assert chart is not None
        assert [point["label"] for point in chart["points"]] == ["Mon", "Tue", "Wed"]
        assert [point["temp_max"] for point in chart["points"]] == [0.0, 3.0, 6.0]

    def test_skips_a_day_missing_a_bound_without_bridging_it(self) -> None:
        """An incomplete day is absent, not interpolated."""
        chart = build_forecast_chart(
            _panel((0.0, -6.0), (None, -4.0), (6.0, 1.0), (2.0, -3.0))
        )

        assert chart is not None
        assert [point["temp_max"] for point in chart["points"]] == [0.0, 6.0, 2.0]

    def test_spans_the_plot_from_first_day_to_last(self) -> None:
        """The window fills the plot's width whatever its length."""
        chart = build_forecast_chart(_panel((0.0, -6.0), (3.0, -4.0), (6.0, 1.0)))

        assert chart is not None
        assert chart["points"][0]["x"] == PLOT_LEFT
        assert chart["points"][-1]["x"] == PLOT_RIGHT

    def test_puts_the_warmest_reading_highest_on_the_page(self) -> None:
        """SVG y grows downward, so the warmest day takes the smallest y."""
        chart = build_forecast_chart(_panel((-2.0, -9.0), (8.0, 1.0)))

        assert chart is not None
        coldest_day, warmest_day = chart["points"]
        assert warmest_day["y_max"] < coldest_day["y_max"]

    def test_keeps_every_point_inside_the_plot(self) -> None:
        """The padded scale is what stops a line running along the edge."""
        chart = build_forecast_chart(_panel((-2.0, -9.0), (8.0, 1.0), (3.0, -4.0)))

        assert chart is not None
        for point in chart["points"]:
            assert PLOT_TOP < point["y_max"] < PLOT_BOTTOM
            assert PLOT_TOP < point["y_min"] < PLOT_BOTTOM
            assert point["y_max"] <= point["y_min"]

    def test_pads_the_scale_beyond_the_readings(self) -> None:
        """The axis extremes sit outside the data, by a fixed margin."""
        chart = build_forecast_chart(_panel((-2.0, -9.0), (8.0, 1.0)))

        assert chart is not None
        assert chart["warmest"] == 8.0 + SCALE_PADDING_C
        assert chart["coldest"] == -9.0 - SCALE_PADDING_C

    def test_band_closes_the_max_line_back_along_the_min_line(self) -> None:
        """The band is one polygon, out along the highs and back along the lows."""
        chart = build_forecast_chart(_panel((0.0, -6.0), (3.0, -4.0)))

        assert chart is not None
        assert chart["band"].startswith(chart["max_line"])
        first, last = chart["points"][0], chart["points"][-1]
        # Back along the lows means the LAST day's low comes first.
        assert chart["band"].endswith(f"{first['x']},{first['y_min']}")
        assert f"{last['x']},{last['y_min']}" in chart["band"]


class TestZeroLine:
    """The 0 °C reference, which is conditional on purpose."""

    def test_drawn_when_the_window_crosses_freezing(self) -> None:
        """A thaw is the thing the chart is most useful for showing."""
        chart = build_forecast_chart(_panel((-1.0, -8.0), (6.0, 1.0)))

        assert chart is not None
        assert chart["zero_y"] is not None
        assert PLOT_TOP < chart["zero_y"] < PLOT_BOTTOM

    def test_absent_when_the_window_stays_above_freezing(self) -> None:
        """A summer week would pin the line to the plot floor.

        A line sitting on the edge of its own plot implies a boundary the
        data never approached, so it is omitted rather than drawn.
        """
        chart = build_forecast_chart(_panel((18.0, 9.0), (24.0, 13.0)))

        assert chart is not None
        assert chart["zero_y"] is None

    def test_absent_when_the_window_stays_below_freezing(self) -> None:
        """The same, at the other end."""
        chart = build_forecast_chart(_panel((-4.0, -12.0), (-2.0, -15.0)))

        assert chart is not None
        assert chart["zero_y"] is None


class TestFlatWindow:
    """A week with no range still has to produce a scale."""

    def test_draws_a_completely_flat_week_down_the_middle(self) -> None:
        """Every day identical has no range of its own to scale against.

        The padding is what saves it: applied to both ends before anything
        is divided, it leaves a span of twice SCALE_PADDING_C, so the line
        lands mid-plot instead of dividing by zero. This is the case a
        separate minimum-span floor was written for — the floor could
        never fire, because the padded span already exceeded it.
        """
        chart = build_forecast_chart(_panel((2.0, 2.0), (2.0, 2.0), (2.0, 2.0)))

        assert chart is not None
        assert chart["warmest"] - chart["coldest"] == SCALE_PADDING_C * 2
        midpoint = (PLOT_TOP + PLOT_BOTTOM) / 2
        for point in chart["points"]:
            assert point["y_max"] == midpoint
            assert point["y_min"] == midpoint
