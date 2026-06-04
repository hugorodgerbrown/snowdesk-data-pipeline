# test_fr_prose.py — Unit tests for _fr_prose.py grammar helpers.
#
# Covers each pattern family with at least one positive match and one
# no-match counter-example per family.
"""Unit tests for the French-prose extraction helpers in _fr_prose.py."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parents[3] / "scripts" / "meteofrance-archive"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _fr_prose import (  # noqa: E402 — inserted after sys.path manipulation
    extract_aspects,
    extract_avalanche_size,
    extract_elevation,
    extract_frequency,
)

# ---------------------------------------------------------------------------
# extract_aspects
# ---------------------------------------------------------------------------


class TestExtractAspectsAbbreviations:
    """Abbreviated compass tokens (NE, SO, NO, …) are recognised."""

    def test_ne_abbreviation(self) -> None:
        """Single 'NE' token → ['NE']."""
        assert "NE" in extract_aspects("Risque NE dominant")

    def test_so_maps_to_sw(self) -> None:
        """French 'SO' (sud-ouest) → CAAML 'SW'."""
        assert "SW" in extract_aspects("versants SO et O")

    def test_no_maps_to_nw(self) -> None:
        """French 'NO' (nord-ouest) → CAAML 'NW'."""
        assert "NW" in extract_aspects("versants NO exposés")

    def test_o_maps_to_w(self) -> None:
        """French 'O' (ouest) → CAAML 'W'."""
        assert "W" in extract_aspects("versant O uniquement")

    def test_multiple_abbreviations(self) -> None:
        """Multiple abbreviations in one phrase are all captured."""
        aspects = extract_aspects("N NE E SE S SO O NO")
        assert set(aspects) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

    def test_ne_not_matched_inside_neige(self) -> None:
        """'NE' inside the word 'NEIGE' must not produce a false positive."""
        # 'NEIGE' contains the letters N-E but as a substring, not a token.
        result = extract_aspects("NEIGE COMPACTE SANS VENT")
        # No aspect abbreviation present; grouping words absent.
        assert result == []

    def test_empty_string_returns_empty(self) -> None:
        """Empty input → empty list."""
        assert extract_aspects("") == []


class TestExtractAspectsGroupingWords:
    """'ubacs' and 'adrets' groupings are resolved correctly."""

    def test_ubacs_yields_n_ne_nw(self) -> None:
        """'ubacs' → ['N', 'NE', 'NW']."""
        aspects = extract_aspects("En ubacs au-dessus de 2000 m")
        assert "N" in aspects
        assert "NE" in aspects
        assert "NW" in aspects
        # Adret-side aspects must not appear.
        assert "S" not in aspects
        assert "SE" not in aspects
        assert "SW" not in aspects

    def test_adrets_yields_s_se_sw(self) -> None:
        """'adrets' → ['S', 'SE', 'SW']."""
        aspects = extract_aspects("également possibles dans les adrets")
        assert "S" in aspects
        assert "SE" in aspects
        assert "SW" in aspects
        # Ubac-side aspects must not appear.
        assert "N" not in aspects
        assert "NE" not in aspects
        assert "NW" not in aspects

    def test_no_grouping_word_no_aspects_from_this_path(self) -> None:
        """Prose without grouping words doesn't produce aspects via this route."""
        aspects = extract_aspects("Risque de plaque dans les couloirs")
        # No ubacs / adrets / secteur — expect no aspects unless abbreviations present.
        assert "N" not in aspects
        assert "S" not in aspects


class TestExtractAspectsSecteur:
    """'secteur nord' / 'secteur sud' qualifiers are resolved."""

    def test_secteur_nord(self) -> None:
        """'secteur nord' → contains 'N'."""
        assert "N" in extract_aspects("dans un large secteur nord")

    def test_secteur_sud_ouest(self) -> None:
        """'secteur sud-ouest' → contains 'SW'."""
        assert "SW" in extract_aspects("secteur sud-ouest exposé")

    def test_secteur_word_required(self) -> None:
        """'nord' alone (without 'secteur') does not trigger this sub-path."""
        # 'nord' as a full-word triggers the full-word branch, not the secteur branch;
        # the net result should still include 'N' via full-word matching.
        aspects = extract_aspects("versant nord bien enneigé")
        assert "N" in aspects


