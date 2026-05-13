"""
bulletins/services/render_model.py — Render model builder for SLF and EUREGIO bulletins.

Converts the raw CAAML properties dict stored in Bulletin.raw_data into a
versioned, presentation-ready ``render_model`` dict. The render model is a
stable, flattened representation that views consume directly, avoiding
repeated re-derivation of the same computed values.

The version constant ``RENDER_MODEL_VERSION`` must be incremented whenever
the output shape or logic changes so that existing rows can be detected as
stale and rebuilt via the ``rebuild_render_models`` management command.

Also provides ``compute_day_character``, a pure function that classifies a
render_model into one of five day-character entries using the five-rule cascade
defined in docs/day_character_rules_spec.md. Each entry is a
:class:`DayCharacter` dataclass carrying both the canonical label and a
one-line explainer that the bulletin page surfaces as an eyebrow above the
"Day Risk Profile" section.

Version 2 changes:
  - Aggregation drives trait and problem ordering verbatim.
  - Strict validation against the canonical 8-token EAWS problem-type enum.
  - ``RenderModelBuildError`` raised on unexpected data shapes.
  - ``title`` fallback derived from (category, time_period) when blank.
  - On validation failure the caller stores
    ``render_model = {"version": 0, "error": ..., "error_type": ...}``.

Version 3 changes:
  - Added ``metadata`` top-level key with publication/validity timestamps,
    ``unscheduled`` flag, and ``lang``. Missing timestamps → ``None``;
    unparseable timestamps → ``None`` (lenient, no raise).
  - Added ``prose`` top-level key with ``snowpack_structure``,
    ``weather_review``, ``weather_forecast`` HTML strings, and a
    ``tendency`` list. Each tendency entry carries ``comment``,
    ``tendency_type``, ``valid_from``, and ``valid_until``.
  - Top-level ``snowpack_structure`` is kept (equals ``prose.snowpack_structure``)
    for backward compatibility; the v4 bump will drop it.

Version 3 (continued — no shape change requiring regeneration):
  - Removed ``fallback_key_message`` from the output shape. The field was
    never rendered in any template; ``properties.highlights`` has been
    absent in SLF data since 2023-12-13.
  - Aggregation synthesis was removed. Missing aggregation logged ERROR and
    returned empty traits instead of synthesising from problem types.

Version 4 changes:
  - Source-aware builder: ``_detect_source()`` identifies SLF vs. EUREGIO
    bulletins and routes to source-specific helpers.
  - Added ``source`` top-level key: ``"slf"`` or ``"euregio"``.
  - ``_resolve_aggregations()`` synthesises aggregation from problem types
    for EUREGIO bulletins (ALBINA/LWD customData present, no CH aggregation).
  - Added per-problem ``avalanche_type`` field (``"slab"``, ``"loose"``,
    or ``None``), drawn from ``customData.ALBINA.avalancheType`` for EUREGIO.
  - Added per-problem ``extras`` field: source-specific passthrough dict.
    SLF: ``{"subdivision": str, "core_zone_text": str|None}``.
    EUREGIO: ``{"avalanche_type": str|None}``.
  - Added ``prose.avalanche_activity`` dict with ``highlights`` and
    ``comment`` string fields (empty strings for SLF; populated from
    ``avalancheActivity`` for EUREGIO).
  - Added top-level ``danger_patterns`` list (``[]`` for SLF;
    ``customData.LWD_Tyrol.dangerPatterns`` for EUREGIO).
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    # ``django_stubs_ext`` ships only with the typing toolchain; importing
    # it at runtime would force every test/CI env to install a typing-only
    # dependency. ``from __future__ import annotations`` (above) means the
    # ``StrOrPromise`` reference in DayCharacter resolves as a forward
    # string at runtime, so the import is genuinely free.
    from django_stubs_ext import StrOrPromise

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

RENDER_MODEL_VERSION: int = 4

# ---------------------------------------------------------------------------
# Constants — EAWS problem-type enum (openapi.json lines 670–683)
# ---------------------------------------------------------------------------

DRY_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        "new_snow",
        "wind_slab",
        "persistent_weak_layers",
        "cornices",
        "no_distinct_avalanche_problem",
        "favourable_situation",
    }
)
WET_PROBLEM_TYPES: frozenset[str] = frozenset({"wet_snow", "gliding_snow"})
KNOWN_PROBLEM_TYPES: frozenset[str] = DRY_PROBLEM_TYPES | WET_PROBLEM_TYPES

PROBLEM_TYPE_TO_CATEGORY: dict[str, str] = {
    **{t: "dry" for t in DRY_PROBLEM_TYPES},
    **{t: "wet" for t in WET_PROBLEM_TYPES},
}

_VALID_TIME_PERIODS: frozenset[str] = frozenset({"all_day", "earlier", "later"})

# ---------------------------------------------------------------------------
# Danger constants
# ---------------------------------------------------------------------------

_DANGER_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

_DANGER_NUMBER: dict[str, str] = {
    "low": "1",
    "moderate": "2",
    "considerable": "3",
    "high": "4",
    "very_high": "5",
}

_SUBDIVISION_MAP: dict[str, str] = {
    "plus": "+",
    "equal": "=",
    "minus": "-",
}

# Avalanche problem types that indicate a hard-to-read day (rule 2).
_HARD_TO_READ_PROBLEMS: frozenset[str] = frozenset(
    {"persistent_weak_layers", "gliding_snow"}
)

_TREELINE_TOKEN = "treeline"  # noqa: S105 — not a password; schema token

# ---------------------------------------------------------------------------
# Title fallbacks — (category, time_period) → display string
# ---------------------------------------------------------------------------

_TITLE_FALLBACK: dict[tuple[str, str], str] = {
    ("dry", "all_day"): "Dry avalanches",
    ("dry", "earlier"): "Dry avalanches, earlier",
    ("dry", "later"): "Dry avalanches, later",
    ("wet", "all_day"): "Wet avalanches",
    ("wet", "earlier"): "Wet avalanches, earlier",
    ("wet", "later"): "Wet avalanches, later",
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class RenderModelBuildError(Exception):
    """Raised when a bulletin's render model cannot be built cleanly."""


