"""
apps/ingest/normalise/day_rating.py — A bulletin's day rating, from its render model.

The service's half of ``RegionDayRating`` (SNOW-1270): which day a bulletin
forecasts (``target_day_for_valid_from``) and what danger range it gives
that day (``rate_render_model``). Both are pure functions of the bulletin,
moved here from ``apps/bulletins/services/day_rating.py`` because reading a
bulletin is the service's job (ADR
``reference-data-has-one-source-of-truth.md``). The instance keeps the
selection of a region's governing bulletin for a day, the table and its
cache, in ``apps.bulletins.services.day_rating``, whose module docstring
states the full v9 policy.

``rate_render_model`` applies, to one chosen bulletin's render model:

1. an elevation-band split (``danger.ratings`` holds two or more
   ``all_day`` entries with distinct keys) → min = lowest band, max =
   highest;
2. otherwise an afternoon-elevated split (a ``later`` trait above every
   morning one) → min = morning peak, max = afternoon peak;
3. otherwise the headline key for both;

plus AM/PM keys whenever there are both morning and afternoon traits, the
ALBINA elevation-band breakdown, and ``no_rating`` when no trait carries a
usable level. Every rule is unchanged from the app's version.
"""

from __future__ import annotations

import datetime
import logging
from datetime import timedelta
from typing import TypedDict

from apps.ingest.normalise.render_model import (
    band_label_for_elevation as _band_label,
)
from apps.ingest.runs import PROVIDER_ALBINA

logger = logging.getLogger(__name__)

# ``RegionDayRating.Rating.NO_RATING``'s value: no qualifying bulletin, or
# none of its traits carried a usable level.
NO_RATING = "no_rating"

# v9 (SNOW-1054): the headline subdivision is read through. v8 re-mapped the
# render model's display character through a table keyed on the raw CAAML
# tokens, matched nothing, and stored "" on every row.
DAY_RATING_VERSION: int = 9

# Map trait danger_level int (1–5) to rating key string.
_DANGER_LEVEL_TO_KEY: dict[int, str] = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}

# Rank order for danger key strings (low=1 … very_high=5).
# Used to sort elevation-band ratings from weakest to strongest.
_DANGER_KEY_RANK: dict[str, int] = {v: k for k, v in _DANGER_LEVEL_TO_KEY.items()}

# The suffixes RegionDayRating.*_subdivision may hold. The render model has
# already resolved SLF's raw token (``minus``/``neutral``/``plus``) to one of
# these, so they are carried through as-is; anything else stores "".
_SUBDIVISION_SUFFIXES: frozenset[str] = frozenset({"-", "=", "+"})


def _detect_elevation_band_split(
    rm_ratings: list[dict],
) -> tuple[str, str] | None:
    """
    Detect an all-day elevation-band split from ``danger.ratings`` entries.

    Returns ``(min_key, max_key)`` when two or more ``all_day`` entries carry
    at least two distinct ``key`` values (Météo-France style elevation split).
    Returns ``None`` when no split is detected (single key, no ``all_day``
    entries, or the ``rm_ratings`` list is absent / empty).

    Args:
        rm_ratings: The ``danger.ratings`` list from the render model.

    Returns:
        ``(min_key, max_key)`` tuple or ``None``.

    """
    all_day_entries = [
        r for r in rm_ratings if (r.get("period") or "all_day") == "all_day"
    ]
    if len(all_day_entries) < 2:
        return None
    keys: set[str] = {
        k
        for r in all_day_entries
        if (k := r.get("key")) is not None and k in _DANGER_KEY_RANK
    }
    if len(keys) < 2:
        return None
    ranked = sorted(keys, key=lambda k: _DANGER_KEY_RANK[k])
    return ranked[0], ranked[-1]


