"""
apps/public/headlines.py — Data-driven headline matrix for bulletin pages.

Provides ``headline_for(source, partition_type, peak_rating, direction,
family) -> str``, backed by a versioned matrix of hand-authored copy for
the top-12 SLF cells and a generic fallback for everything else.

Matrix key: ``(source, partition_type, peak_rating, direction, family)``

- source:         ``"slf"``, ``"albina"``, ``"meteofrance"``
- partition_type: ``"none"`` (single-period), ``"temporal"``, or
                  ``"elevation"``
- peak_rating:    ``"1"``–``"5"`` with optional SLF subdivision suffix
                  (``"4-"``, ``"4="``, ``"4+"``). Pass the plain integer
                  string for sources without subdivision.
- direction:      ``"none"``, ``"rise"``, or ``"fall"``
- family:         ``"slab"``, ``"persistent"``, ``"wet"``, ``"other"``,
                  or ``"mixed"``

Top-12 SLF hand-authored cells:

 1. (slf, none, 1, none, slab)
 2. (slf, none, 2, none, slab)
 3. (slf, none, 2, none, persistent)
 4. (slf, none, 3, none, slab)
 5. (slf, none, 3, none, persistent)
 6. (slf, none, 4, none, slab)       — skier-high; 4-, 4= share same copy
 6c.(slf, none, 4+, none, slab)      — road-high; distinct copy
 7. (slf, none, 4, none, wet)
 8. (slf, temporal, 2, rise, wet)
 9. (slf, temporal, 3, rise, wet)
10. (slf, temporal, 3, fall, slab)
11. (slf, none, 5, none, slab)
12. (slf, none, 3, none, mixed)

One EUREGIO elevation-banded cell (cell 13):
 (meteofrance|albina, elevation, 3, rise, slab)

All other cells fall back to the generic phrasing:
    "Danger level {N} {word}. Read the bulletin carefully."

``HEADLINE_MATRIX_VERSION`` is incremented whenever the cell list or copy
changes so callers can detect stale cached output.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

HEADLINE_MATRIX_VERSION: int = 1

# ---------------------------------------------------------------------------
# Generic fallback helpers
# ---------------------------------------------------------------------------

_PEAK_WORDS: dict[str, str] = {
    "1": "low",
    "2": "moderate",
    "3": "considerable",
    "4": "high",
    "4-": "high",
    "4=": "high",
    "4+": "high",
    "5": "very high",
}


def _generic_headline(peak: str) -> str:
    """Build the generic fallback headline for a given peak rating.

    Args:
        peak: The peak rating string (e.g. ``"3"``, ``"4-"``).

    Returns:
        A generic headline string.

    """
    # Strip subdivision suffix to get the bare number for the ``peak_word`` lookup.
    bare = peak.rstrip("+-=") if len(peak) > 1 else peak
    peak_word = _PEAK_WORDS.get(peak) or _PEAK_WORDS.get(bare) or "unknown"
    return str(
        _("Danger level %(peak)s %(peak_word)s. Read the bulletin carefully.")
        % {"peak": bare, "peak_word": peak_word}
    )


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

# Key shape: (source, partition_type, peak_rating, direction, family)
# All strings; direction "none" for single-period days; peak_rating is the
# numeric string for the highest danger level, with optional SLF subdivision
# suffix ("+", "-", "=").
_MATRIX: dict[tuple[str, str, str, str, str], str] = {
    # ── Cell 1: low danger, slab ───────────────────────────────────────────
    ("slf", "none", "1", "none", "slab"): str(
        _("Generally favourable. Low danger — isolated trigger points only.")
    ),
    # ── Cell 2: moderate danger, slab ─────────────────────────────────────
    ("slf", "none", "2", "none", "slab"): str(
        _("Heightened caution. Moderate danger — choose terrain conservatively.")
    ),
    # ── Cell 3: moderate danger, persistent ───────────────────────────────
    ("slf", "none", "2", "none", "persistent"): str(
        _(
            "Persistent weakness lingers. Moderate danger"
            " — avoid stress on shaded steep slopes."
        )
    ),
    # ── Cell 4: considerable danger, slab ─────────────────────────────────
    ("slf", "none", "3", "none", "slab"): str(
        _("Reactive snowpack. Considerable danger — avoid wind-loaded steeps.")
    ),
    # ── Cell 5: considerable danger, persistent ───────────────────────────
    ("slf", "none", "3", "none", "persistent"): str(
        _(
            "Deep persistent weakness. Considerable danger"
            " — careful route choice essential."
        )
    ),
    # ── Cell 6: high danger, slab (skier-high: bare 4, 4-, 4=) ───────────
    ("slf", "none", "4", "none", "slab"): str(
        _(
            "Dangerous conditions. High danger"
            " — large avalanches possible;"
            " off-piste travel not recommended."
        )
    ),
    ("slf", "none", "4-", "none", "slab"): str(
        _(
            "Dangerous conditions. High danger"
            " — large avalanches possible;"
            " off-piste travel not recommended."
        )
    ),
    ("slf", "none", "4=", "none", "slab"): str(
        _(
            "Dangerous conditions. High danger"
            " — large avalanches possible;"
            " off-piste travel not recommended."
        )
    ),
    # ── Cell 6c: high danger 4+, slab (road-high) ─────────────────────────
    ("slf", "none", "4+", "none", "slab"): str(
        _(
            "Widespread instability. High danger"
            " — road and infrastructure exposure;"
            " backcountry travel inadvisable."
        )
    ),
    # ── Cell 7: high danger, wet ──────────────────────────────────────────
    ("slf", "none", "4", "none", "wet"): str(
        _(
            "Wet-snow cycle. High danger"
            " — significant releases expected on sun-affected aspects."
        )
    ),
    # ── Cell 8: temporal rise to moderate, wet ────────────────────────────
    ("slf", "temporal", "2", "rise", "wet"): str(
        _(
            "Manageable morning, more reactive afternoon"
            " — wet snow expected later in the day."
        )
    ),
    # ── Cell 9: temporal rise to considerable, wet ────────────────────────
    ("slf", "temporal", "3", "rise", "wet"): str(
        _(
            "Touchy morning, dangerous afternoon"
            " — wet snow cycle developing as the day progresses."
        )
    ),
    # ── Cell 10: temporal fall from considerable, slab ────────────────────
    ("slf", "temporal", "3", "fall", "slab"): str(
        _("Reactive at first, easing later — wind slab settles as the day progresses.")
    ),
    # ── Cell 11: very high danger, slab ───────────────────────────────────
    ("slf", "none", "5", "none", "slab"): str(
        _(
            "Extreme conditions. Very high danger"
            " — large natural avalanches, stay out of avalanche terrain."
        )
    ),
    # ── Cell 12: considerable danger, mixed ──────────────────────────────
    ("slf", "none", "3", "none", "mixed"): str(
        _(
            "Mixed hazards. Considerable danger"
            " — multiple problem types in play;"
            " read the bulletin carefully."
        )
    ),
    # ── Cell 13: EUREGIO elevation-banded, considerable rise, slab ────────
    ("meteofrance", "elevation", "3", "rise", "slab"): str(
        _(
            "Higher danger at altitude. Considerable above the band"
            " — manageable lower down."
        )
    ),
    ("albina", "elevation", "3", "rise", "slab"): str(
        _(
            "Higher danger at altitude. Considerable above the band"
            " — manageable lower down."
        )
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def headline_for(
    source: str,
    partition_type: str,
    peak_rating: str,
    direction: str,
    family: str,
) -> str:
    """Return the headline string for a given bulletin profile.

    Looks up ``(source, partition_type, peak_rating, direction, family)`` in
    the versioned :data:`_MATRIX`. Falls back to a generic danger-level
    phrasing when no hand-authored entry exists.

    The ``direction`` argument should be ``"none"`` for single-period days (not
    ``None``) so the dict key is a plain string tuple throughout.

    Args:
        source:         Bulletin source — ``"slf"``, ``"albina"``, or
                        ``"meteofrance"``.
        partition_type: Day partition — ``"none"``, ``"temporal"``, or
                        ``"elevation"``.
        peak_rating:    Peak danger level as a string, e.g. ``"3"``, ``"4-"``,
                        ``"4+"``.
        direction:      Transition direction — ``"none"``, ``"rise"``, or
                        ``"fall"``.
        family:         Problem family — ``"slab"``, ``"persistent"``,
                        ``"wet"``, ``"other"``, or ``"mixed"``.

    Returns:
        A translated headline string.

    """
    key = (source, partition_type, peak_rating, direction, family)
    headline = _MATRIX.get(key)
    if headline is not None:
        return headline
    return _generic_headline(peak_rating)