# ---------------------------------------------------------------------------
# Elevation parsing
# ---------------------------------------------------------------------------


def _parse_elevation(
    elevation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Parse a CAAML elevation dict into a structured elevation object.

    Handles numeric strings, integers, the ``"treeline"`` token, and mixed
    combinations. Returns ``None`` when ``elevation`` is absent or empty.

    Args:
        elevation: Raw CAAML elevation dict with optional ``lowerBound`` /
            ``upperBound`` keys.

    Returns:
        Dict with ``lower`` (int|None), ``upper`` (int|None), and
        ``treeline`` (bool) keys, or ``None`` when no bounds are present.

    """
    if not elevation:
        return None

    lower_raw = elevation.get("lowerBound")
    upper_raw = elevation.get("upperBound")

    # Both absent → no elevation constraint.
    if lower_raw is None and upper_raw is None:
        return None

    def _to_int(value: Any) -> int | None:
        """Convert a bound value to int, or None if not numeric."""
        if value is None:
            return None
        s = str(value)
        if s.isdigit():
            return int(s)
        return None

    treeline = (
        str(lower_raw).lower() == _TREELINE_TOKEN
        or str(upper_raw).lower() == _TREELINE_TOKEN
    )

    return {
        "lower": _to_int(lower_raw),
        "upper": _to_int(upper_raw),
        "treeline": treeline,
    }


# ---------------------------------------------------------------------------
# Danger resolution
# ---------------------------------------------------------------------------


def _resolve_danger(ratings: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Resolve the highest danger level and its subdivision from dangerRatings.

    When multiple ratings share the same highest mainValue the subdivision
    from the last one encountered is used.

    Args:
        ratings: The CAAML ``dangerRatings`` list.

    Returns:
        Dict with ``key`` (str), ``number`` (str), and
        ``subdivision`` (``"+"``, ``"="``, ``"-"``, or None) keys.

    """
    highest = "low"
    raw_subdivision: str = ""

    for rating in ratings:
        value = rating.get("mainValue", "")
        if value not in _DANGER_ORDER:
            continue
        if _DANGER_ORDER.index(value) >= _DANGER_ORDER.index(highest):
            highest = value
            ch_data = (rating.get("customData") or {}).get("CH", {})
            raw_subdivision = ch_data.get("subdivision", "") or ""

    subdivision: str | None = _SUBDIVISION_MAP.get(raw_subdivision, None)

    return {
        "key": highest,
        "number": _DANGER_NUMBER.get(highest, "1"),
        "subdivision": subdivision,
    }


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------


def _detect_source(properties: dict[str, Any]) -> str:
    """
    Detect the bulletin source from CAAML custom data.

    SLF bulletins carry ``customData.CH``; EUREGIO bulletins carry
    ``customData.ALBINA`` or one or more ``customData.LWD_*`` keys.
    Falls back to ``"slf"`` when the source is ambiguous, preserving
    existing behaviour for any bulletin without recognisable markers.

    Args:
        properties: The CAAML properties dict.

    Returns:
        ``"slf"`` or ``"euregio"``.

    """
    custom_data: dict[str, Any] = properties.get("customData") or {}
    if "ALBINA" in custom_data:
        return "euregio"
    for key in custom_data:
        if key.startswith("LWD_"):
            return "euregio"
    # CH key present → SLF; no recognised key → default to SLF.
    return "slf"


# ---------------------------------------------------------------------------
# Source-specific helpers
# ---------------------------------------------------------------------------


def _resolve_aggregations(
    properties: dict[str, Any], source: str
) -> list[dict[str, Any]]:
    """
    Resolve the aggregation list from bulletin properties.

    For SLF bulletins this reads ``customData.CH.aggregation`` verbatim.
    For EUREGIO bulletins it synthesises aggregation entries by grouping
    ``avalancheProblems`` on ``(category, validTimePeriod)``.

    The output shape is the same in both cases:
    ``[{"category": str, "problemTypes": [str], "validTimePeriod": str|None,
       "title": str|None}, ...]``

    Args:
        properties: The CAAML properties dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        A list of aggregation entry dicts.

    """
    if source == "slf":
        return (properties.get("customData") or {}).get("CH", {}).get(
            "aggregation"
        ) or []

    # EUREGIO: synthesise from avalancheProblems.
    # Group on (category, validTimePeriod). Preserve problem order.
    problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for problem in problems:
        pt: str = problem.get("problemType", "")
        vtp: str = problem.get("validTimePeriod") or "all_day"
        category: str = PROBLEM_TYPE_TO_CATEGORY.get(pt, "dry")
        key = (category, vtp)
        if key not in seen:
            seen[key] = {
                "category": category,
                "validTimePeriod": vtp,
                "problemTypes": [],
                "title": None,
            }
            order.append(key)
        entry = seen[key]
        # Avoid duplicates within the same aggregation group.
        if pt not in entry["problemTypes"]:
            entry["problemTypes"].append(pt)

    return [seen[k] for k in order]


def _to_int_safe(val: Any) -> int | None:
    """Convert a raw bound value to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _highest_danger(ratings: list[dict[str, Any]]) -> str | None:
    """Return the highest mainValue string from a list of rating dicts."""
    best: str | None = None
    best_idx = -1
    for rating in ratings:
        val = rating.get("mainValue", "")
        if val in _DANGER_ORDER:
            idx = _DANGER_ORDER.index(val)
            if idx > best_idx:
                best_idx = idx
                best = val
    return best


def _elevation_matches_rating(
    problem_lower: int | None,
    problem_upper: int | None,
    rating_lower: int | None,
    rating_upper: int | None,
) -> bool:
    """Return True when the rating's elevation bounds match the problem's."""
    if rating_lower is not None:
        if problem_lower is None:
            return False
        if problem_lower < rating_lower:
            return False
    if rating_upper is not None:
        if problem_upper is None:
            return False
        if problem_upper > rating_upper:
            return False
    return True


def _filter_ratings_by_elevation(
    matching: list[dict[str, Any]],
    problem_lower: int | None,
    problem_upper: int | None,
) -> list[dict[str, Any]]:
    """
    Partition matching ratings by elevation specificity.

    Returns the most specific candidates (those with elevation bounds), or
    the fallback set (no elevation bounds) when no specific match is found.
    """
    specific: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for rating in matching:
        rating_elev: dict[str, Any] | None = rating.get("elevation") or None
        rating_lower = _to_int_safe((rating_elev or {}).get("lowerBound"))
        rating_upper = _to_int_safe((rating_elev or {}).get("upperBound"))
        has_bound = rating_lower is not None or rating_upper is not None
        if _elevation_matches_rating(
            problem_lower, problem_upper, rating_lower, rating_upper
        ):
            if has_bound:
                specific.append(rating)
            else:
                fallback.append(rating)
    return specific if specific else fallback


def _resolve_problem_rating(
    problem: dict[str, Any],
    danger_ratings: list[dict[str, Any]],
    source: str,
) -> str | None:
    """
    Resolve the danger rating value for a single avalanche problem.

    For SLF bulletins the value is read directly from
    ``problem["dangerRatingValue"]``.

    For EUREGIO bulletins the danger rating is derived by matching the
    problem's elevation and validTimePeriod against the bulletin-level
    ``dangerRatings``.  Matching rules (in order of specificity):

    1. ``validTimePeriod`` must match the problem's period (or the rating
       must have no validTimePeriod, which is treated as ``all_day``).
    2. Elevation: a rating with ``lowerBound`` matches a problem whose
       ``elevation.lowerBound >= lowerBound``; a rating with ``upperBound``
       matches when ``elevation.upperBound <= upperBound``; a rating with no
       bounds matches any elevation.
    3. Most specific wins (a rating with an elevation bound is preferred
       over one with no bound).
    4. Fallback: highest danger value among remaining matching ratings.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        danger_ratings: The bulletin-level ``dangerRatings`` list.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        A danger level string (e.g. ``"moderate"``) or ``None`` when
        no match can be found.

    """
    if source == "slf":
        raw = problem.get("dangerRatingValue")
        return raw if raw else None

    # EUREGIO: match against bulletin-level danger ratings.
    problem_vtp: str = problem.get("validTimePeriod") or "all_day"
    problem_elevation: dict[str, Any] | None = problem.get("elevation") or None
    problem_lower = _to_int_safe((problem_elevation or {}).get("lowerBound"))
    problem_upper = _to_int_safe((problem_elevation or {}).get("upperBound"))

    # Partition ratings by validTimePeriod match.
    matching = [
        r
        for r in danger_ratings
        if (r.get("validTimePeriod") or "all_day") == problem_vtp
    ]
    if not matching:
        matching = list(danger_ratings)

    # Among matching, find the most specific elevation match.
    candidates = _filter_ratings_by_elevation(matching, problem_lower, problem_upper)
    if not candidates:
        candidates = matching

    return _highest_danger(candidates)


def _resolve_problem_comment(problem: dict[str, Any], source: str) -> str:
    """
    Resolve the display comment for a single avalanche problem.

    SLF bulletins carry a per-problem ``comment`` field with HTML prose.
    EUREGIO bulletins carry avalanche activity prose at bulletin level
    (surfaced via ``prose.avalanche_activity``), so per-problem comments
    are returned as empty strings.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        HTML comment string, or empty string when absent or EUREGIO.

    """
    if source == "euregio":
        return ""
    return problem.get("comment") or ""


def _resolve_problem_extras(problem: dict[str, Any], source: str) -> dict[str, Any]:
    """
    Resolve source-specific passthrough fields for a problem card.

    SLF: returns ``{"subdivision": str, "core_zone_text": str|None}`` drawn
    from ``customData.CH``.

    EUREGIO: returns ``{"avalanche_type": str|None}`` drawn from
    ``customData.ALBINA.avalancheType``.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        Source-specific extras dict.

    """
    custom_data: dict[str, Any] = problem.get("customData") or {}
    if source == "slf":
        ch_data: dict[str, Any] = custom_data.get("CH", {})
        return {
            "subdivision": ch_data.get("subdivision", "") or "",
            "core_zone_text": ch_data.get("coreZoneText") or None,
        }
    # EUREGIO
    albina_data: dict[str, Any] = custom_data.get("ALBINA", {})
    return {
        "avalanche_type": albina_data.get("avalancheType") or None,
    }


def _resolve_problem_avalanche_type(problem: dict[str, Any], source: str) -> str | None:
    """
    Resolve the avalanche type (slab/loose) for a problem, if available.

    Only present for EUREGIO bulletins; SLF bulletins always return ``None``.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        ``"slab"``, ``"loose"``, or ``None``.

    """
    if source != "euregio":
        return None
    custom_data: dict[str, Any] = problem.get("customData") or {}
    albina_data: dict[str, Any] = custom_data.get("ALBINA", {})
    return albina_data.get("avalancheType") or None


def _resolve_avalanche_activity(
    properties: dict[str, Any], source: str
) -> dict[str, str]:
    """
    Resolve avalanche activity prose from bulletin properties.

    SLF bulletins do not carry an ``avalancheActivity`` field at the bulletin
    level; returns empty strings for both fields.

    EUREGIO bulletins carry ``avalancheActivity.highlights`` and
    ``avalancheActivity.comment``.

    Args:
        properties: The CAAML properties dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        Dict with ``"highlights"`` and ``"comment"`` string fields.

    """
    if source == "slf":
        return {"highlights": "", "comment": ""}
    activity: dict[str, Any] = properties.get("avalancheActivity") or {}
    return {
        "highlights": activity.get("highlights") or "",
        "comment": activity.get("comment") or "",
    }


def _resolve_danger_patterns(properties: dict[str, Any], source: str) -> list[str]:
    """
    Resolve danger patterns from bulletin custom data.

    SLF bulletins do not carry danger patterns; returns an empty list.

    EUREGIO bulletins may carry ``customData.LWD_Tyrol.dangerPatterns``.
    Other ``LWD_*`` keys are searched when ``LWD_Tyrol`` is absent.

    Args:
        properties: The CAAML properties dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        List of danger pattern strings, e.g. ``["DP10", "DP1"]``, or ``[]``.

    """
    if source == "slf":
        return []
    custom_data: dict[str, Any] = properties.get("customData") or {}
    # Prefer LWD_Tyrol; fall back to any other LWD_* key.
    lwd_data = custom_data.get("LWD_Tyrol") or {}
    if not lwd_data:
        for key, value in custom_data.items():
            if key.startswith("LWD_") and value:
                lwd_data = value
                break
    patterns = lwd_data.get("dangerPatterns") or []
    return [str(p) for p in patterns]


# ---------------------------------------------------------------------------
# Problem builder (source-aware)
# ---------------------------------------------------------------------------


def _build_problem(
    problem: dict[str, Any],
    danger_ratings: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    """
    Convert a raw CAAML avalanche problem into the render model shape.

    Args:
        problem: A single raw avalanche problem dict from CAAML.
        danger_ratings: Bulletin-level danger ratings (used for EUREGIO
            to derive per-problem danger rating values).
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        A rendered problem dict suitable for the render model.

    """
    elevation = _parse_elevation(problem.get("elevation") or None)
    aspects: list[str] = problem.get("aspects") or []
    comment_html: str = _resolve_problem_comment(problem, source)
    core_zone_text: str | None = (problem.get("customData") or {}).get("CH", {}).get(
        "coreZoneText"
    ) or None
    danger_rating_value: str | None = _resolve_problem_rating(
        problem, danger_ratings, source
    )
    avalanche_type: str | None = _resolve_problem_avalanche_type(problem, source)
    extras: dict[str, Any] = _resolve_problem_extras(problem, source)

    return {
        "problem_type": problem.get("problemType", ""),
        "time_period": problem.get("validTimePeriod", ""),
        "elevation": elevation,
        "aspects": aspects,
        "comment_html": comment_html,
        "core_zone_text": core_zone_text,
        "danger_rating_value": danger_rating_value,
        "avalanche_type": avalanche_type,
        "extras": extras,
    }


def _is_prose_only(matched_problems: list[dict[str, Any]]) -> bool:
    """
    Return True when all matched problems have no aspects AND no elevation.

    When this is the case the geographic scope is described only in prose
    so the trait's geography source is ``"prose_only"``.

    Args:
        matched_problems: Problems matched to this aggregation entry.

    Returns:
        True if geography should be sourced from prose.

    """
    for problem in matched_problems:
        aspects = problem.get("aspects") or []
        elevation = problem.get("elevation")
        if aspects or elevation:
            return False
    return True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_problems(avalanche_problems: list[dict[str, Any]]) -> None:
    """
    Validate each avalanche problem's type and validTimePeriod.

    Args:
        avalanche_problems: The ``avalancheProblems`` list from CAAML properties.

    Raises:
        RenderModelBuildError: On unknown problemType or validTimePeriod.

    """
    for problem in avalanche_problems:
        pt = problem.get("problemType", "")
        if pt not in KNOWN_PROBLEM_TYPES:
            raise RenderModelBuildError(
                f"Unknown problemType in avalancheProblems: {pt!r}. "
                f"Known types: {sorted(KNOWN_PROBLEM_TYPES)}"
            )
        vtp = problem.get("validTimePeriod")
        if vtp is not None and vtp not in _VALID_TIME_PERIODS:
            raise RenderModelBuildError(
                f"Unknown validTimePeriod on problem {pt!r}: {vtp!r}. "
                f"Valid values: {sorted(_VALID_TIME_PERIODS)}"
            )


def _validate_aggregation(aggregation: list[dict[str, Any]]) -> None:
    """
    Validate each aggregation entry's structure.

    Args:
        aggregation: The resolved aggregation list (from either the SLF
            ``customData.CH.aggregation`` field or the synthesised EUREGIO list).

    Raises:
        RenderModelBuildError: On structural anomalies in any entry.

    """
    for entry in aggregation:
        entry_types: list[str] = entry.get("problemTypes") or []
        if not entry_types:
            raise RenderModelBuildError(
                "Aggregation entry has empty problemTypes list."
            )
        category = entry.get("category")
        if not category or category not in {"dry", "wet"}:
            raise RenderModelBuildError(
                f"Aggregation entry has missing or unknown category: {category!r}."
            )
        vtp = entry.get("validTimePeriod")
        if vtp is not None and vtp not in _VALID_TIME_PERIODS:
            raise RenderModelBuildError(
                f"Aggregation entry has unknown validTimePeriod: {vtp!r}. "
                f"Valid values: {sorted(_VALID_TIME_PERIODS)}"
            )
        for pt in entry_types:
            if pt not in KNOWN_PROBLEM_TYPES:
                raise RenderModelBuildError(
                    f"Unknown problemType in aggregation entry: {pt!r}. "
                    f"Known types: {sorted(KNOWN_PROBLEM_TYPES)}"
                )


def _validate(
    avalanche_problems: list[dict[str, Any]],
    aggregation: list[dict[str, Any]],
) -> None:
    """
    Validate the consistency of avalanche problems and aggregation entries.

    Delegates per-list validation to ``_validate_problems`` and
    ``_validate_aggregation``, then performs cross-list consistency checks.

    Args:
        avalanche_problems: The ``avalancheProblems`` list from CAAML properties.
        aggregation: The resolved aggregation list.

    Raises:
        RenderModelBuildError: On any of the fail-hard conditions described
            in the module docstring.

    """
    _validate_problems(avalanche_problems)
    _validate_aggregation(aggregation)

    # Cross-check: problem types in avalancheProblems must exactly match
    # the flattened set of problemTypes across aggregation entries.
    if avalanche_problems or aggregation:
        problem_set = {p["problemType"] for p in avalanche_problems}
        agg_set: set[str] = set()
        for entry in aggregation:
            agg_set.update(entry.get("problemTypes") or [])
        if problem_set != agg_set:
            raise RenderModelBuildError(
                f"Problem type mismatch: avalancheProblems contains "
                f"{sorted(problem_set)!r} but aggregation references "
                f"{sorted(agg_set)!r}."
            )


# ---------------------------------------------------------------------------
# Trait builder
# ---------------------------------------------------------------------------


def _build_trait(
    aggregation_entry: dict[str, Any],
    problems_by_type: dict[str, dict[str, Any]],
    danger_ratings: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    """
    Build a single trait dict from an aggregation entry and a problem lookup.

    Problems are iterated in the order specified by the aggregation entry's
    ``problemTypes`` list, preserving the editorial ordering.

    Args:
        aggregation_entry: A single aggregation entry with ``category``,
            ``validTimePeriod``, ``problemTypes``, and optionally ``title``.
        problems_by_type: Lookup dict mapping problemType → raw problem dict.
        danger_ratings: Bulletin-level danger ratings (passed to problem builder).
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        A trait dict in the render model shape.

    """
    category: str = aggregation_entry["category"]
    time_period: str = aggregation_entry.get("validTimePeriod") or "all_day"
    raw_title: str = aggregation_entry.get("title") or ""
    title: str = (
        raw_title
        if raw_title
        else _TITLE_FALLBACK.get(
            (category, time_period), f"{category.capitalize()} avalanches"
        )
    )

    problem_types_ordered: list[str] = aggregation_entry["problemTypes"]

    matched_raw: list[dict[str, Any]] = []
    for pt in problem_types_ordered:
        # Defensive: validation already guarantees pt is in problems_by_type,
        # but assert to catch any future divergence.
        assert pt in problems_by_type, (  # noqa: S101 — post-validation defensive check
            f"Problem type {pt!r} not found in problems_by_type after validation."
        )
        matched_raw.append(problems_by_type[pt])

    built_problems = [_build_problem(p, danger_ratings, source) for p in matched_raw]

    # Determine danger level as max across member problems.
    danger_level = 1
    for p in matched_raw:
        drv = _resolve_problem_rating(p, danger_ratings, source) or ""
        if drv in _DANGER_ORDER:
            candidate = int(_DANGER_NUMBER.get(drv, "1"))
            if candidate > danger_level:
                danger_level = candidate

    # Determine geography source.
    prose: str | None = None
    if matched_raw and _is_prose_only(matched_raw):
        geography_source = "prose_only"
        # Join all problem comments for multi-problem prose-only traits.
        prose_parts = [p.get("comment") or "" for p in matched_raw if p.get("comment")]
        if not prose_parts:
            prose = None
        elif len(prose_parts) == 1:
            prose = prose_parts[0]
        else:
            prose = " ".join(prose_parts)
    else:
        geography_source = "problems"

    return {
        "category": category,
        "time_period": time_period,
        "title": title,
        "geography": {"source": geography_source},
        "problems": built_problems,
        "prose": prose,
        "danger_level": danger_level,
    }


# ---------------------------------------------------------------------------
# Public builder — secondary helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def _parse_iso_timestamp(value: Any) -> str | None:
    """
    Parse a raw timestamp value into a canonical ISO 8601 string.

    Accepts strings in common ISO 8601 / RFC 3339 formats (with or without
    trailing ``Z``). Any parse failure returns ``None`` — timestamps are
    display data and should never block rendering.

    Args:
        value: The raw timestamp value from CAAML properties.

    Returns:
        A canonical ISO 8601 string (UTC, with timezone offset) or ``None``.

    """
    if not value:
        return None
    if not isinstance(value, str):
        return None
    try:
        # Replace trailing Z with +00:00 for Python < 3.11 compatibility.
        normalised = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        # Attach UTC if no tzinfo was present.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Metadata builder
# ---------------------------------------------------------------------------


def _build_metadata(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Extract bulletin metadata from CAAML properties.

    Reads ``publicationTime``, ``validTime.startTime``, ``validTime.endTime``,
    ``nextUpdate``, ``unscheduled``, and ``lang``. Missing or unparseable
    timestamps yield ``None``. Missing ``unscheduled`` defaults to ``False``;
    missing ``lang`` defaults to ``"en"``.

    Args:
        properties: The CAAML properties dict.

    Returns:
        A metadata dict with six keys: ``publication_time``, ``valid_from``,
        ``valid_until``, ``next_update``, ``unscheduled``, and ``lang``.

    """
    valid_time: dict[str, Any] = properties.get("validTime") or {}
    return {
        "publication_time": _parse_iso_timestamp(properties.get("publicationTime")),
        "valid_from": _parse_iso_timestamp(valid_time.get("startTime")),
        "valid_until": _parse_iso_timestamp(valid_time.get("endTime")),
        "next_update": _parse_iso_timestamp(properties.get("nextUpdate")),
        "unscheduled": bool(properties.get("unscheduled", False)),
        "lang": properties.get("lang") or "en",
    }


