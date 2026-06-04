# test_to_caaml_chablais.py — End-to-end parse tests for the CHABLAIS fixture.
"""End-to-end parse tests against the committed CHABLAIS BRA PDF fixture."""

from typing import Any


class TestChablaisdangerRatings:
    """Verify danger rating extraction for a single-level bulletin."""

    def test_returns_one_rating(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS is a single-level bulletin — one dangerRating entry."""
        result = parsed_chablais
        ratings = result["properties"]["dangerRatings"]
        assert isinstance(ratings, list)
        assert len(ratings) == 1

    def test_main_value_is_low(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS 2026-05-21: danger = 1 (faible / low)."""
        result = parsed_chablais
        rating = result["properties"]["dangerRatings"][0]
        assert rating["mainValue"] == "low"

    def test_elevation_bounds_are_none(self, parsed_chablais: dict[str, Any]) -> None:
        """Single-level rating should have no elevation bounds."""
        result = parsed_chablais
        elev = result["properties"]["dangerRatings"][0]["elevation"]
        assert elev["lowerBound"] is None
        assert elev["upperBound"] is None


class TestChablaishighlights:
    """Verify headline extraction."""

    def test_headline_is_present(self, parsed_chablais: dict[str, Any]) -> None:
        """Highlights should be extracted."""
        result = parsed_chablais
        assert result["properties"]["highlights"] != ""

    def test_headline_contains_manteau(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS 2026-05-21 headline: 'MANTEAU NEIGEUX STABLE.'"""
        result = parsed_chablais
        assert "MANTEAU" in result["properties"]["highlights"]


class TestChablaisAvalancheProblems:
    """Verify avalanche problem extraction."""

    def test_one_problem(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS has one SAT label — one avalanche problem."""
        result = parsed_chablais
        problems = result["properties"]["avalancheProblems"]
        assert len(problems) == 1

    def test_problem_type_is_wet_snow(self, parsed_chablais: dict[str, Any]) -> None:
        """SAT 'neige humide' → problemType 'wet_snow'."""
        result = parsed_chablais
        assert result["properties"]["avalancheProblems"][0]["problemType"] == "wet_snow"

    def test_sat_label_preserved(self, parsed_chablais: dict[str, Any]) -> None:
        """The raw SAT label should be stored in the satLabel field."""
        result = parsed_chablais
        assert (
            result["properties"]["avalancheProblems"][0]["satLabel"] == "neige humide"
        )

    def test_aspects_non_empty(self, parsed_chablais: dict[str, Any]) -> None:
        """Aspects list must be non-empty: prose contains 'ubacs' and 'adrets'."""
        aspects = parsed_chablais["properties"]["avalancheProblems"][0]["aspects"]
        assert isinstance(aspects, list)
        assert len(aspects) > 0

    def test_aspects_include_ubac_side(self, parsed_chablais: dict[str, Any]) -> None:
        """'En ubacs' in prose → N, NE, NW present."""
        aspects = parsed_chablais["properties"]["avalancheProblems"][0]["aspects"]
        assert "N" in aspects
        assert "NE" in aspects
        assert "NW" in aspects

    def test_aspects_exclude_east(self, parsed_chablais: dict[str, Any]) -> None:
        """'E' must not appear — 'est' in prose is the verb, not compass."""
        aspects = parsed_chablais["properties"]["avalancheProblems"][0]["aspects"]
        assert "E" not in aspects

    def test_elevation_lower_bound(self, parsed_chablais: dict[str, Any]) -> None:
        """'au-dessus de 2500 m' → elevation.lowerBound == 2500."""
        elev = parsed_chablais["properties"]["avalancheProblems"][0]["elevation"]
        assert elev.get("lowerBound") == 2500

    def test_avalanche_size(self, parsed_chablais: dict[str, Any]) -> None:
        """'Taille 1' in prose → avalancheSize == '1'."""
        size = parsed_chablais["properties"]["avalancheProblems"][0].get(
            "avalancheSize"
        )
        assert size == "1"

    def test_frequency_absent(self, parsed_chablais: dict[str, Any]) -> None:
        """No frequency word in CHABLAIS prose → frequency key absent."""
        problem = parsed_chablais["properties"]["avalancheProblems"][0]
        assert "frequency" not in problem


class TestChablaisWeather:
    """Verify weather extraction."""

    def test_weather_comment_present(self, parsed_chablais: dict[str, Any]) -> None:
        """Weather comment should be non-empty."""
        result = parsed_chablais
        assert result["properties"]["weather"]["comment"] != ""

    def test_isotherm_four_slots(self, parsed_chablais: dict[str, Any]) -> None:
        """Isotherm should produce 4 altitude values."""
        result = parsed_chablais
        iso = result["properties"]["weather"]["isotherm0"]
        assert len(iso) == 4

    def test_isotherm_values(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS: isotherm 4000/4000/4000/4100 m."""
        result = parsed_chablais
        assert result["properties"]["weather"]["isotherm0"] == [4000, 4000, 4000, 4100]

    def test_wind_table_two_rows(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS wind table: two altitude rows."""
        result = parsed_chablais
        wind = result["properties"]["weather"]["wind"]
        assert len(wind) == 2

    def test_wind_row_2000m(self, parsed_chablais: dict[str, Any]) -> None:
        """First wind row at 2000m with speeds [5, 0, 0, 5] km/h."""
        result = parsed_chablais
        row = result["properties"]["weather"]["wind"][0]
        assert row["altitude"] == "2000m"
        assert row["speeds"] == [5, 0, 0, 5]

    def test_wind_row_2500m(self, parsed_chablais: dict[str, Any]) -> None:
        """Second wind row at 2500m with speeds [10, 5, 5, 10] km/h."""
        result = parsed_chablais
        row = result["properties"]["weather"]["wind"][1]
        assert row["altitude"] == "2500m"
        assert row["speeds"] == [10, 5, 5, 10]


class TestChablaisCommentHtml:
    """Verify that prose comment fields are emitted as HTML (not plain-text)."""

    def test_snowpack_comment_contains_html(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """snowpackStructure.comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_chablais["properties"]["snowpackStructure"]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment

    def test_avalanche_activity_comment_contains_html(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """avalancheActivity.comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_chablais["properties"]["avalancheActivity"]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment

    def test_tendency_comment_contains_html(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """tendency[0].comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_chablais["properties"]["tendency"][0]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment


class TestChablaisTendency:
    """Verify tendency extraction."""

    def test_tendency_present(self, parsed_chablais: dict[str, Any]) -> None:
        """Tendency list should be non-empty."""
        result = parsed_chablais
        assert len(result["properties"]["tendency"]) == 1

    def test_tendency_danger_rating(self, parsed_chablais: dict[str, Any]) -> None:
        """Tendency danger: 'low' (faible)."""
        result = parsed_chablais
        assert result["properties"]["tendency"][0]["dangerRating"] == "low"

    def test_tendency_type(self, parsed_chablais: dict[str, Any]) -> None:
        """Tendency type: 'steady' (stationnaire)."""
        result = parsed_chablais
        assert result["properties"]["tendency"][0]["tendencyType"] == "steady"


class TestChablaisEnvelopeShape:
    """Verify the outer GeoJSON Feature envelope shape."""

    def test_feature_type(self, parsed_chablais: dict[str, Any]) -> None:
        """Root object must be GeoJSON Feature."""
        result = parsed_chablais
        assert result["type"] == "Feature"

    def test_geometry_is_none(self, parsed_chablais: dict[str, Any]) -> None:
        """Geometry must be None (no spatial footprint in BRA data)."""
        result = parsed_chablais
        assert result["geometry"] is None

    def test_region_id(self, parsed_chablais: dict[str, Any]) -> None:
        """Region ID must follow FR-{MASSIF} format."""
        result = parsed_chablais
        regions = result["properties"]["regions"]
        assert len(regions) >= 1
        assert regions[0]["regionID"] == "FR-CHABLAIS"

    def test_valid_time_present(self, parsed_chablais: dict[str, Any]) -> None:
        """validTime block must be present with startTime and endTime."""
        result = parsed_chablais
        vt = result["properties"]["validTime"]
        assert "startTime" in vt
        assert "endTime" in vt

    def test_lang_is_fr(self, parsed_chablais: dict[str, Any]) -> None:
        """Language must be 'fr'."""
        result = parsed_chablais
        assert result["properties"]["lang"] == "fr"


class TestChablaiscustomData:
    """Verify the customData.MF block."""

    def test_massif_slug(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.massif must be 'CHABLAIS'."""
        result = parsed_chablais
        assert result["properties"]["customData"]["MF"]["massif"] == "CHABLAIS"

    def test_date(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.date must be '2026-05-22' (bulletin validity date)."""
        result = parsed_chablais
        assert result["properties"]["customData"]["MF"]["date"] == "2026-05-22"

    def test_sat_labels_in_custom_data(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.typicalAvalancheSituations must list the SAT labels."""
        result = parsed_chablais
        sat = result["properties"]["customData"]["MF"]["typicalAvalancheSituations"]
        assert "neige humide" in sat

    def test_source_file(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.source_file must be the PDF filename."""
        result = parsed_chablais
        src = result["properties"]["customData"]["MF"]["source_file"]
        assert src == "BRA.CHABLAIS.20260521140706.pdf"


class TestChablaisSpontaneousTriggeredSplit:
    """Verify that customData.MF carries the spontaneous/triggered split (SNOW-257)."""

    def test_spontaneous_key_present(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.spontaneous must be present."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        assert "spontaneous" in mf

    def test_triggered_key_present(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.triggered must be present."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        assert "triggered" in mf

    def test_spontaneous_is_non_empty(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.spontaneous must be a non-empty string for CHABLAIS."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        assert isinstance(mf["spontaneous"], str)
        assert len(mf["spontaneous"]) > 0

    def test_triggered_is_non_empty(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.triggered must be a non-empty string for CHABLAIS."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        assert isinstance(mf["triggered"], str)
        assert len(mf["triggered"]) > 0

    def test_spontaneous_contains_html(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.spontaneous must carry HTML markup."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        html = mf["spontaneous"]
        assert "<h2>" in html or "<p>" in html or "<li>" in html

    def test_triggered_contains_html(self, parsed_chablais: dict[str, Any]) -> None:
        """customData.MF.triggered must carry HTML markup."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        html = mf["triggered"]
        assert "<h2>" in html or "<p>" in html or "<li>" in html

    def test_spontaneous_differs_from_triggered(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """The two split fields must differ — they come from different source sections."""
        mf = parsed_chablais["properties"]["customData"]["MF"]
        assert mf["spontaneous"] != mf["triggered"]

    def test_concatenated_comment_still_present(
        self, parsed_chablais: dict[str, Any]
    ) -> None:
        """avalancheActivity.comment (backward-compat field) must still be present."""
        comment = parsed_chablais["properties"]["avalancheActivity"]["comment"]
        assert isinstance(comment, str)
        assert len(comment) > 0


class TestChablaisHighlightDetection:
    """Verify the positional highlight detection (SNOW-257 refinement 1)."""

    def test_headline_not_compass_marker(self, parsed_chablais: dict[str, Any]) -> None:
        """Headline must not be a bare compass marker (N, S, O, E)."""
        hl = parsed_chablais["properties"]["highlights"]
        assert hl not in ("N", "S", "O", "E", "O E", "N S")

    def test_headline_not_pure_digits(self, parsed_chablais: dict[str, Any]) -> None:
        """Headline must not be a bare danger-level number."""
        hl = parsed_chablais["properties"]["highlights"]
        assert not hl.strip().isdigit()

    def test_headline_contains_manteau(self, parsed_chablais: dict[str, Any]) -> None:
        """CHABLAIS headline contains 'MANTEAU' (all-caps style confirmed)."""
        assert "MANTEAU" in parsed_chablais["properties"]["highlights"].upper()
