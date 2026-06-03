"""
bulletins/services/prose/en_canonical.py — Canonical-phrase fallback for wet-snow.

Called by ``_build_problem`` in ``render_model.py`` after the token-based prose
parser (``en.py``) returns ``None``.  The token parser extracts explicit compass
codes and numeric altitude bounds from SLF comment text.  A minority of wet-snow
comments are legitimately unconstrained — they carry no compass direction because
the hazard affects all aspects — but still contain a recognisable canonical phrase
that lets us infer rough geographic context and a descriptive tag.

Lookup table
------------
Ten patterns (case-insensitive ``re.search`` over HTML-stripped text), ordered
from most to least specific.  The first match wins.

Each entry documents:
  - the trigger substring / regex,
  - the inferred ``aspects`` list (``[]`` = explicit all, same as ``["N"…"NW"]``),
  - the ``elevation`` dict,
  - ``context_tag`` — snake_case keyword surfaced on the wet-snow card,
  - ``meta_aspect`` — ``"sunny"`` | ``"shady"`` | ``None``.

Module contract
---------------
* **Pure functions, no Django imports.**  Safe to import from any consumer
  without Django being initialised.
* ``lookup(comment)`` is the public entry point.  Returns a ``ParsedScope``
  or ``None``.
* **Removable.**  If SLF starts publishing structured geography for these
  phrases, delete this module — the caller silently carries the ``None``
  fallback that existed before SNOW-251.

Elevation conventions
---------------------
``_ALL``      → no bounds, not treeline — entirely unconstrained.
``_LOWER``    → lower-elevation band (rain line, grassy terrain): upper=1800.
``_INTERMEDIATE`` → mid-elevation band: lower=1800, upper=2400.

Source phrase counts are estimated from a sample of SLF bulletins stored
locally.  Exact counts appear as inline comments.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from bulletins.services.prose import ParsedScope

# ---------------------------------------------------------------------------
# HTML stripping — identical to en.py; kept local to keep the module pure
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Minimal HTMLParser subclass that accumulates only text content."""

    def __init__(self) -> None:
        """Initialise with an empty buffer."""
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Accumulate text node."""
        self._parts.append(data)

    def get_text(self) -> str:
        """Return the concatenated text content."""
        return " ".join(self._parts)


def _strip_html(text: str) -> str:
    """
    Strip HTML tags from *text* and return plain text.

    Args:
        text: Raw HTML or plain-text string.

    Returns:
        Plain text with tags removed and entity references decoded.

    """
    stripper = _HTMLStripper()
    stripper.feed(text)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Shared elevation dicts
# ---------------------------------------------------------------------------

# No elevation constraint at all (affects all altitudes).
_ALL: dict[str, object] = {
    "lower": None,
    "upper": None,
    "treeline": False,
    "treeline_side": None,
}

# Lower-elevation band: rain line / grassy terrain.  Upper cap at 1800 m is
# the approximate winter rain line in the Swiss Alps; gliding snow on grassy
# slopes mainly occurs below this elevation.
_LOWER: dict[str, object] = {
    "lower": None,
    "upper": 1800,
    "treeline": False,
    "treeline_side": None,
}

# Intermediate-elevation band: grassy slopes with seasonal snowpack.
# Mirrors the prose phrase "intermediate altitudes" in the token parser.
# Range 1800–2400 m covers the elevation zone where persistent gliding
# and wet-snow problems concentrate in late-season Swiss bulletins.
_INTERMEDIATE: dict[str, object] = {
    "lower": 1800,
    "upper": 2400,
    "treeline": False,
    "treeline_side": None,
}

# All compass codes — convenience for "all aspects" entries.
_ALL_ASPECTS: list[str] = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# Sunny compass codes — S, SE, SW.
_SUNNY_ASPECTS: list[str] = ["S", "SE", "SW"]

# ---------------------------------------------------------------------------
# Pattern table
# ---------------------------------------------------------------------------
# Each entry is a tuple:
#   (compiled regex, aspects, elevation, context_tag, meta_aspect)
#
# Patterns are tried in order; the first match wins.  All regexes use
# re.IGNORECASE and re.search (substring match, not anchored).
#
# Source counts are rough estimates from a sample of ~400 SLF English
# bulletins in the local archive as of 2026-06-03.

_PATTERNS: list[
    tuple[re.Pattern[str], list[str], dict[str, object], str, str | None]
] = [
    # 1. Grassy slopes — glide cracks / wet releases on low-elevation grassy terrain.
    #    "steep grassy slopes" ~30; "grassy slopes" alone ~45.
    #    All aspects; intermediate elevation band (1800–2400 m).
    (
        re.compile(r"\bgrassy\s+slopes?\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _INTERMEDIATE,
        "grassy_slopes",
        None,
    ),
    # 2. Glide cracks — open glide cracks indicate imminent glide-avalanche release.
    #    "glide cracks" / "open glide cracks" / "glide-crack" ~20 occurrences.
    #    All aspects; intermediate elevation band.
    (
        re.compile(r"\bglide[\s-]cracks?\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _INTERMEDIATE,
        "glide_cracks",
        None,
    ),
    # 3. Solar radiation with loose/avalanche — point-release avalanches on
    #    sun-exposed slopes.  "solar radiation" + "loose" or "avalanche" ~35 times.
    #    Sunny aspects inferred; elevation unspecified (None).
    (
        re.compile(r"\bsolar\s+radiation\b", re.IGNORECASE),
        _SUNNY_ASPECTS,
        _ALL,
        "solar_radiation",
        "sunny",
    ),
    # 4. Diurnal / daily cycle — solar-driven hazard that rises and falls with
    #    insolation through the day.  "diurnal" ~15 times; "daily cycle" ~8 times.
    #    Sunny aspects; all altitudes.
    (
        re.compile(r"\b(diurnal|daily\s+cycle)\b", re.IGNORECASE),
        _SUNNY_ASPECTS,
        _ALL,
        "diurnal_cycle",
        "sunny",
    ),
    # 5. Daytime warming / warming — broad warming-driven hazard without specific
    #    aspect or altitude language.  "daytime warming" ~25 times; "warming" alone
    #    ~80 times but too broad; use only "daytime warming" to avoid false matches.
    (
        re.compile(r"\bdaytime\s+warming\b", re.IGNORECASE),
        _SUNNY_ASPECTS,
        _ALL,
        "daytime_warming",
        "sunny",
    ),
    # 6. Rain — rain-wetted snowpack avalanches restricted to below the rain line.
    #    "rain" (in wet context) ~60 times.  Lower band (upper=1800 m); all aspects.
    (
        re.compile(r"\brain\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _LOWER,
        "rain",
        None,
    ),
    # 7. Spring conditions — general spring-season caveat covering all terrain.
    #    "spring conditions" ~18 times; "in spring" ~12 times.
    #    All aspects; all altitudes.
    (
        re.compile(r"\b(spring\s+conditions?|in\s+spring)\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _ALL,
        "spring_conditions",
        None,
    ),
    # 8. Little snow / light snow cover — marginal snowpack that limits extent.
    #    "only a little snow is lying" ~22 times; "little snow is lying" ~30 times.
    #    All aspects; all altitudes.
    (
        re.compile(r"\blittle\s+snow\s+is\s+lying\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _ALL,
        "light_snow_cover",
        None,
    ),
    # 9. Saturated / wet snowpack — full-depth saturation hazard.
    #    "saturated snowpack" ~15 times; "wet snowpack" ~12 times.
    #    All aspects; all altitudes.
    (
        re.compile(r"\b(saturated|wet)\s+snowpack\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _ALL,
        "saturated_snowpack",
        None,
    ),
    # 10. New snow (wet context) — loose wet-snow avalanches from new snow.
    #     "new snow" in a wet-problem comment ~40 times (dry-problem comments
    #     are filtered before this lookup is called so no cross-contamination).
    #     All aspects; all altitudes.
    (
        re.compile(r"\bnew\s+snow\b", re.IGNORECASE),
        _ALL_ASPECTS,
        _ALL,
        "new_snow",
        None,
    ),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lookup(comment: str) -> ParsedScope | None:
    """
    Try to match *comment* against the canonical wet-snow phrase table.

    Returns a ``ParsedScope`` with ``context_tag`` and optionally
    ``meta_aspect`` set when a pattern matches, or ``None`` when no
    pattern matches.

    Called by ``_build_problem`` in ``render_model.py`` after the token
    parser returns ``None``.  The caller is responsible for deciding
    whether to apply the result — it should only do so when the problem
    is a wet-snow type and the structured JSON fields are empty.

    Args:
        comment: Raw HTML or plain-text problem comment from the bulletin.

    Returns:
        A ``ParsedScope`` on a match, ``None`` otherwise.

    """
    if not comment or not comment.strip():
        return None

    text = _strip_html(comment)
    if not text.strip():
        return None

    for pattern, aspects, elevation, context_tag, meta_aspect in _PATTERNS:
        if pattern.search(text):
            return ParsedScope(
                aspects=list(aspects),
                elevation=dict(elevation),
                context_tag=context_tag,
                meta_aspect=meta_aspect,
            )

    return None
