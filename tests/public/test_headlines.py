"""
tests/public/test_headlines.py — Tests for the headline variant matrix.

Covers:
  - All 12 top SLF cells and the EUREGIO elevation-banded cell (cell 13).
  - The three level-4 subdivision variants (4-, 4=, 4+).
  - The generic fallback for unmatched cells.
  - derive_problem_family() mapping from render model traits.
"""

from __future__ import annotations

import pytest

from apps.bulletins.services.render_model import derive_problem_family
from apps.public.headlines import HEADLINE_MATRIX_VERSION, headline_for

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rm_with_problem(problem_type: str) -> dict:
    """Build a minimal render model containing a single problem of the given type."""
    return {
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "problems": [
                    {
                        "problem_type": problem_type,
                        "aspects": [],
                        "elevation": None,
                        "time_period": "all_day",
                        "comment_html": "",
                        "core_zone_text": None,
                        "danger_rating_value": None,
                    }
                ],
            }
        ]
    }


def _rm_with_problems(*problem_types: str) -> dict:
    """Build a minimal render model with multiple problems across one trait."""
    problems: list[dict] = [
        {
            "problem_type": pt,
            "aspects": [],
            "elevation": None,
            "time_period": "all_day",
            "comment_html": "",
            "core_zone_text": None,
            "danger_rating_value": None,
        }
        for pt in problem_types
    ]
    return {
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "problems": problems,
            }
        ]
    }


# ---------------------------------------------------------------------------
# HEADLINE_MATRIX_VERSION
# ---------------------------------------------------------------------------


def test_headline_matrix_version_is_int() -> None:
    """HEADLINE_MATRIX_VERSION is a positive integer."""
    assert isinstance(HEADLINE_MATRIX_VERSION, int)
    assert HEADLINE_MATRIX_VERSION >= 1


# ---------------------------------------------------------------------------
# Top-12 SLF cells must NOT fall back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "partition_type", "peak_rating", "direction", "family"),
    [
        # Cell 1
        ("SLF", "none", "1", "none", "slab"),
        # Cell 2
        ("SLF", "none", "2", "none", "slab"),
        # Cell 3
        ("SLF", "none", "2", "none", "persistent"),
        # Cell 4
        ("SLF", "none", "3", "none", "slab"),
        # Cell 5
        ("SLF", "none", "3", "none", "persistent"),
        # Cell 6 (bare high, skier-high)
        ("SLF", "none", "4", "none", "slab"),
        # Cell 6a (4-, skier-high)
        ("SLF", "none", "4-", "none", "slab"),
        # Cell 6b (4=, skier-high)
        ("SLF", "none", "4=", "none", "slab"),
        # Cell 6c (4+, road-high)
        ("SLF", "none", "4+", "none", "slab"),
        # Cell 7
        ("SLF", "none", "4", "none", "wet"),
        # Cell 8
        ("SLF", "temporal", "2", "rise", "wet"),
        # Cell 9
        ("SLF", "temporal", "3", "rise", "wet"),
        # Cell 10
        ("SLF", "temporal", "3", "fall", "slab"),
        # Cell 11
        ("SLF", "none", "5", "none", "slab"),
        # Cell 12
        ("SLF", "none", "3", "none", "mixed"),
    ],
)
def test_top_12_cells_do_not_fall_back(
    source: str,
    partition_type: str,
    peak_rating: str,
    direction: str,
    family: str,
) -> None:
    """Every hand-authored SLF cell returns a non-generic headline."""
    result = headline_for(source, partition_type, peak_rating, direction, family)
    # The generic fallback always starts with "Danger level".
    assert not result.startswith("Danger level"), (
        f"Cell ({source!r}, {partition_type!r}, {peak_rating!r}, "
        f"{direction!r}, {family!r}) fell back to generic: {result!r}"
    )
    assert result  # non-empty


# ---------------------------------------------------------------------------
# EUREGIO elevation-banded cell (cell 13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["METEOFRANCE", "ALBINA"])
def test_euregio_elevation_cell(source: str) -> None:
    """EUREGIO elevation-banded cell returns its hand-authored copy."""
    result = headline_for(source, "elevation", "3", "rise", "slab")
    assert "altitude" in result.lower()
    assert not result.startswith("Danger level")


# ---------------------------------------------------------------------------
# Level-4 subdivision: skier-high vs road-high
# ---------------------------------------------------------------------------