def target_day_for_valid_from(valid_from: datetime.datetime) -> datetime.date:
    """
    Return the calendar day forecast by a bulletin with this ``valid_from``.

    Providers publish two issues per day:

    * **Morning** issue (~07:00 UTC): forecasts **today**.
      ``valid_from.hour < 12`` → target_day = ``valid_from.date()``.
    * **Evening** issue (~16:00 UTC): forecasts **tomorrow**.
      ``valid_from.hour >= 12`` → target_day = ``valid_from.date() + 1 day``.

    The 12:00 UTC boundary is chosen so that noon (the earliest plausible
    "afternoon" publication) falls on the evening side, which is the
    conservative choice: if ever a provider shifts an evening issue to
    exactly noon, we still attribute it to the *next* day.

    It is the one rule for which day a bulletin belongs to: the map fill
    and the calendar read it here, and every panel and page reads it
    through ``apps.bulletins.services.selection`` (SNOW-1207).

    Takes the timestamp rather than a ``Bulletin`` so the rule can also be
    applied to a raw CAAML payload before any row exists — the golden-week
    seeder (``bulletins/services/golden_week.py``) selects records by target
    day straight from the on-disk archives, and must agree with the day a
    persisted bulletin will later be rated against.

    Args:
        valid_from: The bulletin's timezone-aware ``validTime.startTime``.

    Returns:
        The calendar date that this bulletin is forecasting.

    """
    if valid_from.hour < 12:
        return valid_from.date()
    return (valid_from + timedelta(days=1)).date()


def _extract_headline_from_render_model(render_model: dict) -> tuple[str, str]:
    """
    Extract the headline danger key and subdivision suffix from a render model.

    Reads ``render_model["danger"]["key"]`` (the pre-computed aggregate) and its
    ``subdivision`` field. Used as the single rating value written to both
    ``min_rating`` and ``max_rating`` for every bulletin with valid traits.

    Args:
        render_model: A render model dict produced by build_render_model.

    Returns:
        A ``(rating_key, subdivision)`` tuple. ``rating_key`` defaults to
        ``"low"`` when nothing usable is found; ``subdivision`` defaults to
        ``""``.

    """
    danger = render_model.get("danger") or {}
    key: str = danger.get("key") or "low"
    raw_sub: str = danger.get("subdivision") or ""
    subdivision = raw_sub if raw_sub in _SUBDIVISION_SUFFIXES else ""
    return key, subdivision


def _resolve_min_max_keys(
    morning_levels: list[int],
    afternoon_levels: list[int],
    headline_key: str,
    headline_subdivision: str,
    rm_ratings: list[dict] | None = None,
) -> tuple[str, str, str, str]:
    """
    Resolve the min/max rating keys and subdivisions.

    Checks in this precedence order:

    1. **Elevation-band split**: when ``rm_ratings`` carries two or more
       ``all_day`` entries with distinct ``key`` values (Météo-France style),
       return ``min`` = lowest-band key, ``max`` = highest-band key.
       This takes precedence over the time-period split.

    2. **Time-period split**: when afternoon traits are strictly more dangerous
       than morning/all-day traits, produce a split: ``min`` = morning max,
       ``max`` = afternoon max.

    3. **Fallback**: headline-only, so the heatmap tile stays in sync with the
       Day Risk Profile panel (SNOW-138).

    Both subdivision fields always mirror the headline bulletin subdivision
    because neither the trait list nor the ``danger.ratings`` entries carry
    per-period subdivision data.

    Args:
        morning_levels: Danger-level ints from ``all_day`` / ``earlier`` traits.
        afternoon_levels: Danger-level ints from ``later`` traits.
        headline_key: The pre-computed aggregate key from the render model.
        headline_subdivision: The subdivision suffix from the render model.
        rm_ratings: The ``danger.ratings`` list from the render model, or
            ``None`` / empty to skip the elevation-band check (defensive
            for older render models that predate the field).

    Returns:
        A ``(min_key, min_subdivision, max_key, max_subdivision)`` tuple.

    """
    # 1. Elevation-band split (highest precedence — Météo-France style).
    if rm_ratings:
        band_split = _detect_elevation_band_split(rm_ratings)
        if band_split is not None:
            min_key, max_key = band_split
            return min_key, headline_subdivision, max_key, headline_subdivision

    # 2. Time-period split (afternoon-elevated).
    if (
        morning_levels
        and afternoon_levels
        and max(afternoon_levels) > max(morning_levels)
    ):
        return (
            _DANGER_LEVEL_TO_KEY[max(morning_levels)],
            headline_subdivision,
            _DANGER_LEVEL_TO_KEY[max(afternoon_levels)],
            headline_subdivision,
        )

    # 3. Fallback: headline-only.
    return headline_key, headline_subdivision, headline_key, headline_subdivision


