# test_to_caaml_mont_blanc.py — End-to-end parse tests for the MONT-BLANC fixture.
"""End-to-end parse tests against the committed MONT-BLANC BRA PDF fixture.

MONT-BLANC is the canonical split-elevation test case: danger=2 (moderate)
above 3600m and danger=1 (low) below.  It also exercises two SAT labels
(neige ventée + neige humide) → two avalanche problems.
"""

from typing import Any


class TestMontBlancDangerRatings:
    """Verify split-elevation danger rating extraction."""

    def test_returns_two_ratings(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC has split elevation — two dangerRating entries."""
        result = parsed_mont_blanc
        ratings = result["properties"]["dangerRatings"]
        assert len(ratings) == 2

    def test_upper_band_is_moderate(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Above 3600m: danger = 2 (limité / moderate)."""
        result = parsed_mont_blanc
        ratings = result["properties"]["dangerRatings"]
        upper = next(r for r in ratings if r["elevation"]["lowerBound"] == 3600)
        assert upper["mainValue"] == "moderate"

    def test_lower_band_is_low(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Below 3600m: danger = 1 (faible / low)."""
        result = parsed_mont_blanc
        ratings = result["properties"]["dangerRatings"]
        lower = next(r for r in ratings if r["elevation"]["upperBound"] == 3600)
        assert lower["mainValue"] == "low"

    def test_upper_band_elevation_bounds(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Upper-band rating: lowerBound=3600, upperBound=None."""
        result = parsed_mont_blanc
        ratings = result["properties"]["dangerRatings"]
        upper = next(r for r in ratings if r["elevation"]["lowerBound"] == 3600)
        assert upper["elevation"]["upperBound"] is None

    def test_lower_band_elevation_bounds(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Lower-band rating: lowerBound=None, upperBound=3600."""
        result = parsed_mont_blanc
        ratings = result["properties"]["dangerRatings"]
        lower = next(r for r in ratings if r["elevation"]["upperBound"] == 3600)
        assert lower["elevation"]["lowerBound"] is None


class TestMontBlancHighlights:
    """Verify headline extraction for a wind-slab bulletin."""

    def test_headline_present(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Highlights must be non-empty."""
        result = parsed_mont_blanc
        assert result["properties"]["highlights"] != ""

    def test_headline_mentions_plaques(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC 2026-05-21 headline references wind-slab (plaques)."""
        result = parsed_mont_blanc
        assert "PLAQUES" in result["properties"]["highlights"].upper()


class TestMontBlancAvalancheProblems:
    """Verify two-problem extraction."""

    def test_two_problems(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC has two SAT labels — two avalanche problems."""
        result = parsed_mont_blanc
        assert len(result["properties"]["avalancheProblems"]) == 2

    def test_first_problem_is_wind_slab(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """First SAT 'neige ventée' → problemType 'wind_slab'."""
        result = parsed_mont_blanc
        problems = result["properties"]["avalancheProblems"]
        first = next(p for p in problems if p["order"] == 1)
        assert first["problemType"] == "wind_slab"

    def test_second_problem_is_wet_snow(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Second SAT 'neige humide' → problemType 'wet_snow'."""
        result = parsed_mont_blanc
        problems = result["properties"]["avalancheProblems"]
        second = next(p for p in problems if p["order"] == 2)
        assert second["problemType"] == "wet_snow"

    def test_sat_labels_preserved(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Both raw SAT labels must appear in customData."""
        result = parsed_mont_blanc
        sat = result["properties"]["customData"]["MF"]["typicalAvalancheSituations"]
        assert "neige ventée" in sat
        assert "neige humide" in sat


class TestMontBlancWeather:
    """Verify weather extraction for MONT-BLANC."""

    def test_isotherm_four_slots(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Isotherm must produce 4 altitude values."""
        result = parsed_mont_blanc
        assert len(result["properties"]["weather"]["isotherm0"]) == 4

    def test_isotherm_values(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC: isotherm 4000/4000/4000/4100 m."""
        result = parsed_mont_blanc
        assert result["properties"]["weather"]["isotherm0"] == [4000, 4000, 4000, 4100]

    def test_wind_table_two_rows(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC wind table: two altitude rows."""
        result = parsed_mont_blanc
        assert len(result["properties"]["weather"]["wind"]) == 2

    def test_wind_row_2000m(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """First wind row at 2000m."""
        result = parsed_mont_blanc
        row = result["properties"]["weather"]["wind"][0]
        assert row["altitude"] == "2000m"
        assert len(row["speeds"]) == 4

    def test_wind_row_4000m(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Second wind row at 4000m (higher altitude than CHABLAIS)."""
        result = parsed_mont_blanc
        row = result["properties"]["weather"]["wind"][1]
        assert row["altitude"] == "4000m"
        assert row["speeds"] == [25, 25, 25, 20]


class TestMontBlancCommentHtml:
    """Verify that prose comment fields are emitted as HTML (not plain-text)."""

    def test_snowpack_comment_contains_html(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """snowpackStructure.comment must contain at least one HTML tag."""
        comment = parsed_mont_blanc["properties"]["snowpackStructure"]["comment"]
        assert "<" in comment

    def test_avalanche_activity_comment_contains_html(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """avalancheActivity.comment must contain at least one HTML tag."""
        comment = parsed_mont_blanc["properties"]["avalancheActivity"]["comment"]
        assert "<" in comment

    def test_tendency_comment_contains_html(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """tendency[0].comment must contain at least one HTML tag."""
        comment = parsed_mont_blanc["properties"]["tendency"][0]["comment"]
        assert "<" in comment


class TestMontBlancTendency:
    """Verify tendency extraction for MONT-BLANC."""

    def test_tendency_present(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Tendency list must be non-empty."""
        result = parsed_mont_blanc
        assert len(result["properties"]["tendency"]) == 1

    def test_tendency_danger_rating(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Next-day danger: 'moderate' (limité)."""
        result = parsed_mont_blanc
        assert result["properties"]["tendency"][0]["dangerRating"] == "moderate"

    def test_tendency_type_is_decreasing(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Tendency type: 'decreasing' (en baisse)."""
        result = parsed_mont_blanc
        assert result["properties"]["tendency"][0]["tendencyType"] == "decreasing"


class TestMontBlancCustomData:
    """Verify the customData.MF block for MONT-BLANC."""

    def test_massif_slug(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Massif slug must be 'MONT-BLANC'."""
        result = parsed_mont_blanc
        assert result["properties"]["customData"]["MF"]["massif"] == "MONT-BLANC"

    def test_source_file(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Source file must be the original PDF filename."""
        result = parsed_mont_blanc
        src = result["properties"]["customData"]["MF"]["source_file"]
        assert src == "BRA.MONT-BLANC.20260521140702.pdf"

    def test_region_id(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Region ID must be 'FR-MONT-BLANC'."""
        result = parsed_mont_blanc
        regions = result["properties"]["regions"]
        assert regions[0]["regionID"] == "FR-MONT-BLANC"
