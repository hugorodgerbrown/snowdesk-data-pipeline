"""
tests/public/test_weather_tags.py — Tests for the wind-direction filters.

The load-bearing assertion in this module is that ``wind_arrow_rotation``
and ``compass_point`` AGREE. The bearing Open-Meteo stores is the direction
the wind blows FROM, and since SNOW-785 both filters report it: the arrow
points at the source and the label names it. Getting that backwards would
silently invert every arrow on the page and point a reader at the wrong
aspect, which is why the agreement is asserted across every sector rather
than inferred from one example.

Before SNOW-785 the arrow flew downwind and this module asserted the
opposite — a 180° opposition — so a regression that reinstates the offset
fails here loudly rather than looking like a restored convention.

The final class checks the panel filter and the hourly chart draw the same
rotation for the same bearing. They are separate implementations reached by
separate templates, and the whole point of SNOW-785 is that they cannot
disagree.
"""

import pytest

from apps.public.templatetags.weather_tags import compass_point, wind_arrow_rotation
from apps.weather.services.hourly_chart import _direction_arrows


class TestCompassPoint:
    """compass_point names the direction the wind comes FROM."""

    @pytest.mark.parametrize(
        ("bearing", "expected"),
        [
            (0, "N"),
            (45, "NE"),
            (90, "E"),
            (135, "SE"),
            (180, "S"),
            (225, "SW"),
            (270, "W"),
            (315, "NW"),
        ],
    )
    def test_each_cardinal_and_intercardinal_point(
        self, bearing: float, expected: str
    ) -> None:
        """The eight sector centres map to their own abbreviations."""
        assert compass_point(bearing) == expected

    @pytest.mark.parametrize(("bearing", "expected"), [(22, "N"), (23, "NE")])
    def test_rounds_to_the_nearest_sector(self, bearing: float, expected: str) -> None:
        """The boundary sits at 22.5 degrees, half a sector width."""
        assert compass_point(bearing) == expected

    def test_wraps_past_north(self) -> None:
        """350 is north, not an index past the end of the tuple."""
        assert compass_point(350) == "N"

    def test_360_is_north(self) -> None:
        """A full turn is a legitimate way to say north, not out of range."""
        assert compass_point(360) == "N"


class TestWindArrowRotation:
    """wind_arrow_rotation points the glyph AT THE SOURCE (SNOW-785)."""

    @pytest.mark.parametrize(
        ("bearing", "expected"),
        [
            (0, 0.0),
            (90, 90.0),
            (180, 180.0),
            (270, 270.0),
        ],
    )
    def test_rotation_is_the_bearing_itself(
        self, bearing: float, expected: float
    ) -> None:
        """A wind from the west (270) points the arrow west (270)."""
        assert wind_arrow_rotation(bearing) == expected

    @pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315])
    def test_arrow_agrees_with_the_named_source(self, bearing: float) -> None:
        """The arrow and the label point the same way, in every sector.

        This is the whole contract between the two filters after
        SNOW-785: both name origin. Asserting it across every sector
        catches an inversion that a single example could miss — and it
        fails loudly if the old ``+ 180`` is ever reinstated.
        """
        rotation = wind_arrow_rotation(bearing)
        assert rotation is not None
        assert (rotation - bearing) % 360 == 0


class TestUnreadableValues:
    """Both filters degrade to None rather than raising."""

    @pytest.mark.parametrize("value", [None, "", "gusty", [], {}])
    def test_returns_none(self, value: object) -> None:
        """A missing or non-numeric bearing draws no arrow at all.

        Rows written before SNOW-778 carry no direction key, so None is
        the ordinary case rather than an error path.
        """
        assert compass_point(value) is None
        assert wind_arrow_rotation(value) is None

    def test_numeric_string_is_accepted(self) -> None:
        """The value arrives from a JSON column, so it is coerced."""
        assert compass_point("270") == "W"


class TestPanelAndChartAgree:
    """The two wind arrows on the forecast page point the same way.

    ``_weather_panel.html`` rotates its glyph with ``wind_arrow_rotation``;
    the hourly chart builds its own ``transform`` in
    ``apps.weather.services.hourly_chart``. Both are on
    ``/weather/<short_id>/`` (SNOW-786), and SNOW-785 exists because they
    used to disagree by half a turn. Two implementations reached by two
    templates will drift unless something asserts they cannot.
    """

    @pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315, 359])
    def test_the_same_bearing_yields_the_same_rotation(self, bearing: float) -> None:
        """One bearing, one direction on screen, whichever surface draws it."""
        arrows = _direction_arrows([bearing])

        assert len(arrows) == 1
        chart_rotation = float(arrows[0]["transform"].split("(")[1].split(" ")[0])
        panel_rotation = wind_arrow_rotation(bearing)

        assert panel_rotation is not None
        assert (chart_rotation - panel_rotation) % 360 == 0

    @pytest.mark.parametrize("bearing", [0, 90, 180, 270])
    def test_both_agree_with_the_compass_label(self, bearing: float) -> None:
        """And with the words, so the glyph never contradicts the text."""
        arrows = _direction_arrows([bearing])

        assert arrows[0]["label"] == compass_point(bearing)