def _resolve_am_pm_keys(
    morning_levels: list[int],
    afternoon_levels: list[int],
    headline_subdivision: str,
) -> tuple[str | None, str, str | None, str]:
    """
    Resolve the AM/PM rating keys and subdivisions for the time-split calendar tile.

    AM/PM are populated whenever both ``morning_levels`` AND ``afternoon_levels``
    are non-empty — regardless of whether the afternoon level is strictly higher
    than the morning level.  This captures flat-but-split days (same danger
    level, different problem mix) in addition to the escalating case (SNOW-291).

    When either list is empty (uniform day, no time split), both fields stay
    ``None``/``""`` so the calendar tile stays a single-colour circle.

    Subdivision for both halves mirrors the headline bulletin subdivision
    (same simplification as the existing min/max path).

    Args:
        morning_levels: Danger-level ints from ``all_day`` / ``earlier`` traits.
        afternoon_levels: Danger-level ints from ``later`` traits.
        headline_subdivision: The subdivision suffix from the render model.

    Returns:
        A ``(am_key, am_subdivision, pm_key, pm_subdivision)`` tuple.
        ``am_key`` and ``pm_key`` are ``None`` when either list is empty.

    """
    if morning_levels and afternoon_levels:
        return (
            _DANGER_LEVEL_TO_KEY[max(morning_levels)],
            headline_subdivision,
            _DANGER_LEVEL_TO_KEY[max(afternoon_levels)],
            headline_subdivision,
        )
    return None, "", None, ""


def _derive_albina_bands(render_model: dict) -> list[dict] | None:
    """
    Derive the elevation-band breakdown list from an ALBINA render model.

    Reads ``render_model["danger"]["ratings"]`` — each rating carries a
    ``period``, ``key``, and ``elevation`` dict.  For ALBINA bulletins the
    ratings already reflect the per-band split, so one band entry is emitted
    per distinct ``(band_id, period)`` pair.

    The returned list is sorted into a deterministic canonical order matching
    the CSS grid expectation:
      - Primary:   time period — ``earlier`` (0) → ``all_day`` (1) → ``later`` (2)
      - Secondary: elevation band — high band first (``above-*`` / ``above-treeline``),
                   low band second (``below-*`` / ``below-treeline``)

    This canonical order is required so the 2×2 CSS grid renders quadrants in
    the correct positions regardless of the order the source CAAML lists ratings.
    Without sorting, a CAAML file that lists ``later`` ratings before ``earlier``
    ones would silently swap the grid quadrants.

    Returns ``None`` when the render model carries no ratings, or when no
    rating has an elevation (constant-danger ALBINA bulletin).

    Args:
        render_model: A render model dict for an ALBINA bulletin.

    Returns:
        A sorted list of band dicts or ``None``.

    """
    ratings: list[dict] = (render_model.get("danger") or {}).get("ratings") or []
    if not ratings:
        return None

    bands: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for rating in ratings:
        elevation = rating.get("elevation")
        # Build a synthetic problem-like dict to reuse band_id_for_problem.
        # The elevation shape from _parse_elevation uses lower/upper ints and
        # treeline bool — we need to reconstruct the raw CAAML shape.
        band_id = _elevation_to_band_id(elevation)
        period: str = rating.get("period") or "all_day"
        key = (band_id, period)
        if key in seen:
            continue
        seen.add(key)
        label = _band_label(elevation)
        rating_key: str = rating.get("key") or "low"
        bands.append(
            {
                "band_id": band_id,
                "label": label,
                "rating_key": rating_key,
                "time_period": period,
            }
        )

    if not bands:
        return None

    # Sort bands into canonical order so the CSS grid quadrants are always correct.
    # Time-period order: earlier (0) < all_day (1) < later (2).
    _PERIOD_ORDER: dict[str, int] = {"earlier": 0, "all_day": 1, "later": 2}

    # Band order within a period: high band (has lower bound) before low band
    # (has upper bound).  Bands beginning with "above" sort before "below".
    # Unknown slugs fall last (sort value 2).
    def _band_sort_key(band: dict) -> tuple[int, int]:
        """Return a (period_order, band_order) sort tuple for a band dict."""
        period_val = _PERIOD_ORDER.get(band.get("time_period") or "all_day", 1)
        bid = band.get("band_id") or ""
        if bid.startswith("above"):
            band_val = 0
        elif bid.startswith("below"):
            band_val = 1
        else:
            band_val = 2
        return (period_val, band_val)

    bands.sort(key=_band_sort_key)
    return bands


