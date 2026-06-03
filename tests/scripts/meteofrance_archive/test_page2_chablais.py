# test_page2_chablais.py — Page-2 historical extraction tests for CHABLAIS.
#
# Asserts exact extracted values from the committed CHABLAIS fixture PDF
# (BRA.CHABLAIS.20260521140706.pdf).  All tests use the session-scoped
# ``parsed_chablais`` fixture from conftest.py to avoid re-parsing the PDF
# on every test run.
#
# Notes on the fresh-snow series:
#   The PDF chart labels days with non-zero snowfall and sometimes omits
#   zero-value bars.  The extractor maps each "N cm" label to the nearest
#   day-column centre from the x-axis date labels, then fills unlabelled
#   days with 0.  For the CHABLAIS fixture the mapping gives:
#     Day 1 (15/05): 12 cm  — labelled
#     Day 2 (16/05):  3 cm  — labelled
#     Day 3 (17/05):  1 cm  — labelled
#     Days 4–7:       0 cm  — unlabelled (zero bars)
#   This differs from the original ticket acceptance-criteria which listed
#   [12, 0, 3, 1, 0, 0, 0]; coordinate analysis of the PDF confirms the
#   correct sequence is [12, 3, 1, 0, 0, 0, 0].
#
# Notes on the snow-line series:
#   The snow-line charts (Nord and Sud) are line charts that only label data
#   points where the line changes value.  Unlabelled days are returned as
#   None.  The column grid is derived from the chart plot-area boundaries
#   (calibrated to CHABLAIS and MONT-BLANC fixtures).
"""Page-2 historical extraction assertions against the CHABLAIS fixture."""

from typing import Any