class TestExtractAspectsFullWords:
    """Full French direction words are recognised."""

    def test_nord(self) -> None:
        """'nord' → 'N'."""
        assert "N" in extract_aspects("versant nord")

    def test_sud(self) -> None:
        """'sud' → 'S'."""
        assert "S" in extract_aspects("versant sud")

    def test_ouest(self) -> None:
        """'ouest' (lowercase) → 'W'."""
        assert "W" in extract_aspects("côté ouest du massif")

    def test_nord_est(self) -> None:
        """'nord-est' → 'NE'."""
        assert "NE" in extract_aspects("versant nord-est")

    def test_est_verb_not_matched(self) -> None:
        """'est' as verb ('la neige est humide') must NOT produce 'E'."""
        result = extract_aspects("la neige est humide et compacte")
        assert "E" not in result

    def test_est_direction_not_matched(self) -> None:
        """Isolated 'est' is never matched as compass — too ambiguous in prose."""
        result = extract_aspects("versant est")
        # By design 'est' is excluded from full-word matching.
        assert "E" not in result


class TestExtractAspectsAllOrientations:
    """'toutes orientations' / 'toutes expositions' → all 8 points."""

    def test_toutes_orientations(self) -> None:
        """'toutes orientations' → all 8 aspects."""
        aspects = extract_aspects("Risque sur toutes orientations")
        assert set(aspects) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

    def test_toutes_expositions(self) -> None:
        """'toutes expositions' → all 8 aspects."""
        aspects = extract_aspects("départs sur toutes expositions")
        assert set(aspects) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

    def test_result_is_canonical_order(self) -> None:
        """All-aspects result must be in canonical clockwise order."""
        aspects = extract_aspects("toutes orientations")
        assert aspects == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


# ---------------------------------------------------------------------------
# extract_elevation
# ---------------------------------------------------------------------------


class TestExtractElevationAbove:
    """'Au-dessus de Nm' → lowerBound."""

    def test_au_dessus(self) -> None:
        """'Au-dessus de 2500 m' → lowerBound=2500."""
        result = extract_elevation("En ubacs au-dessus de 2500 m")
        assert result == {"lowerBound": 2500}

    def test_au_dessus_case_insensitive(self) -> None:
        """Lowercase 'au-dessus' is also matched."""
        result = extract_elevation("au-dessus de 1800 m risque accru")
        assert result is not None
        assert result["lowerBound"] == 1800

    def test_no_elevation_phrase(self) -> None:
        """Prose without elevation phrase → None."""
        result = extract_elevation("Risque de plaque de vent")
        assert result is None


class TestExtractElevationBelow:
    """'En dessous de Nm' → upperBound."""

    def test_en_dessous(self) -> None:
        """'En dessous de 1500 m' → upperBound=1500."""
        result = extract_elevation("En dessous de 1500 m conditions douces")
        assert result == {"upperBound": 1500}

    def test_no_match_returns_none(self) -> None:
        """Prose without 'en dessous' phrase → None."""
        result = extract_elevation("manteau neigeux instable")
        assert result is None


class TestExtractElevationBetween:
    """'Entre Nm et Nm' → both bounds."""

    def test_entre_2700_3600(self) -> None:
        """'Entre 2700 m et 3600 m' → lowerBound=2700, upperBound=3600."""
        result = extract_elevation("Entre 2700 m et 3600 m environ")
        assert result == {"lowerBound": 2700, "upperBound": 3600}

    def test_between_takes_priority_over_above(self) -> None:
        """_BETWEEN has priority over _ABOVE when both phrases appear."""
        text = "Entre 2000 m et 3000 m, parfois au-dessus de 3000 m"
        result = extract_elevation(text)
        assert result == {"lowerBound": 2000, "upperBound": 3000}

    def test_empty_string(self) -> None:
        """Empty string → None."""
        assert extract_elevation("") is None


# ---------------------------------------------------------------------------
# extract_avalanche_size
# ---------------------------------------------------------------------------


