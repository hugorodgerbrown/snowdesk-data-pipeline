"""
tests/public/test_weather_tags.py — Tests for the wind-direction filters.

The load-bearing assertion in this module is that ``wind_arrow_rotation``
and ``compass_point`` disagree by 180 degrees. The bearing Open-Meteo
stores is the direction the wind blows FROM; the arrow is drawn pointing
where it blows TO. Getting that backwards would silently invert every
arrow on the page and point a reader at the wrong aspect, which is why the
opposition is asserted directly rather than inferred from one example.
"""

import pytest

from apps.public.templatetags.weather_tags import compass_point, wind_arrow_rotation


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
    """wind_arrow_rotation points the glyph DOWNWIND."""

    @pytest.mark.parametrize(
        ("bearing", "expected"),
        [
            (0, 180.0),
            (90, 270.0),
            (180, 0.0),
            (270, 90.0),
        ],
    )
    def test_rotation_is_the_reciprocal_bearing(
        self, bearing: float, expected: float
    ) -> None:
        """A wind from the west (270) points the arrow east (90)."""
        assert wind_arrow_rotation(bearing) == expected

    @pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315])
    def test_arrow_opposes_the_named_source(self, bearing: float) -> None:
        """The arrow and the label are always half a turn apart.

        This is the whole contract between the two filters: one shows
        travel, the other names origin. Asserting it across every sector
        catches an inversion that a single example could miss.
        """
        rotation = wind_arrow_rotation(bearing)
        assert rotation is not None
        assert (rotation - bearing) % 360 == 180


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
