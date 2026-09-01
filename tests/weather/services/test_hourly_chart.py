"""
tests/weather/services/test_hourly_chart.py — the meteogram's geometry.

Covers ``apps.weather.services.hourly_chart``, which had no test module of
its own until SNOW-790 — only whatever the page tests happened to exercise
through a rendered template. The parts under test here are the ones a
template cannot check: that a coordinate is a string, that a percentage is
measured against the right containing block, and that a mark appears only
under the condition it is supposed to.

The daylight bar (SNOW-790) is the reason this file exists. It reads a
sunrise/sunset pair out of a day mapping whose shape differs between the two
callers — ``ForecastPanelDay`` supplies ``sunrise_local`` as ``"HH:MM"``,
the committed sample days supply ``sunrise`` as an ISO string — and it
positions two nested elements against two different denominators. Both are
silent failures if wrong: the band simply lands in the wrong place, and no
exception is raised to say so.
"""

import datetime

import pytest

from apps.weather.services.hourly_chart import (
    _daylight,
    _hour_of_day,
    _now_hour,
    build_hourly_chart,
)

# A day whose temperatures cross freezing, so the 0 °C rule is drawn.
CHART_DATE = "2026-02-16"


def _hourly_series(date: str) -> list[dict[str, object]]:
    """
    Build a 24-hour series with a temperature range that crosses zero.

    Args:
        date: The day's ISO date.

    Returns:
        Twenty-four hourly rows.

    """
    return [
        {
            "time": f"{date}T{hour:02d}:00",
            "temperature_2m": -4.0 + hour * 0.4,
            "precipitation": 0.2,
            "snowfall": 0.5,
            "wind_speed_10m": 12.0,
            "wind_gusts_10m": 24.0,
            "wind_direction_10m": 270.0,
            "freezing_level_height": 2100.0,
        }
        for hour in range(24)
    ]


def _day(**extra: object) -> dict[str, object]:
    """
    Build a chartable day mapping.

    Args:
        **extra: Keys to add or override — a sunrise/sunset pair, a date.

    Returns:
        The day mapping, ready for ``build_hourly_chart``.

    """
    return {"date": CHART_DATE, "hourly": _hourly_series(CHART_DATE), **extra}


