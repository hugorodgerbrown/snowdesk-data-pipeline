"""
pipeline/services/render_model.py — Render model builder for SLF avalanche bulletins.

Converts the raw CAAML properties dict stored in Bulletin.raw_data into a
versioned, presentation-ready ``render_model`` dict. The render model is a
stable, flattened representation that views consume directly, avoiding
repeated re-derivation of the same computed values.

The version constant ``RENDER_MODEL_VERSION`` must be incremented whenever
the output shape or logic changes so that existing rows can be detected as
stale and rebuilt via the ``rebuild_render_models`` management command.

Also provides ``compute_day_character``, a pure function that classifies a
render_model into one of five day-character labels using the five-rule cascade
defined in docs/day_character_rules_spec.md.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

RENDER_MODEL_VERSION: int = 1

# ---------------------------------------------------------------------------
# Constants
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
# Problem matching
# ---------------------------------------------------------------------------


def _match_problems(
    aggregation_entry: dict[str, Any],
    all_problems: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Match raw avalanche problems to a single aggregation entry.

    Matching criteria: the problem's ``problemType`` must be in the
    aggregation entry's ``problemTypes`` list AND its ``validTimePeriod``
    must equal the aggregation entry's ``validTimePeriod``. This handles
    dry/wet disambiguation when the same problem type appears in both.

    Args:
        aggregation_entry: A single entry from
            ``customData.CH.aggregation``.
        all_problems: The full ``avalancheProblems`` list from CAAML
            properties.

    Returns:
        A list of matched problem dicts.

    """
    problem_types: set[str] = set(aggregation_entry.get("problemTypes") or [])
    time_period: str = aggregation_entry.get("validTimePeriod", "")

    matched: list[dict[str, Any]] = []
    for problem in all_problems:
        if problem.get("problemType") in problem_types:
            if problem.get("validTimePeriod", "") == time_period:
                matched.append(problem)
    return matched


