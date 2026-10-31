"""
tests/bulletins/test_schema.py — Tests for the CAAML schema dataclasses
and TextChoices enums in apps/bulletins/schema.py.
"""

from apps.bulletins.schema import (
    DangerRatingValue,
    Elevation,
    ValidTimePeriod,
)

# ---------------------------------------------------------------------------
# TextChoices
# ---------------------------------------------------------------------------


class TestDangerRatingValue:
    """Tests for the DangerRatingValue TextChoices enum."""

    def test_contains_all_seven_eaws_values(self) -> None:
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

    def test_label_includes_numeric_level(self) -> None:
        """Numeric levels are surfaced in the human-readable labels."""
        assert "1" in DangerRatingValue.LOW.label
        assert "5" in DangerRatingValue.VERY_HIGH.label


class TestValidTimePeriod:
    """Tests for the ValidTimePeriod TextChoices enum."""

    def test_contains_three_values(self) -> None:
        """The enum exposes the three time-of-day qualifiers."""
        assert set(ValidTimePeriod.values) == {"all_day", "earlier", "later"}


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------


class TestElevation:
    """Tests for the Elevation dataclass."""

    def test_from_dict_returns_none_for_empty(self) -> None:
        """An empty or missing dict yields None."""
        assert Elevation.from_dict(None) is None
        assert Elevation.from_dict({}) is None

    def test_from_dict_lower_bound_only(self) -> None:
        """A dict with only lowerBound populates lower_bound."""
        elevation = Elevation.from_dict({"lowerBound": "1800"})
        assert elevation == Elevation(lower_bound="1800", upper_bound=None)

    def test_from_dict_upper_bound_only(self) -> None:
        """A dict with only upperBound populates upper_bound."""
        elevation = Elevation.from_dict({"upperBound": "2400"})
        assert elevation == Elevation(lower_bound=None, upper_bound="2400")

    def test_from_dict_band(self) -> None:
        """A dict with both bounds defines an elevation band."""
        elevation = Elevation.from_dict(
            {"lowerBound": "1800", "upperBound": "2400"},
        )
        assert elevation == Elevation(lower_bound="1800", upper_bound="2400")

    def test_treeline_is_preserved(self) -> None:
        """The literal 'treeline' value is preserved as a string."""
        elevation = Elevation.from_dict({"lowerBound": "treeline"})
        assert elevation is not None
        assert elevation.lower_bound == "treeline"