class TestHourOfDay:
    """``_hour_of_day`` reads a time from every shape a caller passes."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("08:32", 8 + 32 / 60),
            ("2026-02-16T08:32", 8 + 32 / 60),
            ("2026-02-16T08:32:00+01:00", 8 + 32 / 60),
            (datetime.datetime(2026, 2, 16, 8, 32, tzinfo=datetime.UTC), 8 + 32 / 60),
            ("  08:32  ", 8 + 32 / 60),
        ],
    )
    def test_it_reads_every_shape_a_caller_passes(
        self, value: object, expected: float
    ) -> None:
        """A datetime, an ISO string and a bare HH:MM all read the same."""
        assert _hour_of_day(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", [None, "", "   ", "not a time", 8.5, {}])
    def test_it_returns_none_for_anything_unreadable(self, value: object) -> None:
        """An unreadable value costs the day its band, not its chart."""
        assert _hour_of_day(value) is None


class TestDaylight:
    """``_daylight`` picks a sunrise/sunset pair off the day."""

    def test_it_prefers_the_local_pair(self) -> None:
        """
        ``sunrise_local`` wins over ``sunrise``.

        The production caller supplies both shapes on the same mapping and
        the local pair is the one already in the location's own offset. A
        day is read from one pair or the other, never mixed.
        """
        day = _day(
            sunrise_local="08:32",
            sunset_local="18:58",
            sunrise=f"{CHART_DATE}T00:01",
            sunset=f"{CHART_DATE}T23:59",
        )
        assert _daylight(day) == pytest.approx((8 + 32 / 60, 18 + 58 / 60))

    def test_it_falls_back_to_the_raw_pair(self) -> None:
        """A sample day carries only ``sunrise``/``sunset``."""
        day = _day(sunrise=f"{CHART_DATE}T08:32", sunset=f"{CHART_DATE}T18:58")
        assert _daylight(day) == pytest.approx((8 + 32 / 60, 18 + 58 / 60))

    def test_it_reads_both_values_as_times_of_day(self) -> None:
        """
        The date is discarded, which is a documented limit.

        ``sunrise_local`` is a bare ``"HH:MM"`` and has no date to read, so
        the raw pair is treated the same way. A sunset on the FOLLOWING date
        — which Open-Meteo returns above the Arctic Circle in summer — comes
        back as an early-morning hour. Snowdesk covers the Alps and the
        Pyrenees, where that cannot arise; this test pins the behaviour so a
        ticket that takes the product north finds it rather than a surprise.
        """
        day = _day(sunrise=f"{CHART_DATE}T00:30", sunset="2026-02-17T02:00")
        assert _daylight(day) == pytest.approx((0.5, 2.0))

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"sunrise_local": "08:32"},
            {"sunrise_local": "08:32", "sunset_local": "08:32"},
            {"sunrise_local": "18:58", "sunset_local": "08:32"},
            {"sunrise_local": "nonsense", "sunset_local": "18:58"},
        ],
        ids=["absent", "half-a-pair", "no-daylight", "reversed", "unreadable"],
    )
    def test_it_returns_none_when_there_is_no_lit_run(
        self, extra: dict[str, object]
    ) -> None:
        """A polar night, a half pair and a bad value all draw no band."""
        assert _daylight(_day(**extra)) is None


class TestNowHour:
    """``_now_hour`` marks the clock only on the day being charted."""

    def test_it_reads_the_clock_on_todays_chart(self) -> None:
        """The marker is a fraction of an hour, not a whole one."""
        now = datetime.datetime(2026, 2, 16, 14, 30, tzinfo=datetime.UTC)
        assert _now_hour(_day(), now) == pytest.approx(14.5)

    @pytest.mark.parametrize(
        ("date", "now"),
        [
            (CHART_DATE, None),
            (CHART_DATE, datetime.datetime(2026, 2, 17, 14, 30, tzinfo=datetime.UTC)),
            ("nonsense", datetime.datetime(2026, 2, 16, 14, 30, tzinfo=datetime.UTC)),
        ],
        ids=["no-clock", "another-day", "unreadable-date"],
    )
    def test_it_marks_nothing_otherwise(
        self, date: str, now: datetime.datetime | None
    ) -> None:
        """A forecast for tomorrow has no current hour to mark."""
        assert _now_hour({"date": date}, now) is None


class TestAxisBar:
    """The temperature axis bar, as ``build_hourly_chart`` emits it."""

    def test_the_track_spans_the_plot_not_the_drawing(self) -> None:
        """
        The track is the only band measured against the drawing.

        40 units of left gutter and 46 of right gutter out of 606, so the
        plot the track must sit over is 520 wide starting at 40.
        """
        chart = build_hourly_chart(_day())
        assert chart is not None
        assert chart["axis_track"] == {"left": "6.60%", "width": "85.81%"}

    def test_the_lit_segment_is_measured_against_the_track(self) -> None:
        """
        The band nests inside the track, so its denominator is the track.

        06:00 to 18:00 is a quarter of the way along and half the day wide.
        Measured against the drawing instead, the same run would start at
        21.45% — most of two hours early — and nothing would raise.
        """
        chart = build_hourly_chart(_day(sunrise_local="06:00", sunset_local="18:00"))
        assert chart is not None
        assert chart["daylight_band"] == {"left": "25.00%", "width": "50.00%"}

    def test_the_marker_is_measured_against_the_drawing_not_the_track(self) -> None:
        """
        The pin is a sibling of the track, so its denominator differs.

        It crosses the bar with its ends standing clear above and below, so
        it cannot live inside the track — whose overflow-hidden clips the
        lit segment's corners and would clip the ends off. Midday is
        halfway along the *plot*, which is 49.50% of the 606-unit drawing
        and would be 50.00% of the track. Reading one as the other is a
        silent misplacement, which is what this pins.
        """
        chart = build_hourly_chart(
            _day(),
            now=datetime.datetime(2026, 2, 16, 12, 0, tzinfo=datetime.UTC),
        )
        assert chart is not None
        assert chart["now_marker"] == "49.50%"

    def test_a_day_without_a_readable_pair_keeps_its_chart(self) -> None:
        """The band is the only thing a missing sunrise costs the day."""
        chart = build_hourly_chart(_day())
        assert chart is not None
        assert chart["daylight_band"] is None
        assert chart["temp_segments"]

    def test_the_marker_is_absent_on_a_forward_day(self) -> None:
        """Only today's chart carries a clock."""
        chart = build_hourly_chart(
            _day(),
            now=datetime.datetime(2026, 2, 17, 12, 0, tzinfo=datetime.UTC),
        )
        assert chart is not None
        assert chart["now_marker"] is None


