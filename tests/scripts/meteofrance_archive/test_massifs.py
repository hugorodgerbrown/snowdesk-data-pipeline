# test_massifs.py — Tests for _massifs.py canonical name list and slugify helper.
"""Tests for the canonical massif list and slugify helper."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _massifs import ALL_MASSIFS, ALPINE_MASSIFS, slugify  # noqa: E402


class TestAlpineMassifs:
    """Tests for the ALPINE_MASSIFS constant."""

    def test_count_is_23(self) -> None:
        """There should be exactly 23 Alpine massifs."""
        assert len(ALPINE_MASSIFS) == 23

    def test_beaufortain_spelling(self) -> None:
        """BEAUFORTAIN must be present without an accent (canonical API spelling)."""
        assert "BEAUFORTAIN" in ALPINE_MASSIFS

    def test_chablais_present(self) -> None:
        """CHABLAIS must be in the Alpine list."""
        assert "CHABLAIS" in ALPINE_MASSIFS

    def test_mont_blanc_present(self) -> None:
        """MONT-BLANC must be in the Alpine list."""
        assert "MONT-BLANC" in ALPINE_MASSIFS

    def test_no_pyrenean_massifs(self) -> None:
        """Pyrenean massifs should not be in the Alpine list."""
        pyrenean = {"ASPE-OSSAU", "AURE-LOURON", "HAUTE-BIGORRE", "LUCHONNAIS"}
        assert not pyrenean.intersection(ALPINE_MASSIFS)

    def test_no_corsican_massifs(self) -> None:
        """Corsican massifs should not be in the Alpine list."""
        corsican = {"CINTO-ROTONDO", "RENOSO-INCUDINE"}
        assert not corsican.intersection(ALPINE_MASSIFS)

    def test_all_uppercase_hyphenated(self) -> None:
        """All massif names should be uppercase strings with only hyphens as separators."""
        for name in ALPINE_MASSIFS:
            assert name == name.upper(), f"{name!r} is not uppercase"
            assert " " not in name, f"{name!r} contains a space"


class TestAllMassifs:
    """Tests for the ALL_MASSIFS constant."""

    def test_count_is_35(self) -> None:
        """ALL_MASSIFS should include all 35 massifs from the public API."""
        assert len(ALL_MASSIFS) == 35

    def test_alpine_massifs_subset(self) -> None:
        """Every Alpine massif should appear in ALL_MASSIFS."""
        alpine_set = set(ALPINE_MASSIFS)
        all_set = set(ALL_MASSIFS)
        assert alpine_set.issubset(all_set)


class TestSlugify:
    """Tests for the slugify() helper."""

    def test_uppercase_passthrough(self) -> None:
        """Canonical names pass through unchanged."""
        assert slugify("CHABLAIS") == "CHABLAIS"

    def test_strips_whitespace(self) -> None:
        """Leading and trailing whitespace is stripped."""
        assert slugify("  MONT-BLANC  ") == "MONT-BLANC"

    def test_upcases_lowercase(self) -> None:
        """Lowercase input is converted to uppercase."""
        assert slugify("chablais") == "CHABLAIS"
