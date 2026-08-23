# test_to_caaml_mont_blanc.py — End-to-end parse tests for the MONT-BLANC fixture.
"""End-to-end parse tests against the committed MONT-BLANC BRA PDF fixture.

MONT-BLANC is the canonical split-elevation test case: danger=2 (moderate)
above 3600m and danger=1 (low) below.  It also exercises two SAT labels
(neige ventée + neige humide) → two avalanche problems.
"""

from typing import Any

import pytest

# Pin this module to one xdist worker. The ``parsed_*`` fixtures in
# conftest.py are session-scoped, but an xdist session is *per worker* —
# under the default distribution these tests scatter across all of them
# and each worker re-parses the same PDF (~2s a time). Grouping by PDF
# means one parse per fixture, not one per worker.
pytestmark = pytest.mark.xdist_group(name="mf_pdf_mont_blanc")


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

    def test_aspects_non_empty(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Aspects list must be non-empty for both problems."""
        problems = parsed_mont_blanc["properties"]["avalancheProblems"]
        for p in problems:
            assert isinstance(p["aspects"], list)
            assert len(p["aspects"]) > 0

    def test_aspects_include_ubac_side(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Prose has 'ubacs' → N, NE, NW present in primary problem."""
        aspects = parsed_mont_blanc["properties"]["avalancheProblems"][0]["aspects"]
        assert "N" in aspects
        assert "NE" in aspects
        assert "NW" in aspects

    def test_aspects_include_west(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Triggered prose ends with 'Ouest' → W present in primary problem."""
        aspects = parsed_mont_blanc["properties"]["avalancheProblems"][0]["aspects"]
        assert "W" in aspects

    def test_elevation_populated(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """Elevation dict must be non-empty.

        MONT-BLANC prose has 'Entre 2700 m et 3600 m' (spontaneous, takes
        priority) so the result is lowerBound=2700, upperBound=3600.
        """
        elev = parsed_mont_blanc["properties"]["avalancheProblems"][0]["elevation"]
        assert isinstance(elev, dict)
        assert len(elev) > 0
        assert elev.get("lowerBound") == 2700
        assert elev.get("upperBound") == 3600

    def test_avalanche_size_higher_bound(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """'Taille 1 rarement 2' → avalancheSize == '2' (higher bound)."""
        size = parsed_mont_blanc["properties"]["avalancheProblems"][0].get(
            "avalancheSize"
        )
        assert size == "2"

    def test_both_problems_share_prose_fields(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Both problems receive the same aspects/elevation from shared prose."""
        problems = parsed_mont_blanc["properties"]["avalancheProblems"]
        assert len(problems) == 2
        assert problems[0]["aspects"] == problems[1]["aspects"]
        assert problems[0]["elevation"] == problems[1]["elevation"]


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
        """snowpackStructure.comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_mont_blanc["properties"]["snowpackStructure"]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment

    def test_avalanche_activity_comment_contains_html(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """avalancheActivity.comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_mont_blanc["properties"]["avalancheActivity"]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment

    def test_tendency_comment_contains_html(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """tendency[0].comment must contain structural HTML (h2, p, or li)."""
        comment = parsed_mont_blanc["properties"]["tendency"][0]["comment"]
        assert "<h2>" in comment or "<p>" in comment or "<li>" in comment


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


class TestMontBlancSpontaneousTriggeredSplit:
    """Verify customData.MF carries the spontaneous/triggered split (SNOW-257)."""

    def test_spontaneous_key_present(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.spontaneous must be present."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        assert "spontaneous" in mf

    def test_triggered_key_present(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.triggered must be present."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        assert "triggered" in mf

    def test_spontaneous_non_empty(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.spontaneous must be non-empty for MONT-BLANC."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        assert len(mf["spontaneous"]) > 0

    def test_triggered_non_empty(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.triggered must be non-empty for MONT-BLANC."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        assert len(mf["triggered"]) > 0

    def test_spontaneous_contains_html(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.spontaneous must carry HTML markup."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        html = mf["spontaneous"]
        assert "<h2>" in html or "<p>" in html or "<li>" in html

    def test_triggered_contains_html(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """customData.MF.triggered must carry HTML markup."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        html = mf["triggered"]
        assert "<h2>" in html or "<p>" in html or "<li>" in html

    def test_spontaneous_differs_from_triggered(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """The two split fields must differ — they originate from different sections."""
        mf = parsed_mont_blanc["properties"]["customData"]["MF"]
        assert mf["spontaneous"] != mf["triggered"]

    def test_concatenated_comment_still_present(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """avalancheActivity.comment backward-compat field must remain present."""
        comment = parsed_mont_blanc["properties"]["avalancheActivity"]["comment"]
        assert isinstance(comment, str)
        assert len(comment) > 0


class TestMontBlancHighlightDetection:
    """Verify the positional highlight detection for MONT-BLANC (SNOW-257 refinement 1)."""

    def test_headline_not_compass_marker(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Headline must not be a compass marker like 'N', 'O E', etc."""
        hl = parsed_mont_blanc["properties"]["highlights"]
        assert hl not in ("N", "S", "O", "E", "O E", "N S", "3600m")

    def test_headline_not_danger_number(
        self, parsed_mont_blanc: dict[str, Any]
    ) -> None:
        """Headline must not be a bare danger number."""
        hl = parsed_mont_blanc["properties"]["highlights"]
        assert not hl.strip().isdigit()

    def test_headline_mentions_plaques(self, parsed_mont_blanc: dict[str, Any]) -> None:
        """MONT-BLANC headline mentions wind-slab (PLAQUES)."""
        hl = parsed_mont_blanc["properties"]["highlights"]
        assert "PLAQUES" in hl.upper()