# ---------------------------------------------------------------------------
# Prose builder
# ---------------------------------------------------------------------------


def _build_prose(properties: dict[str, Any], source: str = "slf") -> dict[str, Any]:
    """
    Extract prose sections from CAAML properties.

    Reads ``snowpackStructure.comment``, ``weatherReview.comment``,
    ``weatherForecast.comment``, the ``tendency`` array, and (for EUREGIO)
    ``avalancheActivity``. Each tendency entry captures ``comment``,
    ``tendency_type`` (from ``tendencyType``), ``valid_from``, and
    ``valid_until`` (from the entry's ``validTime``).
    Missing or empty tendency array → ``[]``. Missing scalar prose → ``None``.
    ``avalanche_activity`` is always present; empty strings for SLF.

    Args:
        properties: The CAAML properties dict.
        source: ``"slf"`` or ``"euregio"``.

    Returns:
        A prose dict with ``snowpack_structure``, ``weather_review``,
        ``weather_forecast``, ``tendency``, and ``avalanche_activity`` keys.

    """
    snowpack_structure: str | None = (properties.get("snowpackStructure") or {}).get(
        "comment"
    ) or None

    weather_review: str | None = (properties.get("weatherReview") or {}).get(
        "comment"
    ) or None

    weather_forecast: str | None = (properties.get("weatherForecast") or {}).get(
        "comment"
    ) or None

    raw_tendency: list[dict[str, Any]] = properties.get("tendency") or []
    tendency: list[dict[str, Any]] = []
    for entry in raw_tendency:
        entry_valid_time: dict[str, Any] = entry.get("validTime") or {}
        tendency.append(
            {
                "comment": entry.get("comment") or "",
                "tendency_type": entry.get("tendencyType") or None,
                "valid_from": _parse_iso_timestamp(entry_valid_time.get("startTime")),
                "valid_until": _parse_iso_timestamp(entry_valid_time.get("endTime")),
            }
        )

    avalanche_activity = _resolve_avalanche_activity(properties, source)

    return {
        "snowpack_structure": snowpack_structure,
        "weather_review": weather_review,
        "weather_forecast": weather_forecast,
        "tendency": tendency,
        "avalanche_activity": avalanche_activity,
    }


