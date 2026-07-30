"""
tests/favourites/test_relevance.py — Tests for apps.favourites.relevance.

Covers:
  band_relevance — pure boundary matrix over both-bounds / lower-only /
                    upper-only x inside / above / below / exact boundary
                    (inclusive: pin == lower and pin == upper both APPLIES).
  _relevance_from_bounds — the ElevationBounds adapter: empty bounds -> None;
                    a treeline-pivoted bound -> None (no fabricated verdict);
                    numeric one-sided/both bounds -> the matching verdict.
  annotate_problem_relevance — sentinel-driven: for every committed
                    tests/sentinels/*/*/source.json, build the same problem
                    cards the bulletin page renders and annotate them at a
                    very-low and a very-high pin elevation. Asserts the
                    highlight-never-suppress count invariant
                    (len(annotated) == len(cards)) and that every verdict is
                    one of the four legal values. Explicitly asserts that a
                    problem with no elevation band (Meteo-France A wet_snow)
                    and one with a treeline-pivoted bound (ALBINA C
                    persistent_weak_layers) come back unannotated (None)
                    regardless of pin elevation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.bulletins.services.render_model import build_render_model
from apps.favourites.relevance import (
    RELEVANCE_ABOVE,
    RELEVANCE_APPLIES,
    RELEVANCE_BELOW,
    _relevance_from_bounds,
    annotate_problem_relevance,
    band_relevance,
)
from apps.public.views import (
    ElevationBounds,
    _resolve_problem_cards,
    enrich_render_model,
)

SENTINELS_DIR = Path(__file__).resolve().parent.parent / "sentinels"

# ---------------------------------------------------------------------------
# band_relevance — pure boundary matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pin", "lower", "upper", "expected"),
    [
        # No bounds at all — unannotated.
        (2000.0, None, None, None),
        # Lower-only band.
        (2500.0, 2000, None, RELEVANCE_APPLIES),  # well inside
        (1500.0, 2000, None, RELEVANCE_ABOVE),  # below the lower bound
        (2000.0, 2000, None, RELEVANCE_APPLIES),  # exact boundary, inclusive
        # Upper-only band.
        (1500.0, None, 2000, RELEVANCE_APPLIES),  # well inside
        (2500.0, None, 2000, RELEVANCE_BELOW),  # above the upper bound
        (2000.0, None, 2000, RELEVANCE_APPLIES),  # exact boundary, inclusive
        # Both bounds.
        (2200.0, 2000, 2400, RELEVANCE_APPLIES),  # well inside
        (1500.0, 2000, 2400, RELEVANCE_ABOVE),  # below the band
        (2600.0, 2000, 2400, RELEVANCE_BELOW),  # above the band
        (2000.0, 2000, 2400, RELEVANCE_APPLIES),  # exact lower boundary
        (2400.0, 2000, 2400, RELEVANCE_APPLIES),  # exact upper boundary
    ],
)
def test_band_relevance_matrix(
    pin: float, lower: int | None, upper: int | None, expected: str | None
) -> None:
    """band_relevance covers every bound-shape x position combination."""
    assert band_relevance(pin, lower, upper) == expected


# ---------------------------------------------------------------------------
# _relevance_from_bounds — ElevationBounds adapter
# ---------------------------------------------------------------------------


def _bounds(lower: str = "", upper: str = "", bound_type: str = "") -> ElevationBounds:
    """Build an ElevationBounds for adapter tests (display is irrelevant here)."""
    return ElevationBounds(lower=lower, upper=upper, display="x", bound_type=bound_type)


class TestRelevanceFromBounds:
    """Tests for apps.favourites.relevance._relevance_from_bounds."""

    def test_none_elevation_is_unannotated(self) -> None:
        """A None elevation value returns None (no band data at all)."""
        assert _relevance_from_bounds(2000.0, None) is None

    def test_empty_bounds_is_unannotated(self) -> None:
        """An empty ElevationBounds (bound_type='') returns None."""
        assert _relevance_from_bounds(2000.0, _bounds()) is None

    def test_treeline_lower_bound_is_unannotated(self) -> None:
        """A treeline-pivoted lower bound is left unannotated, not fabricated."""
        bounds = _bounds(lower="treeline", bound_type="LOWER")
        assert _relevance_from_bounds(1500.0, bounds) is None
        assert _relevance_from_bounds(2600.0, bounds) is None

    def test_treeline_upper_bound_is_unannotated(self) -> None:
        """A treeline-pivoted upper bound is left unannotated, not fabricated."""
        bounds = _bounds(upper="treeline", bound_type="UPPER")
        assert _relevance_from_bounds(1500.0, bounds) is None

    def test_numeric_lower_only(self) -> None:
        """A numeric lower-only bound resolves via band_relevance."""
        bounds = _bounds(lower="2000", bound_type="LOWER")
        assert _relevance_from_bounds(2500.0, bounds) == RELEVANCE_APPLIES
        assert _relevance_from_bounds(1500.0, bounds) == RELEVANCE_ABOVE

    def test_numeric_upper_only(self) -> None:
        """A numeric upper-only bound resolves via band_relevance."""
        bounds = _bounds(upper="2000", bound_type="UPPER")
        assert _relevance_from_bounds(1500.0, bounds) == RELEVANCE_APPLIES
        assert _relevance_from_bounds(2500.0, bounds) == RELEVANCE_BELOW

    def test_numeric_both_bounds(self) -> None:
        """A numeric both-bounds band resolves via band_relevance."""
        bounds = _bounds(lower="2000", upper="2400", bound_type="BOTH")
        assert _relevance_from_bounds(2200.0, bounds) == RELEVANCE_APPLIES
        assert _relevance_from_bounds(1500.0, bounds) == RELEVANCE_ABOVE
        assert _relevance_from_bounds(2600.0, bounds) == RELEVANCE_BELOW


# ---------------------------------------------------------------------------
# annotate_problem_relevance — sentinel-driven
# ---------------------------------------------------------------------------


def _all_sentinel_source_paths() -> list[Path]:
    """Return every committed sentinel ``source.json`` path."""
    return sorted(SENTINELS_DIR.glob("*/*/source.json"))


def _sentinel_id(path: Path) -> str:
    """Return a short human-readable test ID from a sentinel file path."""
    return str(path.relative_to(SENTINELS_DIR).parent)


def _cards_for_sentinel(source_path: Path) -> list[dict[str, Any]]:
    """Build the same problem cards the bulletin page would render for a sentinel."""
    raw: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
    raw_render_model = build_render_model(raw)
    render_model = enrich_render_model(raw_render_model)
    traits = render_model.get("traits") or []
    danger = raw_render_model.get("danger") or {}
    return _resolve_problem_cards(
        traits,
        raw_render_model.get("danger_patterns") or [],
        danger.get("ratings") or [],
    )


@pytest.mark.parametrize("source_path", _all_sentinel_source_paths(), ids=_sentinel_id)
def test_annotate_never_changes_card_count(source_path: Path) -> None:
    """Highlight-never-suppress: annotation never filters, reorders, or drops a card."""
    cards = _cards_for_sentinel(source_path)

    for pin_elevation in (200.0, 4000.0):
        annotated = annotate_problem_relevance(cards, pin_elevation)
        assert len(annotated) == len(cards)
        for card in annotated:
            assert card["altitude_relevance"] in {
                RELEVANCE_APPLIES,
                RELEVANCE_ABOVE,
                RELEVANCE_BELOW,
                None,
            }


def test_meteofrance_a_wet_snow_missing_elevation_is_unannotated() -> None:
    """Meteo-France A's wet_snow problem carries no elevation band -> unannotated."""
    source_path = SENTINELS_DIR / "meteofrance" / "A-single-level" / "source.json"
    cards = _cards_for_sentinel(source_path)
    wet_snow_cards = [c for c in cards if c["label"] == "Wet snow"]
    assert wet_snow_cards, "expected at least one wet_snow card in the sentinel"

    for pin_elevation in (200.0, 4000.0):
        annotated = annotate_problem_relevance(cards, pin_elevation)
        wet_snow_annotated = [c for c in annotated if c["label"] == "Wet snow"]
        assert all(c["altitude_relevance"] is None for c in wet_snow_annotated)


def test_albina_c_persistent_weak_layers_treeline_is_unannotated() -> None:
    """ALBINA C's persistent_weak_layers problems pivot on treeline -> unannotated."""
    source_path = SENTINELS_DIR / "albina" / "C-split-day-multi-problem" / "source.json"
    cards = _cards_for_sentinel(source_path)
    pwl_cards = [c for c in cards if c["label"] == "Persistent weak layers"]
    assert pwl_cards, (
        "expected at least one persistent_weak_layers card in the sentinel"
    )

    for pin_elevation in (200.0, 4000.0):
        annotated = annotate_problem_relevance(cards, pin_elevation)
        pwl_annotated = [c for c in annotated if c["label"] == "Persistent weak layers"]
        assert all(c["altitude_relevance"] is None for c in pwl_annotated)