def _elevation_to_band_id(elevation: dict | None) -> str:
    """
    Derive a band_id slug from a parsed elevation dict (render model format).

    The render model elevation shape uses int ``lower``/``upper`` and a
    bool ``treeline`` flag.  This mirrors the logic in ``band_id_for_problem``
    but operates on the already-parsed elevation dict rather than raw CAAML.

    Args:
        elevation: Parsed elevation dict with ``lower``, ``upper``,
            ``treeline``, and ``treeline_side`` keys, or ``None``.

    Returns:
        A hyphenated slug string identifying the elevation band.

    """
    if not elevation:
        return "all-elevations"

    treeline: bool = elevation.get("treeline", False)
    treeline_side: str | None = elevation.get("treeline_side")

    if treeline:
        if treeline_side == "lower":
            return "above-treeline"
        return "below-treeline"

    lower: int | None = elevation.get("lower")
    upper: int | None = elevation.get("upper")

    if lower is not None and upper is not None:
        return f"{lower}-to-{upper}"
    if lower is not None:
        return f"above-{lower}"
    if upper is not None:
        return f"below-{upper}"
    return "all-elevations"


def _compute_min_max_from_traits(
    traits: list,
    headline_key: str,
    headline_subdivision: str,
    bulletin_id: str,
    no_rating: str,
    rm_ratings: list[dict] | None = None,
) -> tuple[str, str, str, str, str | None, str, str | None, str, bool]:
    """
    Scan traits and resolve min/max + AM/PM rating keys and subdivisions.

    Iterates the trait list, buckets valid levels into morning/afternoon,
    then delegates to ``_resolve_min_max_keys`` (which applies the v8
    precedence chain: elevation-band split → afternoon-elevated → headline)
    and ``_resolve_am_pm_keys`` (flat-but-split AM/PM).  Returns a 9-tuple of
    ``(min_key, min_sub, max_key, max_sub, am_key, am_sub, pm_key, pm_sub,
    has_valid_trait)``.

    Args:
        traits: The ``render_model["traits"]`` list.
        headline_key: The bulletin's aggregate danger key (fallback).
        headline_subdivision: The bulletin's aggregate subdivision (fallback).
        bulletin_id: Used only in debug log messages.
        no_rating: The ``no_rating`` sentinel string from ``RegionDayRating.Rating``.
        rm_ratings: The ``danger.ratings`` list from the render model, used by
            ``_resolve_min_max_keys`` to detect an all-day elevation-band split.
            Pass ``None`` / empty to skip the elevation-band check.

    Returns:
        A 9-tuple where the last element is ``True`` when at least one trait
        carried a valid ``danger_level`` integer.  The AM/PM keys are ``None``
        on uniform days (no afternoon split).

    """
    morning_levels: list[int] = []
    afternoon_levels: list[int] = []
    has_valid_trait = False
    for trait in traits:
        raw_level = trait.get("danger_level")
        if not isinstance(raw_level, int) or raw_level not in _DANGER_LEVEL_TO_KEY:
            logger.debug(
                "Bulletin %s trait has missing/invalid danger_level %r; skipping.",
                bulletin_id,
                raw_level,
            )
            continue
        has_valid_trait = True
        if trait.get("time_period", "all_day") == "later":
            afternoon_levels.append(raw_level)
        else:
            morning_levels.append(raw_level)

    if not has_valid_trait:
        return no_rating, "", no_rating, "", None, "", None, "", False

    min_key, min_sub, max_key, max_sub = _resolve_min_max_keys(
        morning_levels,
        afternoon_levels,
        headline_key,
        headline_subdivision,
        rm_ratings=rm_ratings,
    )
    am_key, am_sub, pm_key, pm_sub = _resolve_am_pm_keys(
        morning_levels, afternoon_levels, headline_subdivision
    )
    return min_key, min_sub, max_key, max_sub, am_key, am_sub, pm_key, pm_sub, True


