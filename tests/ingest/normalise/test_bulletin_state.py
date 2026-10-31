"""
tests/ingest/normalise/test_bulletin_state.py — The alert state's fingerprints hold.

SNOW-1341 moved ``bulletin_state`` and its dataclasses from
``apps/bulletins/services/bulletin_change.py`` into ingest. The send log
(``BulletinAlert.fingerprint``) is keyed on ``BulletinState.fingerprint()``,
so a move that changed one byte of the hash would re-send every account's
last alert. The golden values below were computed from the code BEFORE the
move, one per sentinel bulletin, and both import surfaces must reproduce
them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.bulletins.services import bulletin_change
from apps.ingest.normalise.render_model import build_render_model

SENTINELS_DIR = Path(__file__).resolve().parents[2] / "sentinels"

# Computed with apps.bulletins.services.bulletin_change.bulletin_state at
# RENDER_MODEL_VERSION 9, before SNOW-1341 touched it.
GOLDEN_FINGERPRINTS: dict[str, str] = {
    "albina/A-single-level": "85cd994f9890f5b7",
    "albina/B-elevation-band-split": "e3481bcca6b4e87c",
    "albina/C-split-day-multi-problem": "2c326188924bea6c",
    "meteofrance/A-single-level": "290af4803db19f49",
    "meteofrance/B-elevation-band-split": "4d14f15dc19a3757",
    "meteofrance/C-elevation-split-multi-problem": "745d83bf5f042c32",
    "slf/A-single-level": "61e45073be746516",
    "slf/B-subdivision-plus": "a5ab424cce2dd555",
    "slf/C-split-day-multi-problem": "51d932dccd5006a2",
    "slf/D-per-region-single-problem": "dca300284d7dadeb",
    "slf/E-per-region-two-problems": "4915eb64f9c6a39c",
}


def _render_model(sentinel: str) -> dict[str, Any]:
    """Return the render model of one sentinel bulletin.

    Args:
        sentinel: ``<provider>/<case>``, a directory under tests/sentinels.

    Returns:
        ``build_render_model`` of its ``source.json``.

    """
    raw = json.loads((SENTINELS_DIR / sentinel / "source.json").read_text())
    return build_render_model(raw)


def test_every_sentinel_has_a_golden_value() -> None:
    """A new sentinel needs its fingerprint pinned here too."""
    found = {
        str(p.parent.relative_to(SENTINELS_DIR))
        for p in SENTINELS_DIR.glob("*/*/source.json")
    }
    assert found == set(GOLDEN_FINGERPRINTS)


@pytest.mark.parametrize("sentinel", sorted(GOLDEN_FINGERPRINTS))
def test_fingerprint_is_byte_identical(sentinel: str) -> None:
    """The compatibility surface computes the pre-move fingerprint."""
    state = bulletin_change.bulletin_state(_render_model(sentinel))
    assert state.fingerprint() == GOLDEN_FINGERPRINTS[sentinel]