class TestGutterTicks:
    """The temperature chart's two vertical scales carry tick marks."""

    def test_every_scale_label_has_a_mark_at_its_own_height(self) -> None:
        """
        A label with nothing joining it to the plot reads as floating.

        ``top`` carries the offset that centres a text line on the value
        and ``y`` is the value's own height, so a tick drawn at ``top``
        would sit half a line out. They must not be equal.
        """
        chart = build_hourly_chart(_day(), elevation=1436.0)
        assert chart is not None

        for tick in [*chart["temp_ticks"], *chart["freezing_ticks"]]:
            assert isinstance(tick["y"], str)
            assert tick["y"] != tick["top"]

    def test_the_plot_carries_its_own_left_and_right_edges(self) -> None:
        """
        The night shading used to carry these for free.

        A reader took the plot's bounds from where the shaded blocks
        stopped; removing them left the temperature chart's two vertical
        scales with nothing closing them off. The edges are frame, so they
        sit at the plot's fixed x rather than at any value — and only on
        this chart, the one with scales to close.
        """
        chart = build_hourly_chart(_day())
        assert chart is not None

        assert chart["plot_edges"] == ["40", "560"]

    def test_the_marks_reach_from_the_plot_into_each_gutter(self) -> None:
        """
        Left ticks stop at the plot's edge; right ticks start there.

        40 units of left gutter and a plot 520 wide, so the two edges are
        40 and 560 and each tick runs outward from one of them.
        """
        chart = build_hourly_chart(_day())
        assert chart is not None

        assert chart["left_tick_x2"] == "40"
        assert float(chart["left_tick_x1"]) < 40
        assert chart["right_tick_x1"] == "560"
        assert float(chart["right_tick_x2"]) > 560


class TestReferenceRules:
    """What SNOW-790 removed from the plots, and what it left alone."""

    def test_the_plots_carry_no_cursor(self) -> None:
        """
        The clock is answered in the axis, not struck through the data.

        A full-height hairline in all four SVGs crossed every series and
        read as a plotted line.
        """
        chart = build_hourly_chart(
            _day(),
            now=datetime.datetime(2026, 2, 16, 12, 0, tzinfo=datetime.UTC),
        )
        assert chart is not None
        assert "cursor_x" not in chart

    def test_the_zero_rule_survives(self) -> None:
        """
        The 0 °C rule stays on the plot; only its legend row went.

        It is drawn whenever the day's range crosses freezing, which this
        series does — it runs -4 °C to +5.2 °C.
        """
        chart = build_hourly_chart(_day())
        assert chart is not None
        assert chart["zero_line_y"] is not None


class TestCoordinateFormatting:
    """Every coordinate leaves the service as an already-formatted string."""

    def test_no_coordinate_escapes_as_a_float(self) -> None:
        """
        Django localises floats on output.

        A bare float would render ``x="12,5"`` under a comma-decimal locale
        and the drawing would vanish with no error, so the formatting is
        done here where a template cannot forget it.
        """
        chart = build_hourly_chart(
            _day(sunrise_local="06:00", sunset_local="18:00"),
            elevation=1436.0,
            location_label="Verbier village",
            now=datetime.datetime(2026, 2, 16, 12, 0, tzinfo=datetime.UTC),
        )
        assert chart is not None
        assert isinstance(chart["now_marker"], str)
        band = chart["daylight_band"]
        assert band is not None
        assert all(isinstance(value, str) for value in band.values())
        assert all(isinstance(value, str) for value in chart["axis_track"].values())
        assert all(isinstance(x, str) for x in chart["hour_ticks"])


class TestAriaLabel:
    """The band's spoken equivalent."""

    def test_the_daylight_pair_is_spoken(self) -> None:
        """
        The bar is aria-hidden decoration, so the label has to carry it.

        The legend's "About this forecast" block is the other candidate home
        and is supplied by the component library alone — it never renders on
        the page that ships the chart.
        """
        chart = build_hourly_chart(_day(sunrise_local="08:32", sunset_local="18:58"))
        assert chart is not None
        assert "daylight 08:32 to 18:58" in chart["aria_label"]

    def test_a_day_without_a_pair_says_nothing_about_daylight(self) -> None:
        """Silence beats a made-up sunrise."""
        chart = build_hourly_chart(_day())
        assert chart is not None
        assert "daylight" not in chart["aria_label"]