# ---------------------------------------------------------------------------
# Trait list builder (extracted to keep build_render_model complexity low)
# ---------------------------------------------------------------------------


def _build_euregio_traits(
    aggregation: list[dict[str, Any]],
    avalanche_problems: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build traits for EUREGIO bulletins using a per-(type, vtp) problem lookup.

    The same problem type can appear in multiple validTimePeriods in EUREGIO
    bulletins, so each aggregation entry is resolved against the subset of
    problems that match its validTimePeriod.

    Args:
        aggregation: The synthesised EUREGIO aggregation list.
        avalanche_problems: The raw ``avalancheProblems`` list.
        ratings: Bulletin-level danger ratings.

    Returns:
        Flat list of trait dicts in aggregation order.

    """
    problems_by_type_vtp: dict[tuple[str, str], dict[str, Any]] = {}
    for p in avalanche_problems:
        pt = p.get("problemType", "")
        pvtp = p.get("validTimePeriod") or "all_day"
        problems_by_type_vtp[(pt, pvtp)] = p

    traits: list[dict[str, Any]] = []
    for entry in aggregation:
        entry_vtp = entry.get("validTimePeriod") or "all_day"
        entry_problems: dict[str, dict[str, Any]] = {}
        for pt in entry.get("problemTypes") or []:
            if (pt, entry_vtp) in problems_by_type_vtp:
                entry_problems[pt] = problems_by_type_vtp[(pt, entry_vtp)]
            else:
                # Fallback: first occurrence of this problem type.
                for ap in avalanche_problems:
                    if ap.get("problemType") == pt:
                        entry_problems[pt] = ap
                        break
        traits.append(_build_trait(entry, entry_problems, ratings, "euregio"))
    return traits


def _build_traits(
    aggregation: list[dict[str, Any]],
    avalanche_problems: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
    source: str,
    bulletin_id: str,
) -> list[dict[str, Any]]:
    """
    Build the complete traits list from aggregation entries and problems.

    For EUREGIO bulletins the same problem type may appear in multiple
    validTimePeriods, so a per-(type, vtp) lookup is used to ensure each
    aggregation group resolves to the correct problem instance.

    For SLF bulletins a simpler type-keyed lookup suffices.

    Args:
        aggregation: The resolved aggregation entry list.
        avalanche_problems: The raw ``avalancheProblems`` list.
        ratings: Bulletin-level danger ratings.
        source: ``"slf"`` or ``"euregio"``.
        bulletin_id: Used in warning log messages.

    Returns:
        Flat list of trait dicts in aggregation order.

    """
    traits: list[dict[str, Any]] = []

    if source == "euregio":
        traits = _build_euregio_traits(aggregation, avalanche_problems, ratings)
    else:
        # SLF: problem type uniquely identifies a problem row.
        problems_by_type: dict[str, dict[str, Any]] = {
            p["problemType"]: p for p in avalanche_problems
        }
        for entry in aggregation:
            traits.append(_build_trait(entry, problems_by_type, ratings, source))

    if len(traits) > 2:
        logger.warning(
            "Bulletin %s produced %d traits — may have extended the editorial model",
            bulletin_id,
            len(traits),
        )
    return traits


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_render_model(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Build a versioned render model dict from raw CAAML bulletin properties.

    This is a pure function: no Django imports, no I/O, no side effects.

    Raises ``RenderModelBuildError`` when the data shape violates the
    canonical EAWS problem-type enum or structural invariants. The caller
    is responsible for catching this and storing an error sentinel.

    Supports both SLF and EUREGIO (ALBINA) bulletin formats. The source
    is detected automatically via ``_detect_source()`` and stamped in the
    output as ``render_model["source"]``.

    Args:
        properties: The CAAML properties dict (the ``"properties"`` key from
            the GeoJSON Feature envelope stored in ``Bulletin.raw_data``).

    Returns:
        A render model dict ready for storage in ``Bulletin.render_model``.

    Raises:
        RenderModelBuildError: When the bulletin data cannot be cleanly
            mapped to the render model shape.

    """
    bulletin_id: str = properties.get("bulletinID", "<unknown>")

    source = _detect_source(properties)

    ratings: list[dict[str, Any]] = properties.get("dangerRatings") or []
    danger = _resolve_danger(ratings)

    avalanche_problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []
    aggregation: list[dict[str, Any]] = _resolve_aggregations(properties, source)

    # For SLF bulletins: aggregation is expected to always be present when
    # avalancheProblems is non-empty. Log an error and produce empty traits
    # if missing — do not synthesise, as this indicates an upstream data gap.
    if source == "slf" and avalanche_problems and not aggregation:
        logger.error(
            "Bulletin %s has avalancheProblems but no customData.CH.aggregation; "
            "cannot build traits. Bulletin will render with no problem cards.",
            bulletin_id,
        )
        avalanche_problems = []
        aggregation = []

    # Validate — raises RenderModelBuildError on failure.
    _validate(avalanche_problems, aggregation)

    # Both lists empty → quiet day, no traits.
    traits: list[dict[str, Any]] = []

    if aggregation:
        traits = _build_traits(
            aggregation, avalanche_problems, ratings, source, bulletin_id
        )

    prose = _build_prose(properties, source)
    metadata = _build_metadata(properties)
    danger_patterns = _resolve_danger_patterns(properties, source)

    # Keep top-level snowpack_structure for v2 back-compat (equals prose copy).
    snowpack_structure: str | None = prose["snowpack_structure"]

    return {
        "version": RENDER_MODEL_VERSION,
        "source": source,
        "danger": danger,
        "traits": traits,
        "snowpack_structure": snowpack_structure,
        "metadata": metadata,
        "prose": prose,
        "danger_patterns": danger_patterns,
    }


# ---------------------------------------------------------------------------
# Day character
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DayCharacter:
    """
    Pair of label + one-line explainer for the day-character eyebrow.

    The label is one of the five canonical strings from the day-character
    cascade; the explainer is a fixed one-liner that frames the label for
    a non-expert reader. Both fields hold ``gettext_lazy`` proxies so the
    active locale resolves them at render time.
    """

    label: StrOrPromise
    explainer: StrOrPromise


_DAY_CHARACTER: dict[str, DayCharacter] = {
    "stable": DayCharacter(
        label=_("Stable day"),
        explainer=_("Low danger and benign problems — manage as usual."),
    ),
    "manageable": DayCharacter(
        label=_("Manageable day"),
        explainer=_("Moderate to considerable danger — read the terrain carefully."),
    ),
    "hard_to_read": DayCharacter(
        label=_("Hard-to-read day"),
        explainer=_("Persistent or gliding-snow problems can mask the real risk."),
    ),
    "widespread": DayCharacter(
        label=_("Widespread danger"),
        explainer=_(
            "Considerable danger across many aspects, elevations, or problems."
        ),
    ),
    "dangerous": DayCharacter(
        label=_("Dangerous conditions"),
        explainer=_(
            "High to very high danger — backcountry travel is not recommended."
        ),
    ),
}


def _elevation_lower_le_2000(elevation: Any) -> bool:
    """
    Return True if the render model elevation's lower bound is at or below 2000m.

    Accepts the render model elevation dict (with ``lower`` int|None key),
    not the raw CAAML or ElevationBounds object used in views.

    Args:
        elevation: Render model elevation dict or None.

    Returns:
        True when lower bound is present, numeric, and <= 2000.

    """
    if not elevation or not isinstance(elevation, dict):
        return False
    lower = elevation.get("lower")
    return lower is not None and isinstance(lower, int) and lower <= 2000


def _is_widespread(problems: list[dict[str, Any]]) -> bool:
    """
    Return True if the flattened problems indicate widespread exposure (rule 3).

    Checks three conditions: total unique aspects >= 6, any problem with a
    lower elevation bound <= 2000m, or two or more problems present.

    Args:
        problems: Flattened list of render model problem dicts.

    Returns:
        True when exposure is widespread.

    """
    all_aspects: set[str] = set()
    for p in problems:
        all_aspects.update(p.get("aspects") or [])
    has_low_elevation = any(
        _elevation_lower_le_2000(p.get("elevation")) for p in problems
    )
    return len(all_aspects) >= 6 or has_low_elevation or len(problems) >= 2


def _is_stable(danger: int, problems: list[dict[str, Any]]) -> bool:
    """
    Return True if the day qualifies as stable (rule 5).

    Stable when danger is 1, or danger is 2 with only benign problems.

    Args:
        danger: Numeric danger level (1–5).
        problems: Flattened list of render model problem dicts.

    Returns:
        True when the day is stable.

    """
    if danger == 1:
        return True
    return danger == 2 and all(
        p.get("problem_type") == "no_distinct_avalanche_problem" for p in problems
    )


def compute_day_character(render_model: dict[str, Any]) -> DayCharacter:
    """
    Classify a render model into one of five day-character entries.

    Rules are evaluated top-to-bottom; the first match wins. Uses the
    five-rule cascade from docs/day_character_rules_spec.md.

    When ``traits`` is empty (no avalanche problems reported), returns
    the ``"Stable day"`` entry immediately.

    This function is pure — no side effects, no database access.

    Args:
        render_model: A render model dict as produced by
            :func:`build_render_model`.

    Returns:
        A :class:`DayCharacter` carrying both the canonical label
        (``"Stable day"``, ``"Manageable day"``, ``"Hard-to-read day"``,
        ``"Widespread danger"``, or ``"Dangerous conditions"``) and a
        one-line explainer for the eyebrow on the bulletin page.

    """
    danger_info = render_model.get("danger") or {}
    danger = int(danger_info.get("number") or 1)
    subdivision: str = danger_info.get("subdivision") or ""

    # Flatten all problems across all traits for rule evaluation.
    traits: list[dict[str, Any]] = render_model.get("traits") or []

    # Empty traits → quiet day, no problems to trigger any rule.
    if not traits:
        return _DAY_CHARACTER["stable"]

    problems: list[dict[str, Any]] = [
        p for trait in traits for p in (trait.get("problems") or [])
    ]

    # Rule 1 — Dangerous conditions: danger >= 4
    if danger >= 4:
        return _DAY_CHARACTER["dangerous"]

    # Rule 2 — Hard-to-read day: danger >= 2 and any hard-to-read problem
    if danger >= 2 and any(
        p.get("problem_type") in _HARD_TO_READ_PROBLEMS for p in problems
    ):
        return _DAY_CHARACTER["hard_to_read"]

    # Rule 3 — Widespread danger: danger == 3 and broad exposure
    if danger == 3 and _is_widespread(problems):
        return _DAY_CHARACTER["widespread"]

    # Rule 3b — Widespread danger: danger == 3 and upper subdivision (3+)
    if danger == 3 and subdivision == "+":
        return _DAY_CHARACTER["widespread"]

    # Rule 5 — Stable day
    if _is_stable(danger, problems):
        return _DAY_CHARACTER["stable"]

    # Rule 4 — Manageable day: danger 2 or 3 with no earlier match
    if danger in {2, 3}:
        return _DAY_CHARACTER["manageable"]

    # Safe default
    return _DAY_CHARACTER["stable"]