def _build_problem(problem: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a raw CAAML avalanche problem into the render model shape.

    Args:
        problem: A single raw avalanche problem dict from CAAML.

    Returns:
        A rendered problem dict suitable for the render model.

    """
    elevation = _parse_elevation(problem.get("elevation") or None)
    aspects: list[str] = problem.get("aspects") or []
    comment_html: str = problem.get("comment") or ""
    core_zone_text: str | None = (problem.get("customData") or {}).get("CH", {}).get(
        "coreZoneText"
    ) or None
    danger_rating_value: str | None = problem.get("dangerRatingValue") or None

    return {
        "problem_type": problem.get("problemType", ""),
        "time_period": problem.get("validTimePeriod", ""),
        "elevation": elevation,
        "aspects": aspects,
        "comment_html": comment_html,
        "core_zone_text": core_zone_text,
        "danger_rating_value": danger_rating_value,
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
# Trait builder
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, str] = {
    "dry": "dry",
    "wet": "wet",
}

_VALID_TIME_PERIOD_MAP: dict[str, str] = {
    "all_day": "all_day",
    "earlier": "earlier",
    "later": "later",
}


def _build_trait(
    aggregation_entry: dict[str, Any],
    matched_problems: list[dict[str, Any]],
    danger_level: int,
) -> dict[str, Any]:
    """
    Build a single trait dict from an aggregation entry and its matched problems.

    Args:
        aggregation_entry: A single entry from ``customData.CH.aggregation``.
        matched_problems: Problems matched to this aggregation entry.
        danger_level: Numeric danger level (1–5) for this trait.

    Returns:
        A trait dict in the render model shape.

    """
    category: str = _CATEGORY_MAP.get(aggregation_entry.get("category", "dry"), "dry")
    time_period: str = _VALID_TIME_PERIOD_MAP.get(
        aggregation_entry.get("validTimePeriod", "all_day"), "all_day"
    )
    title: str = aggregation_entry.get("title", "") or ""

    built_problems = [_build_problem(p) for p in matched_problems]

    # Determine geography source.
    prose: str | None = None
    if matched_problems and _is_prose_only(matched_problems):
        geography_source = "prose_only"
        # Use the first problem's comment as prose.
        prose = matched_problems[0].get("comment") or None
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
# Public builder
# ---------------------------------------------------------------------------


def _build_fallback_key_message(
    properties: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Derive the fallback key message from the bulletin properties.

    Checks in priority order: first problem comment, snowpack structure
    comment, weather review comment.

    Args:
        properties: The CAAML properties dict.

    Returns:
        A dict with ``text`` and ``source`` keys, or None if no text found.

    """
    ap = properties.get("avalancheProblems") or []
    if ap:
        comment = ap[0].get("comment") or ""
        if comment:
            return {"text": comment, "source": "avalancheProblems[0].comment"}

    snowpack_comment = (properties.get("snowpackStructure") or {}).get("comment") or ""
    if snowpack_comment:
        return {"text": snowpack_comment, "source": "snowpackStructure.comment"}

    weather_comment = (properties.get("weatherReview") or {}).get("comment") or ""
    if weather_comment:
        return {"text": weather_comment, "source": "weatherReview.comment"}

    return None


def _build_traits(
    aggregation: list[dict[str, Any]],
    all_problems: list[dict[str, Any]],
    danger_level: int,
) -> list[dict[str, Any]]:
    """
    Build the list of traits from aggregation entries and problems.

    Falls back to a synthetic dry/all_day trait when aggregation is empty.

    Args:
        aggregation: The ``customData.CH.aggregation`` list.
        all_problems: The full ``avalancheProblems`` list.
        danger_level: Numeric danger level (1–5).

    Returns:
        A list of trait dicts.

    """
    if aggregation:
        return [
            _build_trait(entry, _match_problems(entry, all_problems), danger_level)
            for entry in aggregation
        ]

    # No aggregation — synthesise a single dry/all_day trait.
    logger.warning(
        "Bulletin properties missing customData.CH.aggregation; "
        "synthesising fallback trait with all %d problems",
        len(all_problems),
    )
    return [
        {
            "category": "dry",
            "time_period": "all_day",
            "title": "",
            "geography": {"source": "problems"},
            "problems": [_build_problem(p) for p in all_problems],
            "prose": None,
            "danger_level": danger_level,
        }
    ]


def build_render_model(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Build a versioned render model dict from raw CAAML bulletin properties.

    This is a pure function: no Django imports, no I/O, no side effects.

    Args:
        properties: The CAAML properties dict (the ``"properties"`` key from
            the GeoJSON Feature envelope stored in ``Bulletin.raw_data``).

    Returns:
        A render model dict ready for storage in ``Bulletin.render_model``.

    """
    ratings: list[dict[str, Any]] = properties.get("dangerRatings") or []
    danger = _resolve_danger(ratings)
    danger_level = int(danger["number"])

    all_problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []

    aggregation: list[dict[str, Any]] = (properties.get("customData") or {}).get(
        "CH", {}
    ).get("aggregation") or []

    traits = _build_traits(aggregation, all_problems, danger_level)
    fallback_key_message = _build_fallback_key_message(properties)
    snowpack_structure: str | None = (properties.get("snowpackStructure") or {}).get(
        "comment"
    ) or None

    return {
        "version": RENDER_MODEL_VERSION,
        "danger": danger,
        "traits": traits,
        "fallback_key_message": fallback_key_message,
        "snowpack_structure": snowpack_structure,
    }


# ---------------------------------------------------------------------------
# Day character
# ---------------------------------------------------------------------------


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


def compute_day_character(render_model: dict[str, Any]) -> str:
    """
    Classify a render model into one of five day-character labels.

    Rules are evaluated top-to-bottom; the first match wins. Uses the
    five-rule cascade from docs/day_character_rules_spec.md. This function
    is pure — no side effects, no database access.

    Args:
        render_model: A render model dict as produced by
            :func:`build_render_model`.

    Returns:
        One of ``"Stable day"``, ``"Manageable day"``,
        ``"Hard-to-read day"``, ``"Widespread danger"``, or
        ``"Dangerous conditions"``.

    """
    danger_info = render_model.get("danger") or {}
    danger = int(danger_info.get("number") or 1)
    subdivision: str = danger_info.get("subdivision") or ""

    # Flatten all problems across all traits for rule evaluation.
    traits: list[dict[str, Any]] = render_model.get("traits") or []
    problems: list[dict[str, Any]] = [
        p for trait in traits for p in (trait.get("problems") or [])
    ]

    # Rule 1 — Dangerous conditions: danger >= 4
    if danger >= 4:
        return "Dangerous conditions"

    # Rule 2 — Hard-to-read day: danger >= 2 and any hard-to-read problem
    if danger >= 2 and any(
        p.get("problem_type") in _HARD_TO_READ_PROBLEMS for p in problems
    ):
        return "Hard-to-read day"

    # Rule 3 — Widespread danger: danger == 3 and broad exposure
    if danger == 3 and _is_widespread(problems):
        return "Widespread danger"

    # Rule 3b — Widespread danger: danger == 3 and upper subdivision (3+)
    if danger == 3 and subdivision == "+":
        return "Widespread danger"

    # Rule 5 — Stable day
    if _is_stable(danger, problems):
        return "Stable day"

    # Rule 4 — Manageable day: danger 2 or 3 with no earlier match
    if danger in {2, 3}:
        return "Manageable day"

    # Safe default
    return "Stable day"
