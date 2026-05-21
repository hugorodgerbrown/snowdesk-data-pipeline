# test_massifs.py — Tests for _massifs.py canonical name list and slugify helper.
"""Tests for the canonical massif list, slugify helper, and slug→code lookup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _massifs import (  # noqa: E402
    ALL_MASSIFS,
    ALPINE_MASSIFS,
    SLUG_TO_CODE,
    slug_to_region_id,
    slugify,
)


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


class TestSlugToCode:
    """Tests for the SLUG_TO_CODE mapping."""

    def test_count_is_23(self) -> None:
        """SLUG_TO_CODE should contain exactly 23 Alpine massifs."""
        assert len(SLUG_TO_CODE) == 23

    def test_chablais_maps_to_1(self) -> None:
        """CHABLAIS → massif code 1."""
        assert SLUG_TO_CODE["CHABLAIS"] == 1

    def test_mont_blanc_maps_to_3(self) -> None:
        """MONT-BLANC → massif code 3."""
        assert SLUG_TO_CODE["MONT-BLANC"] == 3

    def test_embrunais_parpaillon_maps_to_20(self) -> None:
        """EMBRUNAIS-PARPAILLON → massif code 20 (space→hyphen normalisation)."""
        assert SLUG_TO_CODE["EMBRUNAIS-PARPAILLON"] == 20

    def test_no_pyrenean_massifs(self) -> None:
        """Pyrenean massifs (cluster != Alps) must not appear in SLUG_TO_CODE."""
        pyrenean = {"ASPE-OSSAU", "AURE-LOURON", "HAUTE-BIGORRE", "LUCHONNAIS"}
        assert not pyrenean.intersection(SLUG_TO_CODE)

    def test_all_alpine_massifs_present(self) -> None:
        """Every slug in ALPINE_MASSIFS must be a key in SLUG_TO_CODE."""
        for slug in ALPINE_MASSIFS:
            assert slug in SLUG_TO_CODE, f"Missing slug: {slug!r}"


class TestSlugToRegionId:
    """Tests for the slug_to_region_id() helper."""

    def test_chablais_returns_fr_01(self) -> None:
        """CHABLAIS → 'FR-01' (code 1, zero-padded)."""
        assert slug_to_region_id("CHABLAIS") == "FR-01"

    def test_mont_blanc_returns_fr_03(self) -> None:
        """MONT-BLANC → 'FR-03' (code 3, zero-padded)."""
        assert slug_to_region_id("MONT-BLANC") == "FR-03"

    def test_embrunais_returns_fr_20(self) -> None:
        """EMBRUNAIS-PARPAILLON → 'FR-20'."""
        assert slug_to_region_id("EMBRUNAIS-PARPAILLON") == "FR-20"

    def test_mercantour_returns_fr_23(self) -> None:
        """MERCANTOUR → 'FR-23' (highest Alpine code)."""
        assert slug_to_region_id("MERCANTOUR") == "FR-23"

    def test_unknown_slug_raises_key_error(self) -> None:
        """Unknown slug raises KeyError."""
        import pytest

        with pytest.raises(KeyError):
            slug_to_region_id("UNKNOWN-MASSIF")
