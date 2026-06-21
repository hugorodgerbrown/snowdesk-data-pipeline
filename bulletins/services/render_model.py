"""
bulletins/services/render_model.py — Render model builder (SLF, ALBINA, MeteoFrance).

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
    for backward compatibility. It was originally slated for removal in v4
    but has been retained — both locations hold the same value (still true
    as of v7).

Version 3 (continued — no shape change requiring regeneration):
  - Removed ``fallback_key_message`` from the output shape. The field was
    never rendered in any template; ``properties.highlights`` has been
    absent in SLF data since 2023-12-13.
  - Aggregation synthesis was removed. Missing aggregation logged ERROR and
    returned empty traits instead of synthesising from problem types.

Version 4 changes:
  - Source-aware builder: ``detect_source()`` identifies SLF vs. ALBINA
    bulletins and routes to source-specific helpers. Now also supports
    MeteoFrance (``"meteofrance"`` / ``Bulletin.Source.METEOFRANCE``). Raises
    ``RenderModelBuildError`` on unrecognised ``customData`` keys — the
    previous silent SLF fallback is removed so that unknown sources surface
    immediately rather than being silently misfiled.
  - Added ``source`` top-level key: ``"slf"``, ``"albina"``, or
    ``"meteofrance"``.
  - ``_resolve_aggregations()`` synthesises aggregation from problem types
    for ALBINA and MeteoFrance bulletins (no CH aggregation in either).
  - Added per-problem ``avalanche_type`` field (``"slab"``, ``"loose"``,
    or ``None``), drawn from ``customData.ALBINA.avalancheType`` for ALBINA.
    Always ``None`` for SLF and MeteoFrance.
  - Added per-problem ``extras`` field: source-specific passthrough dict.
    SLF: ``{"subdivision": str, "core_zone_text": str|None}``.
    ALBINA: ``{"avalanche_type": str|None}``.
    MeteoFrance: ``{}``.
  - Added ``prose.avalanche_activity`` dict with ``highlights`` and
    ``comment`` string fields (empty strings for SLF; populated from
    ``avalancheActivity`` for ALBINA and MeteoFrance).
  - Added top-level ``danger_patterns`` list (``[]`` for SLF and MeteoFrance;
    ``customData.LWD_Tyrol.dangerPatterns`` for ALBINA).

Version 5 changes:
  - Introduced ``CustomDataAdapter`` Protocol and three concrete adapter
    classes (``SlfAdapter``, ``AlbinaAdapter``, ``MeteoFranceAdapter``)
    registered in ``_ADAPTERS``. Each adapter reads its own ``customData``
    namespace and projects source-neutral fields. Source-conditional
    ``if source == X`` branches in helper functions now dispatch through the
    registry, eliminating scattered branching.
  - Added ``danger.ratings`` list: one entry per CAAML ``dangerRating`` with
    ``period``, ``key``, ``subdivision``, and ``elevation`` keys. SLF entries
    carry the per-rating subdivision from ``customData.CH``; ALBINA and
    METEOFRANCE entries carry ``subdivision=None``.
  - Added ``prose.tendency_lead`` string: ALBINA bulletins project
    ``tendency[0].highlights`` here when present; SLF and METEOFRANCE
    always ``None``.
  - Added per-problem named slots ``avalanche_size``, ``frequency``, and
    ``snowpack_stability``: populated from ALBINA raw problem fields;
    ``None`` for SLF and MeteoFrance.

Version 6 changes:
  - Wet-snow prose enrichment: when an SLF wet-snow or gliding-snow problem
    has empty ``aspects`` and no ``elevation``, the per-problem ``comment``
    field is passed to the language-keyed prose parser
    (``bulletins.services.prose.parse_for``).  On a successful parse the
    extracted ``aspects`` and ``elevation`` are merged into the built problem
    dict so that the render model carries structured geography wherever the
    prose grammar can recover it.  EAWS/ALBINA problems are never enriched
    (their data is already structured).  The parser is removable: deleting
    ``bulletins/services/prose/en.py`` silently falls back to the previous
    empty-state behaviour.

Version 7 changes:
  - ALBINA elevation-band grouping: ``AlbinaAdapter.resolve_aggregations``
    now calls the new ``_synthesise_aggregation_from_albina_problems`` helper
    which keys on ``(band_id, category, vtp)`` instead of ``(category, vtp)``.
    Each aggregation entry carries the resolved ``elevation`` dict and a
    ``band_id`` slug so downstream consumers (day_rating, views) can derive
    band labels without re-reading raw CAAML.  MeteoFrance continues to use
    the old ``_synthesise_aggregation_from_problems`` (no elevation split).
    Two new helpers support this: ``band_id_for_problem`` and
    ``band_label_for_elevation``.

Also exposes ``compute_period_transition``, a pure function that inspects the
``danger.ratings`` list in a render model and derives a ``PeriodTransition``
dataclass describing whether the day escalates, de-escalates, is flat-but-split,
or has no transition. Used by views to drive the hero chip and Day Risk Profile
caption without any source-conditional branching — the partition type is derived
from the ratings shape (temporal vs. elevation-banded) universally.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from django.utils.html import strip_tags as _strip_tags
from django.utils.translation import gettext_lazy as _

import bulletins.services.prose.en  # noqa: F401 — registers "en" parser side-effect
from bulletins.models import Bulletin
from bulletins.services.prose import parse_for as _prose_parse_for

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

RENDER_MODEL_VERSION: int = 7

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
# Prose spatial detection (SNOW-263)
# Regexes compiled once at module load for efficiency.  All matches are
# case-insensitive on the stripped lower-cased text, except the
# single-letter compass abbreviations which are matched against the
# ORIGINAL case to avoid spurious hits from common lower-case words.
# ---------------------------------------------------------------------------

# Cardinal directions (north, south, east, west) and combinations.
_RE_CARDINALS: re.Pattern[str] = re.compile(
    r"\b(north|south|east|west)\b", re.IGNORECASE
)
# Compound compass abbreviations (NE, SE, SW, NW) — case-insensitive safe
# because "ne" / "se" etc. do not appear as English words.
_RE_COMPASS_COMPOUND: re.Pattern[str] = re.compile(r"\b(NE|SE|SW|NW)\b")
# Single-letter compass abbreviations (N, S, E, W) as isolated tokens.
# Matched against ORIGINAL-case text to avoid "e.g.", "a", etc.
_RE_COMPASS_SINGLE: re.Pattern[str] = re.compile(r"\b[NSEW]\b")
# Elevation tokens: a 3–4-digit number followed by m/metres/meters.
_RE_ELEVATION_M: re.Pattern[str] = re.compile(
    r"\b\d{3,4}\s?(m|metres|meters)\b", re.IGNORECASE
)
# Proximity words adjacent to a number (e.g. "above 2200 m", "below treeline").
_RE_ELEVATION_ADJACENT: re.Pattern[str] = re.compile(
    r"\b(above|below|between)\s+\d+", re.IGNORECASE
)
# Treeline vocabulary.
_RE_TREELINE: re.Pattern[str] = re.compile(
    r"\b(treeline|tree\s+line|timberline)\b", re.IGNORECASE
)
# Altitude / elevation vocabulary.
_RE_ALTITUDE_VOCAB: re.Pattern[str] = re.compile(
    r"\b(altitude|elevations?|aspects?)\b", re.IGNORECASE
)


_CASE_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _RE_COMPASS_COMPOUND,
    _RE_COMPASS_SINGLE,
)

_CASE_INSENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _RE_CARDINALS,
    _RE_ELEVATION_M,
    _RE_ELEVATION_ADJACENT,
    _RE_TREELINE,
    _RE_ALTITUDE_VOCAB,
)


def detect_prose_spatial(comment_html: str) -> bool:
    """
    Return True when comment_html appears to describe spatial scope.

    Strips HTML tags, then searches for cardinal directions, compass
    abbreviations, elevation tokens (e.g. "2400 m"), treeline vocabulary,
    altitude/elevation keywords, and aspect vocabulary.  The function is
    biased toward false positives: returning True for an uncertain snippet
    degrades gracefully to a "See description below" message, while a false
    negative would wrongly assert "All aspects · all elevations".

    The generic template "Moist snow slides expected as the day warms." must
    return False (no direction, no number+m, no treeline, no altitude, no
    aspect tokens).

    Args:
        comment_html: Raw HTML comment text from the problem card.

    Returns:
        True when any spatial token is detected.

    """
    if not comment_html:
        return False
    original = _strip_tags(comment_html)
    plain = original.lower()
    # Case-sensitive patterns run against original text so single-letter compass
    # abbreviations (N, S, E, W) do not collide with common lower-case words.
    if any(p.search(original) for p in _CASE_SENSITIVE_PATTERNS):
        return True
    # Case-insensitive patterns run against lower-cased plain text.
    return any(p.search(plain) for p in _CASE_INSENSITIVE_PATTERNS)


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
        Dict with ``lower`` (int|None), ``upper`` (int|None),
        ``treeline`` (bool), and ``treeline_side`` (``"lower"``, ``"upper"``,
        or ``None``) keys, or ``None`` when no bounds are present.
        ``treeline_side`` records which CAAML bound carried the ``"treeline"``
        token so consumers can reconstruct distinct "above treeline" /
        "below treeline" bands after projection — the otherwise-lossy step
        that collapses both treeline-pivoted ratings to identical
        ``lower=upper=None`` shape.

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

    lower_is_treeline = str(lower_raw).lower() == _TREELINE_TOKEN
    upper_is_treeline = str(upper_raw).lower() == _TREELINE_TOKEN
    treeline = lower_is_treeline or upper_is_treeline
    treeline_side: str | None = None
    if lower_is_treeline:
        treeline_side = "lower"
    elif upper_is_treeline:
        treeline_side = "upper"

    return {
        "lower": _to_int(lower_raw),
        "upper": _to_int(upper_raw),
        "treeline": treeline,
        "treeline_side": treeline_side,
    }


# ---------------------------------------------------------------------------
# Danger resolution
# ---------------------------------------------------------------------------


def _resolve_danger(
    ratings: list[dict[str, Any]],
    source: Bulletin.Source = Bulletin.Source.SLF,
) -> dict[str, Any]:
    """
    Resolve the highest danger level and its subdivision from dangerRatings.

    When multiple ratings share the same highest mainValue the subdivision
    from the last one encountered is used.

    Also builds a ``ratings`` list: one entry per raw CAAML dangerRating
    with ``period``, ``key``, ``subdivision``, and ``elevation`` keys.
    The per-rating subdivision is adapter-specific — SLF reads
    ``customData.CH.subdivision``; ALBINA and METEOFRANCE carry ``None``.

    Args:
        ratings: The CAAML ``dangerRatings`` list.
        source: A ``Bulletin.Source`` member (used to select the adapter for
            per-rating subdivision resolution).

    Returns:
        Dict with ``key`` (str), ``number`` (str),
        ``subdivision`` (``"+"``, ``"="``, ``"-"``, or None), and
        ``ratings`` (list of per-rating dicts) keys.

    """
    adapter = _get_adapter(source)
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

    # Build the normalised per-rating list.
    ratings_list: list[dict[str, Any]] = []
    for rating in ratings:
        main_value = rating.get("mainValue", "")
        if main_value not in _DANGER_ORDER:
            continue
        ratings_list.append(
            {
                "period": rating.get("validTimePeriod") or "all_day",
                "key": main_value,
                "subdivision": adapter.resolve_danger_rating_subdivision(rating),
                "elevation": _parse_elevation(rating.get("elevation") or None),
            }
        )

    return {
        "key": highest,
        "number": _DANGER_NUMBER.get(highest, "1"),
        "subdivision": subdivision,
        "ratings": ratings_list,
    }


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------


def detect_source(properties: dict[str, Any]) -> Bulletin.Source:
    """
    Detect whether a bulletin originates from SLF, ALBINA, or MeteoFrance.

    Inspects ``customData`` keys:
    - ``"ALBINA"`` or any ``"LWD_*"`` key → ALBINA.
    - ``"MF"`` → MeteoFrance.
    - ``"CH"`` → SLF.

    This is the canonical source-detection logic shared by the render-model
    builder and the ``backfill_pdf_urls`` management command.  Using
    ``customData`` as the discriminator makes detection robust against stale
    ``render_model["source"]`` values (e.g. the legacy ``"euregio"`` value
    written by older pipeline versions).

    Args:
        properties: The CAAML properties dict.

    Returns:
        A ``Bulletin.Source`` member.

    Raises:
        RenderModelBuildError: When no known source marker is found in
            ``customData``.

    """
    custom_data: dict[str, Any] = properties.get("customData") or {}
    if "ALBINA" in custom_data:
        return Bulletin.Source.ALBINA
    for key in custom_data:
        if key.startswith("LWD_"):
            return Bulletin.Source.ALBINA
    if "MF" in custom_data:
        return Bulletin.Source.METEOFRANCE
    if "CH" in custom_data:
        return Bulletin.Source.SLF
    keys = sorted(custom_data.keys())
    raise RenderModelBuildError(
        f"Cannot determine bulletin source: no recognised customData marker found. "
        f"Present keys: {keys!r}. Expected one of: 'ALBINA', 'LWD_*', 'MF', 'CH'."
    )


# ---------------------------------------------------------------------------
# Low-level elevation and danger helpers (used by adapters and helpers below)
# ---------------------------------------------------------------------------


def _to_int_safe(val: Any) -> int | None:
    """Convert a raw bound value to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except ValueError, TypeError:
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


