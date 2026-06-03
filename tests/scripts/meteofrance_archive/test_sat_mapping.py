# test_sat_mapping.py — Tests for the SAT→CAAML problem-type mapping.
"""Tests for the SAT label to CAAML problem-type mapping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _sat_mapping import SAT_TO_PROBLEM_TYPE, sat_to_problem_type  # noqa: E402


class TestSatToProblemType:
    """Tests for sat_to_problem_type()."""

    # ── New snow ──────────────────────────────────────────────────────────

    def test_neige_fraiche_maps_to_new_snow(self) -> None:
        """'neige fraîche' should map to new_snow."""
        assert sat_to_problem_type("neige fraîche") == "new_snow"

    def test_neige_fraiche_accent_free_maps_to_new_snow(self) -> None:
        """'neige fraiche' (accent-free) should map to new_snow."""
        assert sat_to_problem_type("neige fraiche") == "new_snow"

    def test_neige_recente_maps_to_new_snow(self) -> None:
        """'neige récente' (older MF synonym for fresh snow) should map to new_snow."""
        assert sat_to_problem_type("neige récente") == "new_snow"

    def test_neige_recente_accent_free_maps_to_new_snow(self) -> None:
        """'neige recente' (accent-free) should map to new_snow."""
        assert sat_to_problem_type("neige recente") == "new_snow"

    # ── Wind slab ─────────────────────────────────────────────────────────

    def test_neige_ventee_maps_to_wind_slab(self) -> None:
        """'neige ventée' should map to wind_slab."""
        assert sat_to_problem_type("neige ventée") == "wind_slab"

    def test_neige_ventee_accent_free_maps_to_wind_slab(self) -> None:
        """'neige ventee' (accent-free) should map to wind_slab."""
        assert sat_to_problem_type("neige ventee") == "wind_slab"

    def test_plaques_a_vent_maps_to_wind_slab(self) -> None:
        """'plaques à vent' should map to wind_slab."""
        assert sat_to_problem_type("plaques à vent") == "wind_slab"

    def test_plaques_a_vent_accent_free_maps_to_wind_slab(self) -> None:
        """'plaques a vent' (accent-free) should map to wind_slab."""
        assert sat_to_problem_type("plaques a vent") == "wind_slab"

    # ── Persistent weak layers ────────────────────────────────────────────

    def test_couche_fragile_persistante_maps_to_persistent(self) -> None:
        """'couche fragile persistante' should map to persistent_weak_layers."""
        assert (
            sat_to_problem_type("couche fragile persistante")
            == "persistent_weak_layers"
        )

    def test_couche_fragile_enfouie_maps_to_persistent(self) -> None:
        """'couche fragile enfouie' (buried weak layer) should map to persistent_weak_layers."""
        assert sat_to_problem_type("couche fragile enfouie") == "persistent_weak_layers"

    def test_couche_fragile_short_maps_to_persistent(self) -> None:
        """'couche fragile' (truncated MF label) should map to persistent_weak_layers."""
        assert sat_to_problem_type("couche fragile") == "persistent_weak_layers"

    # ── Wet snow ──────────────────────────────────────────────────────────

    def test_neige_humide_maps_to_wet_snow(self) -> None:
        """'neige humide' should map to wet_snow."""
        assert sat_to_problem_type("neige humide") == "wet_snow"

    def test_fonte_maps_to_wet_snow(self) -> None:
        """'fonte' (snowmelt) should map to wet_snow."""
        assert sat_to_problem_type("fonte") == "wet_snow"

    def test_redoux_maps_to_wet_snow(self) -> None:
        """'redoux' (thaw/warming) should map to wet_snow."""
        assert sat_to_problem_type("redoux") == "wet_snow"

    # ── Gliding snow ──────────────────────────────────────────────────────

    def test_glissement_maps_to_gliding_snow(self) -> None:
        """'glissement' should map to gliding_snow."""
        assert sat_to_problem_type("glissement") == "gliding_snow"

    # ── Cornices ──────────────────────────────────────────────────────────

    def test_corniches_maps_to_cornices(self) -> None:
        """'corniches' should map to cornices."""
        assert sat_to_problem_type("corniches") == "cornices"

    # ── No distinct problem ───────────────────────────────────────────────

    def test_pas_de_situation_avalancheuse_typique_maps_to_no_distinct(self) -> None:
        """'pas de situation avalancheuse typique' should map to no_distinct_avalanche_problem."""
        assert (
            sat_to_problem_type("pas de situation avalancheuse typique")
            == "no_distinct_avalanche_problem"
        )

    def test_pas_de_situation_predominante_maps_to_no_distinct(self) -> None:
        """'pas de situation prédominante' (MF shortened form) should map to no_distinct."""
        assert (
            sat_to_problem_type("pas de situation prédominante")
            == "no_distinct_avalanche_problem"
        )

    def test_aucune_situation_typique_predominante_maps_to_no_distinct(self) -> None:
        """EAWS FR canonical form should map to no_distinct_avalanche_problem."""
        assert (
            sat_to_problem_type("aucune situation avalancheuse typique prédominante")
            == "no_distinct_avalanche_problem"
        )

    def test_sans_objet_maps_to_no_distinct(self) -> None:
        """'sans objet' (not applicable, older BRA PDFs) should map to no_distinct."""
        assert sat_to_problem_type("sans objet") == "no_distinct_avalanche_problem"

    # ── Favourable situation ──────────────────────────────────────────────

    def test_situation_favorable_maps_to_favourable_situation(self) -> None:
        """'situation favorable' should map to favourable_situation."""
        assert sat_to_problem_type("situation favorable") == "favourable_situation"

    # ── Removed entry: risque faible ─────────────────────────────────────

    def test_risque_faible_not_in_mapping(self) -> None:
        """'risque faible' is a danger-level label, not a SAT; must not map to anything."""
        assert sat_to_problem_type("risque faible") is None
        assert "risque faible" not in SAT_TO_PROBLEM_TYPE

    # ── Normalisation ─────────────────────────────────────────────────────

    def test_case_insensitive(self) -> None:
        """Mapping should be case-insensitive."""
        assert sat_to_problem_type("Neige Humide") == "wet_snow"

    def test_strips_whitespace(self) -> None:
        """Leading/trailing whitespace should be stripped before lookup."""
        assert sat_to_problem_type("  neige humide  ") == "wet_snow"

    def test_unknown_label_returns_none(self) -> None:
        """Unknown SAT labels should return None rather than raising."""
        assert sat_to_problem_type("situation inconnue") is None
