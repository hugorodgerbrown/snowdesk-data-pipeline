# test_page2_mont_blanc.py — Page-2 historical extraction tests for MONT-BLANC.
#
# Asserts exact extracted values from the committed MONT-BLANC fixture PDF
# (BRA.MONT-BLANC.20260521140702.pdf).  Uses the session-scoped
# ``parsed_mont_blanc`` fixture from conftest.py.
#
# MONT-BLANC differences from CHABLAIS:
#   - Wind altitudes: 4000m (not 2500m) + 2000m
#   - Fresh snow: 12 cm day 1, 5 cm day 2, 0 cm days 3–7
#   - Snow-line: north values differ at day 1 (1300m vs 1100m); south unchanged
"""Page-2 historical extraction assertions against the MONT-BLANC fixture."""

from typing import Any


class TestMontBlancPage2WindHistory:
    """Verify the 7-day wind-history extraction for MONT-BLANC."""

    def test_wind_has_4000m_and_2000m(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC wind grid has rows for 4000m and 2000m."""
        wind = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"]["wind"]
        assert "4000m" in wind
        assert "2000m" in wind

    def test_wind_4000m_length(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Wind row at 4000m must contain exactly 28 values."""
        wind = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"]["wind"]
        assert len(wind["4000m"]) == 28

    def test_wind_4000m_values(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC 4000m wind speeds match the PDF grid exactly."""
        wind = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"]["wind"]
        expected = [
            40,
            40,
            30,
            35,
            40,
            45,
            35,
            40,
            40,
            25,
            25,
            35,
            30,
            40,
            40,
            45,
            40,
            30,
            40,
            50,
            50,
            60,
            50,
            60,
            60,
            45,
            40,
            40,
        ]
        assert wind["4000m"] == expected

    def test_wind_2000m_values(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC 2000m wind speeds match the PDF grid exactly."""
        wind = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"]["wind"]
        expected = [
            20,
            20,
            20,
            30,
            20,
            10,
            10,
            10,
            10,
            5,
            10,
            15,
            15,
            15,
            20,
            20,
            15,
            20,
            15,
            10,
            20,
            10,
            15,
            5,
            5,
            5,
            5,
            5,
        ]
        assert wind["2000m"] == expected


class TestMontBlancPage2FreshSnow:
    """Verify the fresh-snowfall extraction for MONT-BLANC."""

    def test_fresh_snow_length(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """freshSnow must contain exactly 7 values."""
        fs = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert len(fs) == 7

    def test_fresh_snow_values(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC fresh-snow sequence: 12, 5, then five zeros."""
        fs = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs == [12, 5, 0, 0, 0, 0, 0]

    def test_fresh_snow_day1(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Day 1 (15/05): 12 cm fresh snow."""
        fs = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs[0] == 12

    def test_fresh_snow_day2(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Day 2 (16/05): 5 cm fresh snow."""
        fs = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs[1] == 5


class TestMontBlancPage2SnowLine:
    """Verify the snow-line elevation extraction for MONT-BLANC."""

    def test_snow_line_north_day1(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC north snow-line day 1: 1300m."""
        sl = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "snowLine"
        ]
        assert sl["north"][0] == 1300

    def test_snow_line_north_day4(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC north snow-line day 4: 1400m (same as CHABLAIS)."""
        sl = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "snowLine"
        ]
        assert sl["north"][3] == 1400

    def test_snow_line_south_day2(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC south snow-line day 2: 1800m."""
        sl = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "snowLine"
        ]
        assert sl["south"][1] == 1800

    def test_snow_line_north_length(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """snowLine.north must have 7 entries."""
        sl = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "snowLine"
        ]
        assert len(sl["north"]) == 7

    def test_snow_line_south_length(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """snowLine.south must have 7 entries."""
        sl = parsed_mont_blanc["properties"]["customData"]["MF"]["historical"][
            "snowLine"
        ]
        assert len(sl["south"]) == 7
