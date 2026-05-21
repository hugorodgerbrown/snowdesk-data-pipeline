# test_sat_mapping.py — Tests for the SAT→CAAML problem-type mapping.
"""Tests for the SAT label to CAAML problem-type mapping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _sat_mapping import sat_to_problem_type  # noqa: E402


class TestSatToProblemType:
    """Tests for sat_to_problem_type()."""

    def test_neige_humide_maps_to_wet_snow(self) -> None:
        """'neige humide' should map to wet_snow."""
        assert sat_to_problem_type("neige humide") == "wet_snow"

    def test_neige_ventee_maps_to_wind_slab(self) -> None:
        """'neige ventée' should map to wind_slab."""
        assert sat_to_problem_type("neige ventée") == "wind_slab"

    def test_plaques_a_vent_maps_to_wind_slab(self) -> None:
        """'plaques à vent' should map to wind_slab."""
        assert sat_to_problem_type("plaques à vent") == "wind_slab"

    def test_neige_fraiche_maps_to_new_snow(self) -> None:
        """'neige fraîche' should map to new_snow."""
        assert sat_to_problem_type("neige fraîche") == "new_snow"

    def test_couche_fragile_maps_to_persistent(self) -> None:
        """'couche fragile persistante' should map to persistent_weak_layers."""
        assert (
            sat_to_problem_type("couche fragile persistante")
            == "persistent_weak_layers"
        )

    def test_case_insensitive(self) -> None:
        """Mapping should be case-insensitive."""
        assert sat_to_problem_type("Neige Humide") == "wet_snow"

    def test_strips_whitespace(self) -> None:
        """Leading/trailing whitespace should be stripped before lookup."""
        assert sat_to_problem_type("  neige humide  ") == "wet_snow"

    def test_unknown_label_returns_none(self) -> None:
        """Unknown SAT labels should return None rather than raising."""
        assert sat_to_problem_type("situation inconnue") is None

    def test_glissement_maps_to_gliding_snow(self) -> None:
        """'glissement' should map to gliding_snow."""
        assert sat_to_problem_type("glissement") == "gliding_snow"