def test_level_4_skier_high_bare() -> None:
    """Bare level-4 returns the skier-high copy (large avalanches possible)."""
    result = headline_for("SLF", "none", "4", "none", "slab")
    assert "large avalanches" in result.lower()


def test_level_4_skier_high_minus() -> None:
    """4- returns the same skier-high copy as bare 4."""
    bare = headline_for("SLF", "none", "4", "none", "slab")
    minus = headline_for("SLF", "none", "4-", "none", "slab")
    assert bare == minus


def test_level_4_skier_high_equal() -> None:
    """4= returns the same skier-high copy as bare 4."""
    bare = headline_for("SLF", "none", "4", "none", "slab")
    equal = headline_for("SLF", "none", "4=", "none", "slab")
    assert bare == equal


def test_level_4_road_high() -> None:
    """4+ returns the road-high copy (infrastructure exposure)."""
    result = headline_for("SLF", "none", "4+", "none", "slab")
    assert "road" in result.lower() or "infrastructure" in result.lower()
    # Must differ from the skier-high copy.
    skier_high = headline_for("SLF", "none", "4", "none", "slab")
    assert result != skier_high


# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("peak_rating", "expected_word"),
    [
        ("1", "low"),
        ("2", "moderate"),
        ("3", "considerable"),
        ("4", "high"),
        ("5", "very high"),
    ],
)
def test_generic_fallback_contains_danger_word(
    peak_rating: str, expected_word: str
) -> None:
    """Generic fallback includes the correct danger-level word."""
    # Use a source/family combo that has no hand-authored entry.
    result = headline_for("ALBINA", "none", peak_rating, "none", "other")
    assert "Danger level" in result
    assert expected_word in result


def test_generic_fallback_no_subdivision_in_text() -> None:
    """Generic fallback strips the subdivision suffix from the level number."""
    result = headline_for("SLF", "temporal", "4-", "none", "persistent")
    # "4-" should not appear as-is; "4" should.
    assert "4-" not in result
    assert "4" in result


# ---------------------------------------------------------------------------
# derive_problem_family
# ---------------------------------------------------------------------------


class TestDeriveProblemFamily:
    """Unit tests for the derive_problem_family pure function."""

    def test_wind_slab_maps_to_slab(self) -> None:
        """wind_slab → slab."""
        assert derive_problem_family(_rm_with_problem("wind_slab")) == "slab"

    def test_new_snow_maps_to_slab(self) -> None:
        """new_snow → slab."""
        assert derive_problem_family(_rm_with_problem("new_snow")) == "slab"

    def test_persistent_weak_layers_maps_to_persistent(self) -> None:
        """persistent_weak_layers → persistent."""
        assert (
            derive_problem_family(_rm_with_problem("persistent_weak_layers"))
            == "persistent"
        )

    def test_wet_snow_maps_to_wet(self) -> None:
        """wet_snow → wet."""
        assert derive_problem_family(_rm_with_problem("wet_snow")) == "wet"

    def test_gliding_snow_maps_to_wet(self) -> None:
        """gliding_snow → wet."""
        assert derive_problem_family(_rm_with_problem("gliding_snow")) == "wet"

    def test_cornices_maps_to_other(self) -> None:
        """cornices → other."""
        assert derive_problem_family(_rm_with_problem("cornices")) == "other"

    def test_no_problems_returns_other(self) -> None:
        """No traits → other (generic fallback guard)."""
        assert derive_problem_family({"traits": []}) == "other"

    def test_empty_render_model_returns_other(self) -> None:
        """Missing traits key → other."""
        assert derive_problem_family({}) == "other"

    def test_mixed_slab_and_wet(self) -> None:
        """Two different families → mixed."""
        rm = _rm_with_problems("wind_slab", "wet_snow")
        assert derive_problem_family(rm) == "mixed"

    def test_mixed_slab_and_persistent(self) -> None:
        """wind_slab + persistent_weak_layers → mixed."""
        rm = _rm_with_problems("wind_slab", "persistent_weak_layers")
        assert derive_problem_family(rm) == "mixed"

    def test_two_slab_types_returns_slab_not_mixed(self) -> None:
        """wind_slab + new_snow → slab (same family, not mixed)."""
        rm = _rm_with_problems("wind_slab", "new_snow")
        assert derive_problem_family(rm) == "slab"