# ---------------------------------------------------------------------------
# CustomDataAdapter Protocol and concrete implementations
# ---------------------------------------------------------------------------


class CustomDataAdapter(Protocol):
    """
    Protocol for source-specific bulletin field adapters.

    Each concrete adapter reads its own ``customData`` namespace and projects
    the values into a source-neutral shape. The build pipeline selects the
    appropriate adapter via ``_ADAPTERS`` keyed on ``Bulletin.Source``.

    All methods are pure — no I/O, no side effects.
    """

    def resolve_aggregations(self, properties: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the aggregation list for this source."""
        ...

    def resolve_problem_rating(
        self,
        problem: dict[str, Any],
        danger_ratings: list[dict[str, Any]],
    ) -> str | None:
        """Return the danger rating value for a single problem."""
        ...

    def resolve_problem_comment(self, problem: dict[str, Any]) -> str:
        """Return the display comment for a single problem."""
        ...

    def resolve_problem_extras(self, problem: dict[str, Any]) -> dict[str, Any]:
        """Return source-specific passthrough fields for a problem."""
        ...

    def resolve_problem_avalanche_type(self, problem: dict[str, Any]) -> str | None:
        """Return the avalanche type (slab/loose/None) for a problem."""
        ...

    def resolve_problem_eaws_fields(self, problem: dict[str, Any]) -> dict[str, Any]:
        """
        Return EAWS optional problem-level fields.

        Returns a dict with ``avalanche_size`` (int|None), ``frequency``
        (str|None), and ``snowpack_stability`` (str|None).
        """
        ...

    def resolve_avalanche_activity(self, properties: dict[str, Any]) -> dict[str, str]:
        """Return the avalanche activity prose dict."""
        ...

    def resolve_danger_patterns(self, properties: dict[str, Any]) -> list[str]:
        """Return the danger patterns list."""
        ...

    def resolve_tendency_lead(self, properties: dict[str, Any]) -> str | None:
        """Return the tendency lead string, or None when absent."""
        ...

    def resolve_danger_rating_subdivision(self, rating: dict[str, Any]) -> str | None:
        """
        Return the subdivision suffix for a single dangerRating entry.

        The raw token (``"plus"``, ``"minus"``, ``"equal"``) is resolved to
        the display character (``"+"``, ``"-"``, ``"="``) or ``None``.
        """
        ...


class SlfAdapter:
    """
    Adapter for SLF (Swiss) bulletins.

    Reads ``customData.CH`` for aggregation, subdivision, and coreZoneText.
    Per-problem EAWS fields (avalanche_size, frequency, snowpack_stability)
    are absent in SLF data — always returns ``None`` for those.
    """

    def resolve_aggregations(self, properties: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the CH aggregation list verbatim."""
        return (properties.get("customData") or {}).get("CH", {}).get(
            "aggregation"
        ) or []

    def resolve_problem_rating(
        self,
        problem: dict[str, Any],
        danger_ratings: list[dict[str, Any]],  # noqa: ARG002 — not used for SLF
    ) -> str | None:
        """SLF carries per-problem dangerRatingValue directly."""
        raw = problem.get("dangerRatingValue")
        return raw if raw else None

    def resolve_problem_comment(self, problem: dict[str, Any]) -> str:
        """SLF carries a per-problem comment field with HTML prose."""
        return problem.get("comment") or ""

    def resolve_problem_extras(self, problem: dict[str, Any]) -> dict[str, Any]:
        """Return subdivision and coreZoneText from customData.CH."""
        ch_data: dict[str, Any] = (problem.get("customData") or {}).get("CH", {})
        return {
            "subdivision": ch_data.get("subdivision", "") or "",
            "core_zone_text": ch_data.get("coreZoneText") or None,
        }

    def resolve_problem_avalanche_type(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for SLF
    ) -> str | None:
        """SLF bulletins do not carry an avalanche type."""
        return None

    def resolve_problem_eaws_fields(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for SLF
    ) -> dict[str, Any]:
        """SLF bulletins do not carry EAWS problem-level fields."""
        return {
            "avalanche_size": None,
            "frequency": None,
            "snowpack_stability": None,
        }

    def resolve_avalanche_activity(
        self,
        properties: dict[str, Any],  # noqa: ARG002 — not used for SLF
    ) -> dict[str, str]:
        """SLF does not carry bulletin-level avalanche activity."""
        return {"highlights": "", "comment": ""}

    def resolve_danger_patterns(
        self,
        properties: dict[str, Any],  # noqa: ARG002 — not used for SLF
    ) -> list[str]:
        """SLF bulletins do not carry danger patterns."""
        return []

    def resolve_tendency_lead(
        self,
        properties: dict[str, Any],  # noqa: ARG002 — not used for SLF
    ) -> str | None:
        """SLF bulletins do not carry a tendency lead."""
        return None

    def resolve_danger_rating_subdivision(self, rating: dict[str, Any]) -> str | None:
        """Read subdivision from customData.CH on each dangerRating."""
        ch_data = (rating.get("customData") or {}).get("CH", {})
        raw_subdivision: str = ch_data.get("subdivision", "") or ""
        return _SUBDIVISION_MAP.get(raw_subdivision, None)


def _synthesise_aggregation_from_problems(
    properties: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Synthesise an aggregation list from ``avalancheProblems`` by (category, vtp).

    Shared by AlbinaAdapter and MeteoFranceAdapter which both lack a
    ``customData`` aggregation block and derive the same grouping from their
    problems list.

    Args:
        properties: The CAAML bulletin properties dict.

    Returns:
        An aggregation list in problem-encounter order with deduplicated entries.

    """
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
        if pt not in entry["problemTypes"]:
            entry["problemTypes"].append(pt)
    return [seen[k] for k in order]


def band_id_for_problem(problem: dict[str, Any]) -> str:
    """
    Derive a stable band identifier slug from an ALBINA problem's elevation.

    Used by ``_synthesise_aggregation_from_albina_problems`` to group problems
    that share the same elevation band, regardless of category or time period.
    The slug is safe for use as a data attribute value.

    Rules:
      - No elevation → ``"all-elevations"``
      - Treeline pivot, lower bound is treeline → ``"above-treeline"`` (above band)
        or ``"below-treeline"`` (below band, upper bound is treeline).
      - Numeric lower bound only → ``"above-{lower}"`` (above band).
      - Numeric upper bound only → ``"below-{upper}"`` (below band).
      - Both numeric → ``"{lower}-to-{upper}"`` (narrow band — rare).

    Args:
        problem: A single raw CAAML avalanche problem dict.

    Returns:
        A hyphenated slug string identifying the elevation band.

    """
    elevation: dict[str, Any] | None = problem.get("elevation") or None
    if not elevation:
        return "all-elevations"

    lower_raw = elevation.get("lowerBound")
    upper_raw = elevation.get("upperBound")

    lower_is_treeline = str(lower_raw or "").lower() == _TREELINE_TOKEN
    upper_is_treeline = str(upper_raw or "").lower() == _TREELINE_TOKEN

    if lower_is_treeline:
        return "above-treeline"
    if upper_is_treeline:
        return "below-treeline"

    lower_int = _to_int_safe(lower_raw)
    upper_int = _to_int_safe(upper_raw)

    if lower_int is not None and upper_int is not None:
        return f"{lower_int}-to-{upper_int}"
    if lower_int is not None:
        return f"above-{lower_int}"
    if upper_int is not None:
        return f"below-{upper_int}"
    return "all-elevations"


def band_label_for_elevation(elevation: dict[str, Any] | None) -> str:
    """
    Build a human-readable band label from a parsed elevation dict.

    Consumes the shape produced by ``_parse_elevation``:
    ``{"lower": int|None, "upper": int|None, "treeline": bool,
       "treeline_side": "lower"|"upper"|None}``.

    Rules:
      - ``None`` → ``"All elevations"``
      - treeline, lower bound is treeline (``treeline_side == "lower"``) →
        ``"Above treeline"``
      - treeline, upper bound is treeline (``treeline_side == "upper"``) →
        ``"Below treeline"``
      - numeric lower only → ``"Above {lower} m"``
      - numeric upper only → ``"Below {upper} m"``
      - both numeric → ``"{lower}–{upper} m"``

    Args:
        elevation: Parsed elevation dict from ``_parse_elevation``, or ``None``.

    Returns:
        A human-readable band label string.

    """
    if not elevation:
        return "All elevations"

    treeline: bool = elevation.get("treeline", False)
    treeline_side: str | None = elevation.get("treeline_side")

    if treeline:
        if treeline_side == "lower":
            return "Above treeline"
        return "Below treeline"

    lower: int | None = elevation.get("lower")
    upper: int | None = elevation.get("upper")

    if lower is not None and upper is not None:
        return f"{lower}–{upper} m"
    if lower is not None:
        return f"Above {lower} m"
    if upper is not None:
        return f"Below {upper} m"
    return "All elevations"


def _synthesise_aggregation_from_albina_problems(
    properties: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Synthesise an ALBINA aggregation list keyed on (band_id, category, vtp).

    ALBINA bulletins split danger by elevation band — each problem carries an
    ``elevation`` field that identifies the band (e.g. ``lowerBound: "2200"``).
    This synthesiser keys on ``(band_id, category, vtp)`` so that a bulletin
    with an elevation split produces one aggregation entry per band per
    (category, period), instead of collapsing both bands into one entry.

    Unlike ``_synthesise_aggregation_from_problems`` (which is shared with
    MeteoFrance and keys on ``(category, vtp)`` only), this function also
    carries the parsed ``elevation`` dict and ``band_id`` string forward on
    each entry so that downstream consumers (day_rating, views) can derive
    band labels without re-reading the raw CAAML.

    The MeteoFrance adapter continues to call the shared function.

    Args:
        properties: The CAAML bulletin properties dict.

    Returns:
        An aggregation list in problem-encounter order with entries deduplicated
        by ``(band_id, category, vtp)`` and enriched with ``elevation`` and
        ``band_id`` keys.

    """
    problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for problem in problems:
        pt: str = problem.get("problemType", "")
        vtp: str = problem.get("validTimePeriod") or "all_day"
        category: str = PROBLEM_TYPE_TO_CATEGORY.get(pt, "dry")
        band_id: str = band_id_for_problem(problem)
        key = (band_id, category, vtp)
        if key not in seen:
            parsed_elevation = _parse_elevation(problem.get("elevation") or None)
            seen[key] = {
                "category": category,
                "validTimePeriod": vtp,
                "problemTypes": [],
                "title": None,
                "band_id": band_id,
                "elevation": parsed_elevation,
            }
            order.append(key)
        entry = seen[key]
        if pt not in entry["problemTypes"]:
            entry["problemTypes"].append(pt)
    return [seen[k] for k in order]


class AlbinaAdapter:
    """
    Adapter for ALBINA (ALBINA) bulletins.

    Synthesises aggregation from avalancheProblems. Reads
    ``customData.ALBINA`` for avalanche type, ``customData.LWD_Tyrol``
    (or any ``LWD_*`` key) for danger patterns, and ``tendency[0].highlights``
    for the tendency lead. Per-problem EAWS fields
    (``avalancheSize``, ``frequency``, ``snowpackStability``) are read
    from the raw problem dict.
    """

    def resolve_aggregations(self, properties: dict[str, Any]) -> list[dict[str, Any]]:
        """Synthesise aggregation from avalancheProblems by (band, category, vtp)."""
        return _synthesise_aggregation_from_albina_problems(properties)

    def resolve_problem_rating(
        self,
        problem: dict[str, Any],
        danger_ratings: list[dict[str, Any]],
    ) -> str | None:
        """Derive danger rating by matching the problem's elevation/vtp."""
        return _match_problem_rating(problem, danger_ratings)

    def resolve_problem_comment(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for ALBINA
    ) -> str:
        """ALBINA carries activity prose at bulletin level, not per-problem."""
        return ""

    def resolve_problem_extras(self, problem: dict[str, Any]) -> dict[str, Any]:
        """Return avalanche_type from customData.ALBINA."""
        albina_data: dict[str, Any] = (problem.get("customData") or {}).get(
            "ALBINA", {}
        )
        return {
            "avalanche_type": albina_data.get("avalancheType") or None,
        }

    def resolve_problem_avalanche_type(self, problem: dict[str, Any]) -> str | None:
        """Return the avalanche type from customData.ALBINA."""
        albina_data: dict[str, Any] = (problem.get("customData") or {}).get(
            "ALBINA", {}
        )
        return albina_data.get("avalancheType") or None

    def resolve_problem_eaws_fields(self, problem: dict[str, Any]) -> dict[str, Any]:
        """Return EAWS fields from the raw problem dict."""
        return {
            "avalanche_size": problem.get("avalancheSize"),
            "frequency": problem.get("frequency") or None,
            "snowpack_stability": problem.get("snowpackStability") or None,
        }

    def resolve_avalanche_activity(self, properties: dict[str, Any]) -> dict[str, str]:
        """Return avalancheActivity highlights and comment."""
        activity: dict[str, Any] = properties.get("avalancheActivity") or {}
        return {
            "highlights": activity.get("highlights") or "",
            "comment": activity.get("comment") or "",
        }

    def resolve_danger_patterns(self, properties: dict[str, Any]) -> list[str]:
        """Return dangerPatterns from customData.LWD_Tyrol (or any LWD_* key)."""
        custom_data: dict[str, Any] = properties.get("customData") or {}
        lwd_data = custom_data.get("LWD_Tyrol") or {}
        if not lwd_data:
            for key, value in custom_data.items():
                if key.startswith("LWD_") and value:
                    lwd_data = value
                    break
        patterns = lwd_data.get("dangerPatterns") or []
        return [str(p) for p in patterns]

    def resolve_tendency_lead(self, properties: dict[str, Any]) -> str | None:
        """Return tendency[0].highlights when present and non-empty."""
        tendency = properties.get("tendency") or []
        if tendency:
            highlights = (tendency[0] or {}).get("highlights") or ""
            if highlights.strip():
                return highlights
        return None

    def resolve_danger_rating_subdivision(
        self,
        rating: dict[str, Any],  # noqa: ARG002 — ALBINA has no per-rating subdivision
    ) -> str | None:
        """ALBINA dangerRatings carry no subdivision."""
        return None


class MeteoFranceAdapter:
    """
    Adapter for MeteoFrance (METEOFRANCE / BRA) bulletins.

    Synthesises aggregation from avalancheProblems. Per-problem EAWS fields
    and danger patterns are absent in METEOFRANCE data. No tendency lead.
    """

    def resolve_aggregations(self, properties: dict[str, Any]) -> list[dict[str, Any]]:
        """Synthesise aggregation from avalancheProblems by (category, vtp)."""
        return _synthesise_aggregation_from_problems(properties)

    def resolve_problem_rating(
        self,
        problem: dict[str, Any],
        danger_ratings: list[dict[str, Any]],
    ) -> str | None:
        """Derive danger rating by matching the problem's elevation/vtp."""
        return _match_problem_rating(problem, danger_ratings)

    def resolve_problem_comment(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for METEOFRANCE
    ) -> str:
        """METEOFRANCE carries activity prose at bulletin level, not per-problem."""
        return ""

    def resolve_problem_extras(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — no METEOFRANCE problem-level extras
    ) -> dict[str, Any]:
        """MeteoFrance has no source-specific problem-level extras."""
        return {}

    def resolve_problem_avalanche_type(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for METEOFRANCE
    ) -> str | None:
        """METEOFRANCE bulletins do not carry an avalanche type."""
        return None

    def resolve_problem_eaws_fields(
        self,
        problem: dict[str, Any],  # noqa: ARG002 — not used for METEOFRANCE
    ) -> dict[str, Any]:
        """METEOFRANCE bulletins do not carry EAWS problem-level fields."""
        return {
            "avalanche_size": None,
            "frequency": None,
            "snowpack_stability": None,
        }

    def resolve_avalanche_activity(self, properties: dict[str, Any]) -> dict[str, str]:
        """Return avalancheActivity highlights and comment."""
        activity: dict[str, Any] = properties.get("avalancheActivity") or {}
        return {
            "highlights": activity.get("highlights") or "",
            "comment": activity.get("comment") or "",
        }

    def resolve_danger_patterns(
        self,
        properties: dict[str, Any],  # noqa: ARG002 — not used for METEOFRANCE
    ) -> list[str]:
        """METEOFRANCE bulletins do not carry danger patterns."""
        return []

    def resolve_tendency_lead(
        self,
        properties: dict[str, Any],  # noqa: ARG002 — not used for METEOFRANCE
    ) -> str | None:
        """METEOFRANCE bulletins do not carry a tendency lead."""
        return None

    def resolve_danger_rating_subdivision(
        self,
        rating: dict[str, Any],  # noqa: ARG002 — METEOFRANCE has no per-rating subdivision
    ) -> str | None:
        """METEOFRANCE dangerRatings carry no subdivision."""
        return None


# Registry mapping source → adapter instance.
_ADAPTERS: dict[Bulletin.Source, CustomDataAdapter] = {
    Bulletin.Source.SLF: SlfAdapter(),
    Bulletin.Source.ALBINA: AlbinaAdapter(),
    Bulletin.Source.METEOFRANCE: MeteoFranceAdapter(),
}


def _get_adapter(source: Bulletin.Source) -> CustomDataAdapter:
    """
    Return the adapter for a given bulletin source.

    Raises ``RenderModelBuildError`` when no adapter is registered for
    the source — this should never happen in practice, but surfaces
    any future source additions that were not paired with a new adapter.

    Args:
        source: A ``Bulletin.Source`` member.

    Returns:
        The registered :class:`CustomDataAdapter` instance.

    Raises:
        RenderModelBuildError: When the source has no registered adapter.

    """
    adapter = _ADAPTERS.get(source)
    if adapter is None:
        raise RenderModelBuildError(
            f"No CustomDataAdapter registered for source {source!r}. "
            f"Registered sources: {sorted(str(s) for s in _ADAPTERS)}"
        )
    return adapter


# ---------------------------------------------------------------------------
# Shared problem-rating matcher (used by both ALBINA and METEOFRANCE adapters)
# ---------------------------------------------------------------------------


def _match_problem_rating(
    problem: dict[str, Any],
    danger_ratings: list[dict[str, Any]],
) -> str | None:
    """
    Derive the danger rating for a non-SLF problem by elevation + period matching.

    Used by AlbinaAdapter and MeteoFranceAdapter which share the same
    matching algorithm. See ``_resolve_problem_rating`` for the full spec.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        danger_ratings: Bulletin-level danger ratings list.

    Returns:
        The highest matching mainValue string, or ``None``.

    """
    problem_vtp: str = problem.get("validTimePeriod") or "all_day"
    problem_elevation: dict[str, Any] | None = problem.get("elevation") or None
    problem_lower = _to_int_safe((problem_elevation or {}).get("lowerBound"))
    problem_upper = _to_int_safe((problem_elevation or {}).get("upperBound"))

    matching = [
        r
        for r in danger_ratings
        if (r.get("validTimePeriod") or "all_day") == problem_vtp
    ]
    if not matching:
        matching = list(danger_ratings)

    candidates = _filter_ratings_by_elevation(matching, problem_lower, problem_upper)
    if not candidates:
        candidates = matching

    return _highest_danger(candidates)


# ---------------------------------------------------------------------------
# Source-specific helpers
# ---------------------------------------------------------------------------


def _resolve_aggregations(
    properties: dict[str, Any], source: Bulletin.Source
) -> list[dict[str, Any]]:
    """
    Resolve the aggregation list from bulletin properties via the adapter registry.

    For SLF bulletins this reads ``customData.CH.aggregation`` verbatim.
    For ALBINA and MeteoFrance bulletins it synthesises aggregation entries
    by grouping ``avalancheProblems`` on ``(category, validTimePeriod)``.

    The output shape is the same in all cases:
    ``[{"category": str, "problemTypes": [str], "validTimePeriod": str|None,
       "title": str|None}, ...]``

    Args:
        properties: The CAAML properties dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        A list of aggregation entry dicts.

    """
    return _get_adapter(source).resolve_aggregations(properties)


def _resolve_problem_rating(
    problem: dict[str, Any],
    danger_ratings: list[dict[str, Any]],
    source: Bulletin.Source,
) -> str | None:
    """
    Resolve the danger rating value for a single avalanche problem.

    Dispatches to the source adapter. For SLF bulletins the value is read
    directly from ``problem["dangerRatingValue"]``. For ALBINA and
    MeteoFrance bulletins the danger rating is derived by matching the
    problem's elevation and validTimePeriod against the bulletin-level
    ``dangerRatings``.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        danger_ratings: The bulletin-level ``dangerRatings`` list.
        source: A ``Bulletin.Source`` member.

    Returns:
        A danger level string (e.g. ``"moderate"``) or ``None`` when
        no match can be found.

    """
    return _get_adapter(source).resolve_problem_rating(problem, danger_ratings)


def _resolve_problem_comment(problem: dict[str, Any], source: Bulletin.Source) -> str:
    """
    Resolve the display comment for a single avalanche problem.

    SLF bulletins carry a per-problem ``comment`` field with HTML prose.
    ALBINA and MeteoFrance bulletins carry avalanche activity prose at
    bulletin level (surfaced via ``prose.avalanche_activity``), so
    per-problem comments are returned as empty strings.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        HTML comment string, or empty string when absent, ALBINA, or METEOFRANCE.

    """
    return _get_adapter(source).resolve_problem_comment(problem)


def _resolve_problem_extras(
    problem: dict[str, Any], source: Bulletin.Source
) -> dict[str, Any]:
    """
    Resolve source-specific passthrough fields for a problem card.

    SLF: returns ``{"subdivision": str, "core_zone_text": str|None}`` drawn
    from ``customData.CH``.

    ALBINA: returns ``{"avalanche_type": str|None}`` drawn from
    ``customData.ALBINA.avalancheType``.

    MeteoFrance: returns ``{}`` (no source-specific problem-level extras).

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        Source-specific extras dict.

    """
    return _get_adapter(source).resolve_problem_extras(problem)


def _resolve_problem_avalanche_type(
    problem: dict[str, Any], source: Bulletin.Source
) -> str | None:
    """
    Resolve the avalanche type (slab/loose) for a problem, if available.

    Only present for ALBINA bulletins; SLF and MeteoFrance bulletins
    always return ``None``.

    Args:
        problem: A single raw CAAML avalanche problem dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        ``"slab"``, ``"loose"``, or ``None``.

    """
    return _get_adapter(source).resolve_problem_avalanche_type(problem)


def _resolve_avalanche_activity(
    properties: dict[str, Any], source: Bulletin.Source
) -> dict[str, str]:
    """
    Resolve avalanche activity prose from bulletin properties.

    SLF bulletins do not carry an ``avalancheActivity`` field at the bulletin
    level; returns empty strings for both fields.

    ALBINA and MeteoFrance bulletins carry ``avalancheActivity.highlights``
    and ``avalancheActivity.comment``.

    Args:
        properties: The CAAML properties dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        Dict with ``"highlights"`` and ``"comment"`` string fields.

    """
    return _get_adapter(source).resolve_avalanche_activity(properties)


def _resolve_danger_patterns(
    properties: dict[str, Any], source: Bulletin.Source
) -> list[str]:
    """
    Resolve danger patterns from bulletin custom data.

    SLF and MeteoFrance bulletins do not carry danger patterns; returns an
    empty list.

    ALBINA bulletins may carry ``customData.LWD_Tyrol.dangerPatterns``.
    Other ``LWD_*`` keys are searched when ``LWD_Tyrol`` is absent.

    Args:
        properties: The CAAML properties dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        List of danger pattern strings, e.g. ``["DP10", "DP1"]``, or ``[]``.

    """
    return _get_adapter(source).resolve_danger_patterns(properties)


# ---------------------------------------------------------------------------
# Problem builder (source-aware)
# ---------------------------------------------------------------------------


def _build_problem(
    problem: dict[str, Any],
    danger_ratings: list[dict[str, Any]],
    source: Bulletin.Source,
    lang: str = "en",
) -> dict[str, Any]:
    """
    Convert a raw CAAML avalanche problem into the render model shape.

    When the problem is a wet-snow or gliding-snow type, ``aspects`` is empty,
    and ``elevation`` is absent in the raw data, the per-problem ``comment``
    field is passed to the registered prose parser for ``lang``.  On a
    successful parse the extracted aspects and elevation are merged into the
    returned dict.  The enrichment is a no-op when no parser is registered
    for the language, or when the parser finds no tokens.

    Args:
        problem: A single raw avalanche problem dict from CAAML.
        danger_ratings: Bulletin-level danger ratings (used for ALBINA
            and MeteoFrance to derive per-problem danger rating values).
        source: A ``Bulletin.Source`` member.
        lang: BCP-47 language code for prose-parser dispatch (default ``"en"``).

    Returns:
        A rendered problem dict suitable for the render model.

    """
    adapter = _get_adapter(source)
    elevation = _parse_elevation(problem.get("elevation") or None)
    aspects: list[str] = problem.get("aspects") or []
    comment_html: str = adapter.resolve_problem_comment(problem)
    extras: dict[str, Any] = adapter.resolve_problem_extras(problem)
    # core_zone_text is projected by SlfAdapter.resolve_problem_extras; ALBINA
    # and METEOFRANCE adapters return {} so extras.get() yields None for those sources.
    # Reading from extras rather than raw customData.CH keeps the namespace
    # boundary clean — callers never need to know the SLF namespace key.
    core_zone_text: str | None = extras.get("core_zone_text") or None
    danger_rating_value: str | None = adapter.resolve_problem_rating(
        problem, danger_ratings
    )
    avalanche_type: str | None = adapter.resolve_problem_avalanche_type(problem)
    eaws_fields: dict[str, Any] = adapter.resolve_problem_eaws_fields(problem)

    # Prose enrichment: for wet-snow/gliding-snow problems that lack structured
    # geography, attempt to extract aspects and elevation from the comment text.
    # Only applied when both aspects and elevation are absent so that bulletins
    # that already carry structured data are never overwritten.
    problem_type: str = problem.get("problemType", "")
    if (
        problem_type in WET_PROBLEM_TYPES
        and not aspects
        and elevation is None
        and comment_html
    ):
        parsed = _prose_parse_for(lang, comment_html)
        if parsed is not None:
            aspects = parsed.aspects
            # Only store elevation when the parsed dict carries real constraints
            # (a bound or a treeline flag). An all-None unconstrained dict means
            # no elevation phrase was found and should stay as None.
            parsed_elev = parsed.elevation
            if (
                parsed_elev.get("lower") is not None
                or parsed_elev.get("upper") is not None
                or parsed_elev.get("treeline")
            ):
                elevation = parsed_elev

    return {
        "problem_type": problem_type,
        "time_period": problem.get("validTimePeriod", ""),
        "elevation": elevation,
        "aspects": aspects,
        "comment_html": comment_html,
        "core_zone_text": core_zone_text,
        "danger_rating_value": danger_rating_value,
        "avalanche_type": avalanche_type,
        "extras": extras,
        "avalanche_size": eaws_fields["avalanche_size"],
        "frequency": eaws_fields["frequency"],
        "snowpack_stability": eaws_fields["snowpack_stability"],
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
            ``customData.CH.aggregation`` field or the synthesised ALBINA list).

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
    source: Bulletin.Source,
    lang: str = "en",
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
        source: A ``Bulletin.Source`` member.
        lang: BCP-47 language code forwarded to the per-problem prose enrichment.

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

    built_problems = [
        _build_problem(p, danger_ratings, source, lang) for p in matched_raw
    ]

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

    # ALBINA entries carry band_id and elevation from the synthesised aggregation.
    # SLF and MeteoFrance entries never set these keys, so .get() returns None.
    band_id: str | None = aggregation_entry.get("band_id") or None
    band_elevation: dict[str, Any] | None = aggregation_entry.get("elevation") or None

    return {
        "category": category,
        "time_period": time_period,
        "title": title,
        "geography": {"source": geography_source},
        "problems": built_problems,
        "prose": prose,
        "danger_level": danger_level,
        "band_id": band_id,
        "elevation": band_elevation,
    }


# ---------------------------------------------------------------------------
# Trait list builder (extracted to keep build_render_model complexity low)
# ---------------------------------------------------------------------------


def _build_synthesised_traits(
    aggregation: list[dict[str, Any]],
    avalanche_problems: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
    source: Bulletin.Source,
    lang: str = "en",
) -> list[dict[str, Any]]:
    """
    Build traits for ALBINA and MeteoFrance bulletins using a per-(type, vtp) lookup.

    The same problem type can appear in multiple validTimePeriods in both
    ALBINA and MeteoFrance bulletins, so each aggregation entry is resolved
    against the subset of problems that match its validTimePeriod.

    Args:
        aggregation: The synthesised aggregation list.
        avalanche_problems: The raw ``avalancheProblems`` list.
        ratings: Bulletin-level danger ratings.
        source: Either ``Bulletin.Source.ALBINA`` or ``Bulletin.Source.METEOFRANCE``.
        lang: BCP-47 language code forwarded to the per-problem prose enrichment.

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
        traits.append(_build_trait(entry, entry_problems, ratings, source, lang))
    return traits


def _build_traits(
    aggregation: list[dict[str, Any]],
    avalanche_problems: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
    source: Bulletin.Source,
    bulletin_id: str,
    lang: str = "en",
) -> list[dict[str, Any]]:
    """
    Build the complete traits list from aggregation entries and problems.

    For ALBINA and MeteoFrance bulletins the same problem type may appear
    in multiple validTimePeriods, so a per-(type, vtp) lookup is used to
    ensure each aggregation group resolves to the correct problem instance.

    For SLF bulletins a simpler type-keyed lookup suffices.

    Args:
        aggregation: The resolved aggregation entry list.
        avalanche_problems: The raw ``avalancheProblems`` list.
        ratings: Bulletin-level danger ratings.
        source: A ``Bulletin.Source`` member.
        bulletin_id: Used in warning log messages.
        lang: BCP-47 language code forwarded to the per-problem prose enrichment.

    Returns:
        Flat list of trait dicts in aggregation order.

    """
    traits: list[dict[str, Any]] = []

    if source in {Bulletin.Source.ALBINA, Bulletin.Source.METEOFRANCE}:
        traits = _build_synthesised_traits(
            aggregation, avalanche_problems, ratings, source, lang
        )
    else:
        # SLF: problem type uniquely identifies a problem row.
        problems_by_type: dict[str, dict[str, Any]] = {
            p["problemType"]: p for p in avalanche_problems
        }
        for entry in aggregation:
            traits.append(_build_trait(entry, problems_by_type, ratings, source, lang))

    if len(traits) > 2:
        logger.warning(
            "Bulletin %s produced %d traits — may have extended the editorial model",
            bulletin_id,
            len(traits),
        )
    return traits


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
    except ValueError, AttributeError:
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


def _build_prose(
    properties: dict[str, Any], source: Bulletin.Source = Bulletin.Source.SLF
) -> dict[str, Any]:
    """
    Extract prose sections from CAAML properties.

    Reads ``snowpackStructure.comment``, ``weatherReview.comment``,
    ``weatherForecast.comment``, the ``tendency`` array, and (for ALBINA)
    ``avalancheActivity``. Each tendency entry captures ``comment``,
    ``tendency_type`` (from ``tendencyType``), ``valid_from``, and
    ``valid_until`` (from the entry's ``validTime``).
    Missing or empty tendency array → ``[]``. Missing scalar prose → ``None``.
    ``avalanche_activity`` is always present; empty strings for SLF.

    Also projects ``tendency_lead`` (str|None): ALBINA bulletins carry a
    short editorial lead at ``tendency[0].highlights`` — a forecaster-authored
    one-liner. SLF and MeteoFrance always return ``None`` here.

    Args:
        properties: The CAAML properties dict.
        source: A ``Bulletin.Source`` member.

    Returns:
        A prose dict with ``snowpack_structure``, ``weather_review``,
        ``weather_forecast``, ``tendency``, ``avalanche_activity``, and
        ``tendency_lead`` keys.

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
    tendency_lead: str | None = _get_adapter(source).resolve_tendency_lead(properties)

    return {
        "snowpack_structure": snowpack_structure,
        "weather_review": weather_review,
        "weather_forecast": weather_forecast,
        "tendency": tendency,
        "avalanche_activity": avalanche_activity,
        "tendency_lead": tendency_lead,
    }


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_render_model(properties: dict[str, Any]) -> dict[str, Any]:
    """
    Build a versioned render model dict from raw CAAML bulletin properties.

    This is a pure function: no I/O, no side effects.

    Raises ``RenderModelBuildError`` when the data shape violates the
    canonical EAWS problem-type enum, structural invariants, or when the
    bulletin source cannot be identified from ``customData`` keys. The
    caller is responsible for catching this and storing an error sentinel.

    Supports SLF, ALBINA, and MeteoFrance (METEOFRANCE) bulletin formats.
    The source is detected automatically via ``detect_source()`` and
    stamped in the output as ``render_model["source"]``.

    Args:
        properties: The CAAML properties dict (the ``"properties"`` key from
            the GeoJSON Feature envelope stored in ``Bulletin.raw_data``).

    Returns:
        A render model dict ready for storage in ``Bulletin.render_model``.

    Raises:
        RenderModelBuildError: When the bulletin data cannot be cleanly
            mapped to the render model shape, or when the source cannot be
            determined from ``customData``.

    """
    bulletin_id: str = properties.get("bulletinID", "<unknown>")

    source = detect_source(properties)

    ratings: list[dict[str, Any]] = properties.get("dangerRatings") or []
    danger = _resolve_danger(ratings, source)

    avalanche_problems: list[dict[str, Any]] = properties.get("avalancheProblems") or []
    aggregation: list[dict[str, Any]] = _resolve_aggregations(properties, source)

    # For SLF bulletins: aggregation is expected to always be present when
    # avalancheProblems is non-empty. Log an error and produce empty traits
    # if missing — do not synthesise, as this indicates an upstream data gap.
    if source == Bulletin.Source.SLF and avalanche_problems and not aggregation:
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

    # Extract lang early so prose enrichment uses the bulletin's own language.
    lang: str = properties.get("lang") or "en"

    if aggregation:
        traits = _build_traits(
            aggregation, avalanche_problems, ratings, source, bulletin_id, lang
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


# ---------------------------------------------------------------------------
# Period transition
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PeriodTransition:
    """
    Describes how the avalanche danger changes across the day.

    Derived from the ``danger.ratings`` list in the render model by
    :func:`compute_period_transition`. Used by views to render the hero chip
    and the Day Risk Profile caption.

    Attributes:
        direction: ``"rise"``, ``"fall"``, or ``"none"`` (flat-but-split).
            ``"none"`` means the danger level is unchanged but a period
            boundary exists (i.e. problem type changes across the day).
        destination_key: CAAML danger key for the destination period, e.g.
            ``"considerable"``. Same as the source key when ``direction="none"``.
        destination_number: Display number string (``"1"``–``"5"``) for the
            destination level.
        destination_subdivision: Subdivision suffix (``"+"``, ``"-"``,
            ``"="``), or ``""`` when absent.
        partition_type: ``"temporal"`` when the split is morning/afternoon;
            ``"elevation"`` when the split is by elevation band.
        partition_label: Human-readable qualifier for the split, e.g.
            ``"above 2600 m"`` for an elevation-banded bulletin or ``""``
            for a temporal split. Provided only when ``partition_type``
            is ``"elevation"`` and an elevation bound is available.
        has_split: ``True`` when any temporal split exists (direction may be
            ``"none"`` for flat-but-split). Used by templates to decide whether
            to show the Day Risk Profile transition row at all.

    """

    direction: str
    destination_key: str
    destination_number: str
    destination_subdivision: str
    partition_type: str
    partition_label: str
    has_split: bool


def _elevation_label_from_rm(rm_elev: dict[str, Any] | None) -> str:
    """
    Return a short elevation label from a projected render-model elevation dict.

    E.g. ``{"lower": 2600, "upper": None, ...}`` → ``"above 2600 m"``.
    Used by ``compute_period_transition`` to build the ``partition_label``
    field on :class:`PeriodTransition`.

    Args:
        rm_elev: A projected ``danger.ratings[*].elevation`` dict, or ``None``.

    Returns:
        A human-readable string such as ``"above 2600 m"`` or ``""``.

    """
    if not rm_elev:
        return ""
    lower = rm_elev.get("lower")
    upper = rm_elev.get("upper")
    treeline = rm_elev.get("treeline")
    treeline_side = rm_elev.get("treeline_side")
    if treeline and treeline_side == "lower":
        return "above treeline"
    if treeline and treeline_side == "upper":
        return "below treeline"
    if lower is not None:
        return f"above {lower} m"
    if upper is not None:
        return f"below {upper} m"
    return ""


def _danger_rank_pair(key: str, subdivision: str) -> tuple[int, int]:
    """Return sortable rank for a ``(key, subdivision)`` pair.

    Subdivision display chars ``"+"``, ``"="``, ``"-"`` map to offsets
    ``(+1, 0, -1)``; absent / ``""`` maps to 0.

    Args:
        key: CAAML danger key string.
        subdivision: Display character or ``""``.

    Returns:
        ``(band_index, subdivision_offset)`` tuple.

    """
    band = _DANGER_ORDER.index(key) if key in _DANGER_ORDER else 0
    offset = {"+": 1, "=": 0, "-": -1}.get(subdivision, 0)
    return (band, offset)


def _best_rated(ratings: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(key, subdivision)`` for the highest-ranked rating in *ratings*.

    Falls back to ``("low", "")`` when no recognisable key is found.

    Args:
        ratings: A non-empty list of projected danger rating dicts.

    Returns:
        ``(danger_key, subdivision_display_char)`` for the peak rating.

    """
    best_key = "low"
    best_sub = ""
    for r in ratings:
        key = r.get("key") or "low"
        sub = r.get("subdivision") or ""
        if key not in _DANGER_ORDER:
            continue
        if _danger_rank_pair(key, sub) > _danger_rank_pair(best_key, best_sub):
            best_key = key
            best_sub = sub
    return best_key, best_sub


def _resolve_partition(
    by_period: dict[str, list[dict[str, Any]]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str] | None:
    """Resolve the partition type and source/destination rating groups.

    Returns a 4-tuple ``(partition_type, source_ratings, dest_ratings,
    partition_label)`` or ``None`` when no two-period split is detected.

    Args:
        by_period: Rating entries grouped by period string.

    Returns:
        ``(partition_type, source_ratings, dest_ratings, partition_label)``
        or ``None`` when only one period is present.

    """
    all_day = by_period.get("all_day")
    later = by_period.get("later")
    earlier = by_period.get("earlier")

    if all_day and later:
        return "temporal", all_day, later, ""

    if earlier and later:
        has_elevation = any(r.get("elevation") for r in earlier + later)
        if has_elevation:
            dest_upper_band = max(
                later,
                key=lambda r: (r.get("elevation") or {}).get("lower") or 0,
            )
            partition_label = _elevation_label_from_rm(dest_upper_band.get("elevation"))
            return "elevation", earlier, later, partition_label
        return "temporal", earlier, later, ""

    return None


def derive_problem_family(render_model: dict[str, Any]) -> str:
    """
    Derive a problem-family label from the dominant avalanche problem type.

    Maps the dominant problem type across all traits to one of five family
    tokens used by the headline matrix to select copy. The ``dominant`` type
    is the first problem type encountered when iterating traits in aggregation
    order (editorial priority order).

    Family mapping:
    - ``wind_slab``, ``new_snow`` → ``"slab"``
    - ``persistent_weak_layers``, ``deep_persistent_weak_layers`` → ``"persistent"``
    - ``wet_snow``, ``gliding_snow`` → ``"wet"``
    - ``cornices``, ``favourable_situation``,
      ``no_distinct_avalanche_problem`` → ``"other"``
    - Two or more distinct families present → ``"mixed"``
    - No problems in the bulletin → ``"other"`` (generic fallback)

    Args:
        render_model: A render model dict as produced by
            :func:`build_render_model`.

    Returns:
        One of ``"slab"``, ``"persistent"``, ``"wet"``, ``"other"``, or
        ``"mixed"``.

    """
    _PROBLEM_TO_FAMILY: dict[str, str] = {
        "wind_slab": "slab",
        "new_snow": "slab",
        "persistent_weak_layers": "persistent",
        "deep_persistent_weak_layers": "persistent",
        "wet_snow": "wet",
        "gliding_snow": "wet",
        "cornices": "other",
        "favourable_situation": "other",
        "no_distinct_avalanche_problem": "other",
    }

    traits: list[dict[str, Any]] = render_model.get("traits") or []
    families: set[str] = set()

    for trait in traits:
        for problem in trait.get("problems") or []:
            pt: str = problem.get("problem_type") or ""
            family = _PROBLEM_TO_FAMILY.get(pt, "other")
            families.add(family)

    if not families:
        return "other"
    if len(families) == 1:
        return next(iter(families))
    return "mixed"


def compute_period_transition(render_model: dict[str, Any]) -> PeriodTransition | None:
    """
    Derive the period transition descriptor from a render model's danger ratings.

    Inspects ``render_model["danger"]["ratings"]`` to determine whether the
    day has a meaningful split between its first and second period, and if so
    whether the danger level rises, falls, or stays the same (flat-but-split).

    The function is source-neutral — it reads only from the projected
    ``danger.ratings`` list, which is populated for all sources.  No
    ``if source == "slf"`` branching appears here.

    **Temporal split (SLF-style)**: a bulletin that carries both ``all_day``
    and ``later`` ratings has a temporal partition.  The ``all_day`` rating
    is the source period; the ``later`` rating is the destination.

    **Elevation split (ALBINA/EUREGIO-style)**: a bulletin that carries
    ``earlier`` + ``later`` ratings (no ``all_day``) may also carry
    per-elevation-band ratings within each period.  The highest-ranked rating
    for the earlier period is the source; the highest-ranked for the later
    period is the destination.

    Returns ``None`` when the render model has no usable ratings or the
    ratings carry fewer than two distinct periods.

    Args:
        render_model: A render model dict as produced by
            :func:`build_render_model`.

    Returns:
        A :class:`PeriodTransition` instance, or ``None`` when no split is
        detected.

    """
    danger_info: dict[str, Any] = render_model.get("danger") or {}
    rm_ratings: list[dict[str, Any]] = danger_info.get("ratings") or []
    if not rm_ratings:
        return None

    # Group ratings by period.
    by_period: dict[str, list[dict[str, Any]]] = {}
    for r in rm_ratings:
        period = r.get("period") or "all_day"
        if r.get("key") not in _DANGER_ORDER:
            continue
        by_period.setdefault(period, []).append(r)

    # Determine partition type and source/destination period groups.
    partition = _resolve_partition(by_period)
    if partition is None:
        return None

    partition_type, source_ratings, dest_ratings, partition_label = partition

    src_key, src_sub = _best_rated(source_ratings)
    dst_key, dst_sub = _best_rated(dest_ratings)

    src_rank = _danger_rank_pair(src_key, src_sub)
    dst_rank = _danger_rank_pair(dst_key, dst_sub)

    if dst_rank > src_rank:
        direction = "rise"
    elif dst_rank < src_rank:
        direction = "fall"
    else:
        direction = "none"

    dst_number = _DANGER_NUMBER.get(dst_key, "1")

    return PeriodTransition(
        direction=direction,
        destination_key=dst_key,
        destination_number=dst_number,
        destination_subdivision=dst_sub,
        partition_type=partition_type,
        partition_label=partition_label,
        has_split=True,
    )
