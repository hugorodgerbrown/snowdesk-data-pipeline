"""
tests/pipeline/test_schema.py — Tests for the CAAML schema dataclasses
and TextChoices enums in pipeline/schema.py.
"""

from pipeline.schema import (
    AvalancheProblem,
    AvalancheProblemType,
    DangerRating,
    DangerRatingValue,
    Elevation,
    ValidTimePeriod,
)

# ---------------------------------------------------------------------------
# TextChoices
# ---------------------------------------------------------------------------


class TestDangerRatingValue:
    """Tests for the DangerRatingValue TextChoices enum."""

    def test_contains_all_seven_eaws_values(self):
        """The enum exposes all seven values defined by the CAAML schema."""
        assert set(DangerRatingValue.values) == {
            "low",
            "moderate",
            "considerable",
            "high",
            "very_high",
            "no_snow",
            "no_rating",
        }

    def test_label_includes_numeric_level(self):
        """Numeric levels are surfaced in the human-readable labels."""
        assert "1" in DangerRatingValue.LOW.label
        assert "5" in DangerRatingValue.VERY_HIGH.label


class TestValidTimePeriod:
    """Tests for the ValidTimePeriod TextChoices enum."""

    def test_contains_three_values(self):
        """The enum exposes the three time-of-day qualifiers."""
        assert set(ValidTimePeriod.values) == {"all_day", "earlier", "later"}


class TestAvalancheProblemType:
    """Tests for the AvalancheProblemType TextChoices enum."""

    def test_contains_all_eight_problem_types(self):
        """The enum exposes all eight EAWS problem types."""
        assert set(AvalancheProblemType.values) == {
            "new_snow",
            "wind_slab",
            "persistent_weak_layers",
            "wet_snow",
            "gliding_snow",
            "cornices",
            "no_distinct_avalanche_problem",
            "favourable_situation",
        }


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------


class TestElevation:
    """Tests for the Elevation dataclass."""

    def test_from_dict_returns_none_for_empty(self):
        """An empty or missing dict yields None."""
        assert Elevation.from_dict(None) is None
        assert Elevation.from_dict({}) is None

    def test_from_dict_lower_bound_only(self):
        """A dict with only lowerBound populates lower_bound."""
        elevation = Elevation.from_dict({"lowerBound": "1800"})
        assert elevation == Elevation(lower_bound="1800", upper_bound=None)

    def test_from_dict_upper_bound_only(self):
        """A dict with only upperBound populates upper_bound."""
        elevation = Elevation.from_dict({"upperBound": "2400"})
        assert elevation == Elevation(lower_bound=None, upper_bound="2400")

    def test_from_dict_band(self):
        """A dict with both bounds defines an elevation band."""
        elevation = Elevation.from_dict(
            {"lowerBound": "1800", "upperBound": "2400"},
        )
        assert elevation == Elevation(lower_bound="1800", upper_bound="2400")

    def test_treeline_is_preserved(self):
        """The literal 'treeline' value is preserved as a string."""
        elevation = Elevation.from_dict({"lowerBound": "treeline"})
        assert elevation.lower_bound == "treeline"


# ---------------------------------------------------------------------------
# DangerRating
# ---------------------------------------------------------------------------


class TestDangerRating:
    """Tests for the DangerRating dataclass."""

    def test_from_dict_minimal(self):
        """Only mainValue is required; other fields default appropriately."""
        rating = DangerRating.from_dict({"mainValue": "low"})
        assert rating.main_value == "low"
        assert rating.valid_time_period is None
        assert rating.elevation is None
        assert rating.aspects == ()

    def test_from_dict_with_all_fields(self):
        """All optional fields are mapped from camelCase to snake_case."""
        rating = DangerRating.from_dict(
            {
                "mainValue": "considerable",
                "validTimePeriod": "later",
                "elevation": {"lowerBound": "2000"},
                "aspects": ["N", "NE"],
            }
        )
        assert rating.main_value == "considerable"
        assert rating.valid_time_period == "later"
        assert rating.elevation == Elevation(lower_bound="2000")
        assert rating.aspects == ("N", "NE")

    def test_aspects_are_immutable_tuple(self):
        """aspects is stored as a tuple so the dataclass remains hashable."""
        rating = DangerRating.from_dict(
            {"mainValue": "low", "aspects": ["N", "S"]},
        )
        assert isinstance(rating.aspects, tuple)


# ---------------------------------------------------------------------------
# AvalancheProblem
# ---------------------------------------------------------------------------


class TestAvalancheProblem:
    """Tests for the AvalancheProblem dataclass."""

    def test_from_dict_minimal(self):
        """Only problemType is required."""
        problem = AvalancheProblem.from_dict({"problemType": "wet_snow"})
        assert problem.problem_type == "wet_snow"
        assert problem.comment is None
        assert problem.danger_rating_value is None
        assert problem.valid_time_period is None
        assert problem.elevation is None
        assert problem.aspects == ()
        assert problem.avalanche_size is None
        assert problem.snowpack_stability is None
        assert problem.frequency is None

    def test_from_dict_with_all_fields(self):
        """All optional fields are mapped from camelCase to snake_case."""
        problem = AvalancheProblem.from_dict(
            {
                "problemType": "wind_slab",
                "comment": "Fresh wind slabs above 2200m.",
                "dangerRatingValue": "considerable",
                "validTimePeriod": "all_day",
                "elevation": {"lowerBound": "2200"},
                "aspects": ["N", "NW"],
                "avalancheSize": 2,
                "snowpackStability": "poor",
                "frequency": "some",
            }
        )
        assert problem.problem_type == "wind_slab"
        assert problem.comment == "Fresh wind slabs above 2200m."
        assert problem.danger_rating_value == "considerable"
        assert problem.valid_time_period == "all_day"
        assert problem.elevation == Elevation(lower_bound="2200")
        assert problem.aspects == ("N", "NW")
        assert problem.avalanche_size == 2
        assert problem.snowpack_stability == "poor"
        assert problem.frequency == "some"