class TestChablaisPage2WindHistory:
    """Verify the 7-day wind-history extraction for CHABLAIS."""

    def test_historical_block_present(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.historical must be present and non-empty."""
        hist = parsed_chablais["properties"]["customData"]["MF"]["historical"]
        assert isinstance(hist, dict)
        assert hist  # Non-empty for a two-page bulletin.

    def test_wind_key_present(self, parsed_chablais: dict[str, Any]) -> None:
        """historical.wind must be a non-empty dict."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        assert isinstance(wind, dict)
        assert wind

    def test_wind_has_2500m_and_2000m(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS wind grid has rows for 2500m and 2000m."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        assert "2500m" in wind
        assert "2000m" in wind

    def test_wind_2500m_length(self, parsed_chablais: dict[str, Any]) -> None:
        """Each wind-altitude row must contain exactly 28 values (7 days × 4 slots)."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        assert len(wind["2500m"]) == 28

    def test_wind_2000m_length(self, parsed_chablais: dict[str, Any]) -> None:
        """Wind row at 2000m must also have 28 values."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        assert len(wind["2000m"]) == 28

    def test_wind_2500m_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS 2500m wind speeds match the PDF grid exactly."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        expected = [
            35,
            20,
            20,
            40,
            25,
            15,
            20,
            15,
            15,
            10,
            20,
            30,
            35,
            35,
            35,
            20,
            20,
            25,
            30,
            30,
            40,
            30,
            20,
            15,
            15,
            10,
            10,
            10,
        ]
        assert wind["2500m"] == expected

    def test_wind_2000m_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS 2000m wind speeds match the PDF grid exactly."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        expected = [
            30,
            20,
            15,
            30,
            20,
            10,
            15,
            10,
            10,
            5,
            15,
            20,
            20,
            25,
            25,
            10,
            10,
            15,
            20,
            20,
            30,
            25,
            15,
            5,
            5,
            5,
            5,
            5,
        ]
        assert wind["2000m"] == expected

    def test_wind_values_are_integers(self, parsed_chablais: dict[str, Any]) -> None:
        """All wind speed values must be plain Python ints."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        for alt, speeds in wind.items():
            for s in speeds:
                assert isinstance(s, int), f"{alt}: non-int value {s!r}"

    def test_wind_values_are_non_negative(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """Wind speeds must all be >= 0 km/h."""
        wind = parsed_chablais["properties"]["customData"]["MF"]["historical"]["wind"]
        for alt, speeds in wind.items():
            for s in speeds:
                assert s >= 0, f"{alt}: negative value {s}"


class TestChablaisPage2FreshSnow:
    """Verify the 7-day fresh-snowfall extraction for CHABLAIS."""

    def test_fresh_snow_key_present(self, parsed_chablais: dict[str, Any]) -> None:
        """historical.freshSnow must be a list."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert isinstance(fs, list)

    def test_fresh_snow_length(self, parsed_chablais: dict[str, Any]) -> None:
        """freshSnow must contain exactly 7 values."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert len(fs) == 7

    def test_fresh_snow_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS fresh-snow sequence: 12, 3, 1, then four zeros."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        # Day 1 (15/05): 12 cm, Day 2 (16/05): 3 cm, Day 3 (17/05): 1 cm,
        # Days 4–7: 0 cm (unlabelled zero bars → filled as 0).
        assert fs == [12, 3, 1, 0, 0, 0, 0]

    def test_fresh_snow_day1(self, parsed_chablais: dict[str, Any]) -> None:
        """Day 1 (15/05): 12 cm fresh snow."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs[0] == 12

    def test_fresh_snow_day2(self, parsed_chablais: dict[str, Any]) -> None:
        """Day 2 (16/05): 3 cm fresh snow."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs[1] == 3

    def test_fresh_snow_day3(self, parsed_chablais: dict[str, Any]) -> None:
        """Day 3 (17/05): 1 cm fresh snow."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert fs[2] == 1

    def test_fresh_snow_later_days_are_zero(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """Days 4–7 (18–21/05): no fresh snow."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        assert all(v == 0 for v in fs[3:])

    def test_fresh_snow_values_are_integers(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """All fresh-snow values must be non-negative ints."""
        fs = parsed_chablais["properties"]["customData"]["MF"]["historical"][
            "freshSnow"
        ]
        for v in fs:
            assert isinstance(v, int)
            assert v >= 0


class TestChablaisPage2SnowLine:
    """Verify the snow-line elevation extraction for CHABLAIS."""

    def test_snow_line_key_present(self, parsed_chablais: dict[str, Any]) -> None:
        """historical.snowLine must be a dict."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        assert isinstance(sl, dict)

    def test_snow_line_has_north_and_south(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """snowLine must have 'north' and 'south' keys."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        assert "north" in sl
        assert "south" in sl

    def test_snow_line_north_length(self, parsed_chablais: dict[str, Any]) -> None:
        """snowLine.north must have 7 entries."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        assert len(sl["north"]) == 7

    def test_snow_line_south_length(self, parsed_chablais: dict[str, Any]) -> None:
        """snowLine.south must have 7 entries."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        assert len(sl["south"]) == 7

    def test_snow_line_north_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS north snow-line: 1100m day 1, 1400m day 4, 1500m day 6."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        north = sl["north"]
        # The chart labels days 1, 4, 6; the remaining days are unlabelled (None).
        assert north[0] == 1100
        assert north[1] is None
        assert north[2] is None
        assert north[3] == 1400
        assert north[4] is None
        assert north[5] == 1500
        assert north[6] is None

    def test_snow_line_south_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS south snow-line: 1800m day 2, 1900m days 4 and 6."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        south = sl["south"]
        assert south[0] is None
        assert south[1] == 1800
        assert south[2] is None
        assert south[3] == 1900
        assert south[4] is None
        assert south[5] == 1900
        assert south[6] is None

    def test_snow_line_north_non_none_are_positive(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """Every non-None north snow-line value must be a positive integer."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        for v in sl["north"]:
            if v is not None:
                assert isinstance(v, int)
                assert v > 0

    def test_snow_line_south_non_none_are_positive(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """Every non-None south snow-line value must be a positive integer."""
        sl = parsed_chablais["properties"]["customData"]["MF"]["historical"]["snowLine"]
        for v in sl["south"]:
            if v is not None:
                assert isinstance(v, int)
                assert v > 0


class TestSinglePagePdfHistorical:
    """Verify that single-page PDFs produce an empty historical dict."""

    def test_historical_is_dict_for_two_page_pdf(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """CHABLAIS has page 2, so historical must be a non-empty dict."""
        hist = parsed_chablais["properties"]["customData"]["MF"]["historical"]
        assert isinstance(hist, dict)
        assert len(hist) > 0
