# test_to_caaml_chablais.py — End-to-end parse tests for the CHABLAIS fixture.
"""End-to-end parse tests against the committed CHABLAIS BRA PDF fixture."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import parse_pdf  # noqa: E402


class TestChablaisdangerRatings:
    """Verify danger rating extraction for a single-level bulletin."""

    def test_returns_one_rating(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS is a single-level bulletin — one dangerRating entry."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        ratings = result["properties"]["dangerRatings"]
        assert isinstance(ratings, list)
        assert len(ratings) == 1

    def test_main_value_is_low(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS 2026-05-21: danger = 1 (faible / low)."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        rating = result["properties"]["dangerRatings"][0]
        assert rating["mainValue"] == "low"

    def test_elevation_bounds_are_none(self, chablais_pdf_path: Path) -> None:
        """Single-level rating should have no elevation bounds."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        elev = result["properties"]["dangerRatings"][0]["elevation"]
        assert elev["lowerBound"] is None
        assert elev["upperBound"] is None


class TestChablaishighlights:
    """Verify headline extraction."""

    def test_headline_is_present(self, chablais_pdf_path: Path) -> None:
        """Highlights should be extracted."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["highlights"] != ""

    def test_headline_contains_manteau(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS 2026-05-21 headline: 'MANTEAU NEIGEUX STABLE.'"""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert "MANTEAU" in result["properties"]["highlights"]


class TestChablaisAvalancheProblems:
    """Verify avalanche problem extraction."""

    def test_one_problem(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS has one SAT label — one avalanche problem."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        problems = result["properties"]["avalancheProblems"]
        assert len(problems) == 1

    def test_problem_type_is_wet_snow(self, chablais_pdf_path: Path) -> None:
        """SAT 'neige humide' → problemType 'wet_snow'."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["avalancheProblems"][0]["problemType"] == "wet_snow"

    def test_sat_label_preserved(self, chablais_pdf_path: Path) -> None:
        """The raw SAT label should be stored in the satLabel field."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert (
            result["properties"]["avalancheProblems"][0]["satLabel"] == "neige humide"
        )


class TestChablaisWeather:
    """Verify weather extraction."""

    def test_weather_comment_present(self, chablais_pdf_path: Path) -> None:
        """Weather comment should be non-empty."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["weather"]["comment"] != ""

    def test_isotherm_four_slots(self, chablais_pdf_path: Path) -> None:
        """Isotherm should produce 4 altitude values."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        iso = result["properties"]["weather"]["isotherm0"]
        assert len(iso) == 4

    def test_isotherm_values(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS: isotherm 4000/4000/4000/4100 m."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["weather"]["isotherm0"] == [4000, 4000, 4000, 4100]

    def test_wind_table_two_rows(self, chablais_pdf_path: Path) -> None:
        """CHABLAIS wind table: two altitude rows."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        wind = result["properties"]["weather"]["wind"]
        assert len(wind) == 2

    def test_wind_row_2000m(self, chablais_pdf_path: Path) -> None:
        """First wind row at 2000m with speeds [5, 0, 0, 5] km/h."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        row = result["properties"]["weather"]["wind"][0]
        assert row["altitude"] == "2000m"
        assert row["speeds"] == [5, 0, 0, 5]

    def test_wind_row_2500m(self, chablais_pdf_path: Path) -> None:
        """Second wind row at 2500m with speeds [10, 5, 5, 10] km/h."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        row = result["properties"]["weather"]["wind"][1]
        assert row["altitude"] == "2500m"
        assert row["speeds"] == [10, 5, 5, 10]


class TestChablaisTendency:
    """Verify tendency extraction."""

    def test_tendency_present(self, chablais_pdf_path: Path) -> None:
        """Tendency list should be non-empty."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert len(result["properties"]["tendency"]) == 1

    def test_tendency_danger_rating(self, chablais_pdf_path: Path) -> None:
        """Tendency danger: 'low' (faible)."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["tendency"][0]["dangerRating"] == "low"

    def test_tendency_type(self, chablais_pdf_path: Path) -> None:
        """Tendency type: 'steady' (stationnaire)."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["tendency"][0]["tendencyType"] == "steady"


class TestChablaisEnvelopeShape:
    """Verify the outer GeoJSON Feature envelope shape."""

    def test_feature_type(self, chablais_pdf_path: Path) -> None:
        """Root object must be GeoJSON Feature."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["type"] == "Feature"

    def test_geometry_is_none(self, chablais_pdf_path: Path) -> None:
        """Geometry must be None (no spatial footprint in BRA data)."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["geometry"] is None

    def test_region_id(self, chablais_pdf_path: Path) -> None:
        """Region ID must follow FR-{MASSIF} format."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        regions = result["properties"]["regions"]
        assert len(regions) >= 1
        assert regions[0]["regionID"] == "FR-CHABLAIS"

    def test_valid_time_present(self, chablais_pdf_path: Path) -> None:
        """validTime block must be present with startTime and endTime."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        vt = result["properties"]["validTime"]
        assert "startTime" in vt
        assert "endTime" in vt

    def test_lang_is_fr(self, chablais_pdf_path: Path) -> None:
        """Language must be 'fr'."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["lang"] == "fr"


class TestChablaiscustomData:
    """Verify the customData.MF block."""

    def test_massif_slug(self, chablais_pdf_path: Path) -> None:
        """customData.MF.massif must be 'CHABLAIS'."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["customData"]["MF"]["massif"] == "CHABLAIS"

    def test_date(self, chablais_pdf_path: Path) -> None:
        """customData.MF.date must be '2026-05-22' (bulletin validity date)."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        assert result["properties"]["customData"]["MF"]["date"] == "2026-05-22"

    def test_sat_labels_in_custom_data(self, chablais_pdf_path: Path) -> None:
        """customData.MF.typicalAvalancheSituations must list the SAT labels."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        sat = result["properties"]["customData"]["MF"]["typicalAvalancheSituations"]
        assert "neige humide" in sat

    def test_source_file(self, chablais_pdf_path: Path) -> None:
        """customData.MF.source_file must be the PDF filename."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        src = result["properties"]["customData"]["MF"]["source_file"]
        assert src == "BRA.CHABLAIS.20260521140706.pdf"
