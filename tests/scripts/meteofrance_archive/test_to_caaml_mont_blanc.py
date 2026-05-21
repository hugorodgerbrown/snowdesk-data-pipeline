# test_to_caaml_mont_blanc.py — End-to-end parse tests for the MONT-BLANC fixture.
"""End-to-end parse tests against the committed MONT-BLANC BRA PDF fixture.

MONT-BLANC is the canonical split-elevation test case: danger=2 (moderate)
above 3600m and danger=1 (low) below.  It also exercises two SAT labels
(neige ventée + neige humide) → two avalanche problems.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import parse_pdf  # noqa: E402


class TestMontBlancDangerRatings:
    """Verify split-elevation danger rating extraction."""

    def test_returns_two_ratings(self, mont_blanc_pdf_path: Path) -> None:
        """MONT-BLANC has split elevation — two dangerRating entries."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        assert len(ratings) == 2

    def test_upper_band_is_moderate(self, mont_blanc_pdf_path: Path) -> None:
        """Above 3600m: danger = 2 (limité / moderate)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        upper = next(r for r in ratings if r["elevation"]["lowerBound"] == 3600)
        assert upper["mainValue"] == "moderate"

    def test_lower_band_is_low(self, mont_blanc_pdf_path: Path) -> None:
        """Below 3600m: danger = 1 (faible / low)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        lower = next(r for r in ratings if r["elevation"]["upperBound"] == 3600)
        assert lower["mainValue"] == "low"

    def test_upper_band_elevation_bounds(self, mont_blanc_pdf_path: Path) -> None:
        """Upper-band rating: lowerBound=3600, upperBound=None."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        upper = next(r for r in ratings if r["elevation"]["lowerBound"] == 3600)
        assert upper["elevation"]["upperBound"] is None

    def test_lower_band_elevation_bounds(self, mont_blanc_pdf_path: Path) -> None:
        """Lower-band rating: lowerBound=None, upperBound=3600."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        lower = next(r for r in ratings if r["elevation"]["upperBound"] == 3600)
        assert lower["elevation"]["lowerBound"] is None


class TestMontBlancHighlights:
    """Verify headline extraction for a wind-slab bulletin."""

    def test_headline_present(self, mont_blanc_pdf_path: Path) -> None:
        """Highlights must be non-empty."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert result["properties"]["highlights"] != ""

    def test_headline_mentions_plaques(self, mont_blanc_pdf_path: Path) -> None:
        """MONT-BLANC 2026-05-21 headline references wind-slab (plaques)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert "PLAQUES" in result["properties"]["highlights"].upper()


class TestMontBlancAvalancheProblems:
    """Verify two-problem extraction."""

    def test_two_problems(self, mont_blanc_pdf_path: Path) -> None:
        """MONT-BLANC has two SAT labels — two avalanche problems."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert len(result["properties"]["avalancheProblems"]) == 2

    def test_first_problem_is_wind_slab(self, mont_blanc_pdf_path: Path) -> None:
        """First SAT 'neige ventée' → problemType 'wind_slab'."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        problems = result["properties"]["avalancheProblems"]
        first = next(p for p in problems if p["order"] == 1)
        assert first["problemType"] == "wind_slab"

    def test_second_problem_is_wet_snow(self, mont_blanc_pdf_path: Path) -> None:
        """Second SAT 'neige humide' → problemType 'wet_snow'."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        problems = result["properties"]["avalancheProblems"]
        second = next(p for p in problems if p["order"] == 2)
        assert second["problemType"] == "wet_snow"

    def test_sat_labels_preserved(self, mont_blanc_pdf_path: Path) -> None:
        """Both raw SAT labels must appear in customData."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        sat = result["properties"]["customData"]["MF"]["typicalAvalancheSituations"]
        assert "neige ventée" in sat
        assert "neige humide" in sat


class TestMontBlancWeather:
    """Verify weather extraction for MONT-BLANC."""

    def test_isotherm_four_slots(self, mont_blanc_pdf_path: Path) -> None:
        """Isotherm must produce 4 altitude values."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert len(result["properties"]["weather"]["isotherm0"]) == 4

    def test_isotherm_values(self, mont_blanc_pdf_path: Path) -> None:
        """MONT-BLANC: isotherm 4000/4000/4000/4100 m."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert result["properties"]["weather"]["isotherm0"] == [4000, 4000, 4000, 4100]

    def test_wind_table_two_rows(self, mont_blanc_pdf_path: Path) -> None:
        """MONT-BLANC wind table: two altitude rows."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert len(result["properties"]["weather"]["wind"]) == 2

    def test_wind_row_2000m(self, mont_blanc_pdf_path: Path) -> None:
        """First wind row at 2000m."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        row = result["properties"]["weather"]["wind"][0]
        assert row["altitude"] == "2000m"
        assert len(row["speeds"]) == 4

    def test_wind_row_4000m(self, mont_blanc_pdf_path: Path) -> None:
        """Second wind row at 4000m (higher altitude than CHABLAIS)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        row = result["properties"]["weather"]["wind"][1]
        assert row["altitude"] == "4000m"
        assert row["speeds"] == [25, 25, 25, 20]


class TestMontBlancTendency:
    """Verify tendency extraction for MONT-BLANC."""

    def test_tendency_present(self, mont_blanc_pdf_path: Path) -> None:
        """Tendency list must be non-empty."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert len(result["properties"]["tendency"]) == 1

    def test_tendency_danger_rating(self, mont_blanc_pdf_path: Path) -> None:
        """Next-day danger: 'moderate' (limité)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert result["properties"]["tendency"][0]["dangerRating"] == "moderate"

    def test_tendency_type_is_decreasing(self, mont_blanc_pdf_path: Path) -> None:
        """Tendency type: 'decreasing' (en baisse)."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert result["properties"]["tendency"][0]["tendencyType"] == "decreasing"


class TestMontBlancCustomData:
    """Verify the customData.MF block for MONT-BLANC."""

    def test_massif_slug(self, mont_blanc_pdf_path: Path) -> None:
        """Massif slug must be 'MONT-BLANC'."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        assert result["properties"]["customData"]["MF"]["massif"] == "MONT-BLANC"

    def test_source_file(self, mont_blanc_pdf_path: Path) -> None:
        """Source file must be the original PDF filename."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        src = result["properties"]["customData"]["MF"]["source_file"]
        assert src == "BRA.MONT-BLANC.20260521140702.pdf"

    def test_region_id(self, mont_blanc_pdf_path: Path) -> None:
        """Region ID must be 'FR-MONT-BLANC'."""
        result = parse_pdf(mont_blanc_pdf_path)
        assert result is not None
        regions = result["properties"]["regions"]
        assert regions[0]["regionID"] == "FR-MONT-BLANC"