class TestExtractAvalancheSizeNumeric:
    """Numeric 'Taille N' is parsed correctly."""

    def test_taille_1(self) -> None:
        """'Taille 1' → '1'."""
        assert extract_avalanche_size("départs ponctuels. Taille 1.") == "1"

    def test_taille_2(self) -> None:
        """'Taille 2' → '2'."""
        assert extract_avalanche_size("quelques plaques. Taille 2") == "2"

    def test_range_takes_higher_bound(self) -> None:
        """'Taille 1 rarement 2' → '2' (higher bound)."""
        assert extract_avalanche_size("Taille 1 rarement 2") == "2"

    def test_range_higher_first(self) -> None:
        """'Taille 3 à 4' → '4' (higher bound)."""
        assert extract_avalanche_size("Taille 3 à 4") == "4"

    def test_no_size_phrase(self) -> None:
        """Prose without a size phrase → None."""
        assert extract_avalanche_size("Manteau neigeux stable sur les versants") is None

    def test_empty_string(self) -> None:
        """Empty string → None."""
        assert extract_avalanche_size("") is None


class TestExtractAvalancheSizeProse:
    """Prose labels (petite, moyenne, grande, …) are mapped to EAWS strings."""

    def test_petite(self) -> None:
        """'petite' → '1'."""
        assert extract_avalanche_size("départs de petite taille") == "1"

    def test_moyenne(self) -> None:
        """'moyenne' → '2'."""
        assert extract_avalanche_size("plaques de taille moyenne") == "2"

    def test_grande(self) -> None:
        """'grande' → '3'."""
        assert extract_avalanche_size("avalanches de grande taille") == "3"

    def test_tres_grande(self) -> None:
        """'très grande' → '4'."""
        assert extract_avalanche_size("risque de très grande avalanche") == "4"

    def test_extreme(self) -> None:
        """'extrême' → '5'."""
        assert extract_avalanche_size("phénomène extrême") == "5"

    def test_petite_a_moyenne_returns_2(self) -> None:
        """'petite à moyenne' → '2' (higher bound per spec)."""
        assert extract_avalanche_size("taille petite à moyenne") == "2"

    def test_moyenne_a_grande_returns_3(self) -> None:
        """'moyenne à grande' → '3'."""
        assert extract_avalanche_size("taille moyenne à grande") == "3"

    def test_grande_a_tres_grande_returns_4(self) -> None:
        """'grande à très grande' → '4'."""
        assert extract_avalanche_size("taille grande à très grande") == "4"

    def test_numeric_takes_priority_over_prose(self) -> None:
        """'Taille 3' wins over any prose label that might also match."""
        # "grande" would normally → "3", but "Taille 3" is the same here.
        assert extract_avalanche_size("Taille 3, de grande envergure") == "3"


# ---------------------------------------------------------------------------
# extract_frequency
# ---------------------------------------------------------------------------


class TestExtractFrequency:
    """Frequency phrases map to EAWS tokens."""

    def test_rares(self) -> None:
        """'rares' → 'few'."""
        assert extract_frequency("départs rares mais violents") == "few"

    def test_isolees(self) -> None:
        """'isolées' → 'few'."""
        assert extract_frequency("départs isolées en altitude") == "few"

    def test_quelques(self) -> None:
        """'quelques' → 'some'."""
        assert extract_frequency("quelques plaques résiduelles") == "some"

    def test_nombreuses(self) -> None:
        """'nombreuses' → 'many'."""
        assert extract_frequency("nombreuses coulées possibles") == "many"

    def test_nombreux(self) -> None:
        """'nombreux' → 'many'."""
        assert extract_frequency("départs nombreux sur les adrets") == "many"

    def test_no_frequency_phrase(self) -> None:
        """Prose without a frequency term → None."""
        assert extract_frequency("manteau neigeux instable") is None

    def test_empty_string(self) -> None:
        """Empty string → None."""
        assert extract_frequency("") is None

    def test_priority_many_over_few(self) -> None:
        """When both 'nombreuses' and 'rares' appear, 'many' wins (listed first)."""
        result = extract_frequency("nombreuses avalanches, rares en altitude")
        assert result == "many"
