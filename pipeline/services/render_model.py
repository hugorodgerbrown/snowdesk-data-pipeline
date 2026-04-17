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
  - **Missing aggregation no longer raises.** When ``customData.CH.aggregation``
    is absent but ``avalancheProblems`` is non-empty (a real-world SLF
    quirk), aggregation is synthesised from the problems by grouping on
    ``(category, validTimePeriod)``. Per the CAAML schema, aggregation
    is a visualisation hint and dry/wet problem types are disjoint, so
    the synthesis is unambiguous. A warning is logged so operators can
    track the upstream gap. Output shape is unchanged — no version bump.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

RENDER_MODEL_VERSION: int = 3

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
# Problem builder
# ---------------------------------------------------------------------------


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
        aggregation: The ``customData.CH.aggregation`` list.

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
        aggregation: The ``customData.CH.aggregation`` list.

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
# Aggregation synthesis (fallback when SLF omits customData.CH.aggregation)
# ---------------------------------------------------------------------------


def _synthesise_aggregation(
    avalanche_problems: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build aggregation entries from avalancheProblems alone.

    Used when SLF has not provided ``customData.CH.aggregation`` but the
    bulletin still carries problems. Groups problems by
    ``(category, validTimePeriod)``, preserving SLF's problem ordering
    within and across groups. Category is resolved via
    ``PROBLEM_TYPE_TO_CATEGORY`` — dry/wet problem types are disjoint so
    the grouping is unambiguous.

    Caller must run ``_validate_problems`` first; this helper assumes
    every ``problemType`` is a known EAWS token.

    Args:
        avalanche_problems: Raw CAAML avalancheProblems list.

    Returns:
        A list of aggregation-entry dicts in the same shape as
        ``customData.CH.aggregation``.

    """
    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for problem in avalanche_problems:
        pt = problem.get("problemType", "")
        category = PROBLEM_TYPE_TO_CATEGORY[pt]
        time_period = problem.get("validTimePeriod") or "all_day"
        key = (category, time_period)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(pt)

    return [
        {
            "category": category,
            "validTimePeriod": time_period,
            "problemTypes": groups[(category, time_period)],
        }
        for category, time_period in order
    ]


# ---------------------------------------------------------------------------
# Trait builder
# ---------------------------------------------------------------------------


def _build_trait(
    aggregation_entry: dict[str, Any],
    problems_by_type: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Build a single trait dict from an aggregation entry and a problem lookup.

    Problems are iterated in the order specified by the aggregation entry's
    ``problemTypes`` list, preserving SLF's editorial ordering.

    Args:
        aggregation_entry: A single entry from ``customData.CH.aggregation``.
        problems_by_type: Lookup dict mapping problemType → raw problem dict.

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

    built_problems = [_build_problem(p) for p in matched_raw]

    # Determine danger level as max across member problems.
    danger_level = 1
    for p in matched_raw:
        drv = p.get("dangerRatingValue") or ""
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


def _build_prose(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Extract prose sections from CAAML properties.

    Reads ``snowpackStructure.comment``, ``weatherReview.comment``,
    ``weatherForecast.comment``, and the ``tendency`` array. Each tendency
    entry captures ``comment``, ``tendency_type`` (from ``tendencyType``),
    ``valid_from``, and ``valid_until`` (from the entry's ``validTime``).
    Missing or empty tendency array → ``[]``. Missing scalar prose → ``None``.

    Args:
        properties: The CAAML properties dict.

    Returns:
        A prose dict with ``snowpack_structure``, ``weather_review``,
        ``weather_forecast``, and ``tendency`` keys.

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

    return {
        "snowpack_structure": snowpack_structure,
        "weather_review": weather_review,
        "weather_forecast": weather_forecast,
        "tendency": tendency,
    }


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

    ratings: list[dict[str, Any]] = properties.get("dangerRatings") or []
    danger = _resolve_danger(ratings)

    avalanche_problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []
    aggregation: list[dict[str, Any]] = (properties.get("customData") or {}).get(
        "CH", {}
    ).get("aggregation") or []

    # SLF occasionally omits the aggregation hint; rebuild it from the
    # problem types so the bulletin still renders. Validate problems first
    # so synthesis only sees known EAWS types.
    if avalanche_problems and not aggregation:
        _validate_problems(avalanche_problems)
        logger.warning(
            "Bulletin %s has avalancheProblems but no customData.CH.aggregation; "
            "synthesising aggregation from problem types.",
            bulletin_id,
        )
        aggregation = _synthesise_aggregation(avalanche_problems)

    # Validate — raises RenderModelBuildError on failure.
    _validate(avalanche_problems, aggregation)

    # Both lists empty → quiet day, no traits.
    traits: list[dict[str, Any]] = []

    if aggregation:
        # Build a type→problem lookup for O(1) access.
        problems_by_type: dict[str, dict[str, Any]] = {
            p["problemType"]: p for p in avalanche_problems
        }

        for entry in aggregation:
            traits.append(_build_trait(entry, problems_by_type))

        if len(traits) > 2:
            logger.warning(
                "Bulletin %s produced %d traits — SLF may have extended the "
                "editorial model",
                bulletin_id,
                len(traits),
            )

    fallback_key_message = _build_fallback_key_message(properties)
    prose = _build_prose(properties)
    metadata = _build_metadata(properties)

    # Keep top-level snowpack_structure for v2 back-compat (equals prose copy).
    snowpack_structure: str | None = prose["snowpack_structure"]

    return {
        "version": RENDER_MODEL_VERSION,
        "danger": danger,
        "traits": traits,
        "fallback_key_message": fallback_key_message,
        "snowpack_structure": snowpack_structure,
        "metadata": metadata,
        "prose": prose,
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
    five-rule cascade from docs/day_character_rules_spec.md.

    When ``traits`` is empty (no avalanche problems reported), returns
    ``"Stable day"`` immediately.

    This function is pure — no side effects, no database access.

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

    # Empty traits → quiet day, no problems to trigger any rule.
    if not traits:
        return _("Stable day")  # type: ignore[return-value]

    problems: list[dict[str, Any]] = [
        p for trait in traits for p in (trait.get("problems") or [])
    ]

    # Rule 1 — Dangerous conditions: danger >= 4
    if danger >= 4:
        return _("Dangerous conditions")  # type: ignore[return-value]

    # Rule 2 — Hard-to-read day: danger >= 2 and any hard-to-read problem
    if danger >= 2 and any(
        p.get("problem_type") in _HARD_TO_READ_PROBLEMS for p in problems
    ):
        return _("Hard-to-read day")  # type: ignore[return-value]

    # Rule 3 — Widespread danger: danger == 3 and broad exposure
    if danger == 3 and _is_widespread(problems):
        return _("Widespread danger")  # type: ignore[return-value]

    # Rule 3b — Widespread danger: danger == 3 and upper subdivision (3+)
    if danger == 3 and subdivision == "+":
        return _("Widespread danger")  # type: ignore[return-value]

    # Rule 5 — Stable day
    if _is_stable(danger, problems):
        return _("Stable day")  # type: ignore[return-value]

    # Rule 4 — Manageable day: danger 2 or 3 with no earlier match
    if danger in {2, 3}:
        return _("Manageable day")  # type: ignore[return-value]

    # Safe default
    return _("Stable day")  # type: ignore[return-value]