class DayRating(TypedDict):
    """The danger range one bulletin gives the day it forecasts.

    The ``RegionDayRating`` columns a chosen bulletin decides, plus
    ``rated``: False when no trait carried a usable level, in which case
    every key is ``no_rating`` and the row names no source bulletin.
    """

    min_rating: str
    min_subdivision: str
    max_rating: str
    max_subdivision: str
    am_rating: str | None
    am_subdivision: str
    pm_rating: str | None
    pm_subdivision: str
    source: str
    bands: list[dict] | None
    rated: bool


def unrated_day() -> DayRating:
    """Return the rating of a day no bulletin governs.

    Returns:
        Every key ``no_rating``, no AM/PM split, no source, not rated.

    """
    return DayRating(
        min_rating=NO_RATING,
        min_subdivision="",
        max_rating=NO_RATING,
        max_subdivision="",
        am_rating=None,
        am_subdivision="",
        pm_rating=None,
        pm_subdivision="",
        source="",
        bands=None,
        rated=False,
    )


def rate_render_model(render_model: dict | None, bulletin_id: str) -> DayRating:
    """Return the danger range a chosen bulletin gives the day it forecasts.

    Args:
        render_model: The chosen bulletin's render model.
        bulletin_id: Its id, for the debug log only.

    Returns:
        The day's rating (see the module docstring for the policy).

    """
    rm = render_model or {}
    headline_key, headline_subdivision = _extract_headline_from_render_model(rm)
    traits: list = rm.get("traits") or []
    rm_ratings: list[dict] = (rm.get("danger") or {}).get("ratings") or []
    source = str(rm.get("source") or "")
    bands = _derive_albina_bands(rm) if source == PROVIDER_ALBINA else None

    if not traits:
        # Quiet day: an elevation-band split first, otherwise the headline.
        band_split = _detect_elevation_band_split(rm_ratings)
        if band_split is not None:
            min_key, max_key = band_split
            logger.debug(
                "Bulletin %s has empty traits but elevation-band split"
                " (%r → %r); using band split.",
                bulletin_id,
                min_key,
                max_key,
            )
        else:
            logger.debug(
                "Bulletin %s has empty traits; using headline danger key %r.",
                bulletin_id,
                headline_key,
            )
            min_key = max_key = headline_key
        return DayRating(
            min_rating=min_key,
            min_subdivision=headline_subdivision,
            max_rating=max_key,
            max_subdivision=headline_subdivision,
            am_rating=None,
            am_subdivision="",
            pm_rating=None,
            pm_subdivision="",
            source=source,
            bands=bands,
            rated=True,
        )

    (
        min_key,
        min_subdivision,
        max_key,
        max_subdivision,
        am_key,
        am_subdivision,
        pm_key,
        pm_subdivision,
        has_valid,
    ) = _compute_min_max_from_traits(
        traits,
        headline_key,
        headline_subdivision,
        bulletin_id,
        NO_RATING,
        rm_ratings=rm_ratings,
    )
    if not has_valid:
        return DayRating(**{**unrated_day(), "source": source, "bands": bands})
    return DayRating(
        min_rating=min_key,
        min_subdivision=min_subdivision,
        max_rating=max_key,
        max_subdivision=max_subdivision,
        am_rating=am_key,
        am_subdivision=am_subdivision,
        pm_rating=pm_key,
        pm_subdivision=pm_subdivision,
        source=source,
        bands=bands,
        rated=True,
    )
