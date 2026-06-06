"""
bulletins/services/day_rating.py — Per-(region, date) danger rating aggregation.

Maintains the RegionDayRating denormalisation table. Each row stores both the
minimum and maximum danger ratings (within one chosen bulletin) for a single
(region, calendar day) pair.

Aggregation policy (v8 — elevation-band split + afternoon-elevated split + AM/PM):
  - For day X, pick the single bulletin that was most recently published by
    ~10am on day X:
    - Morning-of-X (valid_from.date() == X, hour < 12) takes priority.
    - Prior-evening-of-(X-1) (valid_from.date() == X-1, hour >= 12) is the
      fallback when no morning-of-X bulletin exists.
    - Evening-of-X (valid_from.date() == X, hour >= 12) is excluded — its
      target day is X+1.
  - Formally: keep candidates where ``_target_day(b) == X``; pick the one
    with the latest ``valid_from``.  Because morning-of-X has a later
    ``valid_from`` than prior-evening-of-(X-1), this naturally implements
    the morning-wins / prior-evening-fallback convention.
  - Min/max split logic — checked in this precedence order:

    1. **Elevation-band split (SNOW-293)**: if
       ``render_model["danger"]["ratings"]`` contains two or more entries
       with ``period="all_day"`` and at least two distinct ``key`` values,
       the day carries an elevation-band split (Météo-France style).
       ``min_rating`` = key of the lowest-ranked band (sorted by
       ``_DANGER_KEY_RANK``); ``max_rating`` = key of the highest-ranked
       band. Subdivisions mirror the headline.

    2. **Time-period split (afternoon-elevated)**: during the trait loop,
       collect ``morning_levels`` from traits with
       ``time_period in ("all_day", "earlier")`` and ``afternoon_levels``
       from traits with ``time_period == "later"``. If both lists are
       non-empty and ``max(afternoon_levels) > max(morning_levels)``,
       produce a split: ``min_rating = key of max(morning_levels)``,
       ``max_rating = key of max(afternoon_levels)``.

    3. **Fallback (headline-only)**: both ``min_rating`` and ``max_rating``
       are set to the bulletin's headline ``render_model["danger"]["key"]``
       (the CAAML aggregate). This keeps the heatmap tile in sync with the
       Day Risk Profile panel, which also shows the headline rating (SNOW-138).

  - AM/PM split fields (SNOW-291):
    - ``am_rating`` / ``pm_rating`` are populated whenever both
      ``morning_levels`` AND ``afternoon_levels`` are non-empty, regardless of
      whether the afternoon level is higher than the morning level.  They
      encode the problem-mix split for flat-but-split days (same level, two
      distinct problem categories) in addition to the escalating case.
    - ``am_rating = key of max(morning_levels)``,
      ``pm_rating = key of max(afternoon_levels)``.
    - Both fields stay ``None`` (uniform day) when the bulletin has no
      ``later`` traits, preserving the single-colour calendar tile for those
      rows.
    - Subdivision for both AM and PM mirrors the headline bulletin subdivision
      (same simplification as ``min/max_subdivision``).
    - The AM/PM time split is independent of the elevation-band split — the
      two encode orthogonal structure (time of day vs band) and either, both,
      or neither may apply to a single bulletin.
  - Bulletins with an empty traits list (quiet day) still benefit from the
    elevation-band split check (step 1) because it reads ``danger.ratings``,
    not ``traits``. AM/PM stays ``None`` on quiet days because the time split
    is purely trait-driven.
  - Bulletins with a completely malformed render_model (empty dict or missing
    both ``danger`` and ``traits``) → write ``no_rating``.
  - Traits with missing or non-integer ``danger_level`` are skipped (debug log).
    If all traits are invalid the row is written as ``no_rating``.
  - Qualifying means ``render_model_version >= RENDER_MODEL_VERSION``
    (version=0 error sentinels are excluded).
  - ``source_bulletin`` is always the chosen bulletin (or None when no
    candidate exists).
  - ``max_subdivision`` / ``min_subdivision``: sourced from the chosen
    bulletin's aggregate ``render_model["danger"]["subdivision"]``.

This module intentionally does NOT use post_save signals.
Call ``apply_bulletin_day_ratings`` from ``upsert_bulletin`` inline.
"""

from __future__ import annotations

import datetime
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.utils import timezone

from bulletins.models import Bulletin
from bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    band_label_for_elevation as _band_label,
)

if TYPE_CHECKING:
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)

DAY_RATING_VERSION: int = 8

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

# Map CAAML customData.CH.subdivision strings to the suffix stored in
# RegionDayRating.max_subdivision / min_subdivision.
_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}


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


def _target_day(bulletin: "Bulletin") -> datetime.date:
    """
    Return the calendar day that a bulletin is forecasting.

    SLF publishes two issues per day:

    * **Morning** issue (~07:00 UTC): forecasts **today**.
      ``valid_from.hour < 12`` → target_day = ``valid_from.date()``.
    * **Evening** issue (~16:00 UTC): forecasts **tomorrow**.
      ``valid_from.hour >= 12`` → target_day = ``valid_from.date() + 1 day``.

    The 12:00 UTC boundary is chosen so that noon (the earliest plausible
    "afternoon" publication) falls on the evening side, which is the
    conservative choice: if ever SLF shifts an evening issue to exactly
    noon, we still attribute it to the *next* day.

    This mirrors the morning-wins / prior-evening-fallback convention
    implemented by ``_select_default_issue`` in ``public/views.py``
    (which uses 10:00 UTC as the pivot to prefer the morning update).

    Args:
        bulletin: A Bulletin instance with a timezone-aware ``valid_from``.

    Returns:
        The calendar date that this bulletin is forecasting.

    """
    vf: datetime.datetime = bulletin.valid_from
    if vf.hour < 12:
        return vf.date()
    return (vf + timedelta(days=1)).date()


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
    subdivision = _SUBDIVISION_SUFFIX.get(raw_sub, "")
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


def recompute_region_day(
    region: "MicroRegion",
    day: date,
    *,
    commit: bool = True,
) -> None:
    """
    Recompute and (optionally) persist the RegionDayRating for one (region, day).

    Selects the single bulletin that was most recently published by ~10am on
    ``day`` (the morning-of-day if available; otherwise the prior-evening).
    Aggregates min/max ratings across that bulletin's traits only.

    The SQL pre-filter fetches bulletins with ``valid_from.date`` in
    ``{day, day - 1}`` to capture both the morning-of-day and
    prior-evening candidates; a Python post-filter via ``_target_day``
    then drops any evening-of-day bulletin (whose target is day+1).

    Sets ``min_rating`` / ``max_rating`` using the v8 policy: elevation-band
    split from ``danger.ratings`` (precedence 1), afternoon-elevated trait
    split (precedence 2), or headline-only fallback (precedence 3).
    Quiet-day bulletins (empty traits) still check for an elevation-band
    split before falling back to the headline.

    Writes ``no_rating`` when no qualifying bulletin exists or when the
    chosen bulletin's render_model is entirely malformed.

    Args:
        region: The MicroRegion to aggregate for.
        day: The calendar date to aggregate.
        commit: When True (default), upsert the RegionDayRating row.
                When False, log what would be written without touching the DB.

    """
    from bulletins.models import RegionDayRating

    no_rating = RegionDayRating.Rating.NO_RATING

    # SQL pre-filter: valid_from date in {day, day-1} covers the two possible
    # candidate bulletins for day X (morning-of-X and evening-of-(X-1)).
    pre_candidates = list(
        Bulletin.objects.filter(
            regions=region,
            valid_from__date__in=[day, day - timedelta(days=1)],
            render_model_version__gte=RENDER_MODEL_VERSION,
        )
    )

    # Python post-filter: keep only bulletins whose target day equals ``day``.
    # This drops the evening-of-X bulletin (valid_from.date() == day, hour >= 12)
    # whose target is actually day+1.
    candidates = [b for b in pre_candidates if _target_day(b) == day]

    # Source/bands (SNOW-292): blank/None unless the chosen bulletin is ALBINA.
    source_str: str = ""
    bands: list[dict] | None = None
    # AM/PM split fields (SNOW-291): always start as None (uniform day).
    am_key: str | None = None
    am_subdivision: str = ""
    pm_key: str | None = None
    pm_subdivision: str = ""

    if not candidates:
        min_key: str = no_rating
        min_subdivision: str = ""
        max_key: str = no_rating
        max_subdivision: str = ""
        source_bulletin = None
    else:
        # Single-bulletin policy: pick the candidate with the latest valid_from.
        # When both morning-of-X and prior-evening-of-(X-1) exist, morning-of-X
        # has the later valid_from and is therefore chosen automatically.
        chosen = max(candidates, key=lambda b: b.valid_from)
        rm = chosen.render_model or {}
        headline_key, headline_subdivision = _extract_headline_from_render_model(rm)
        traits: list = rm.get("traits") or []
        rm_ratings: list[dict] = (rm.get("danger") or {}).get("ratings") or []

        # Always copy source from the chosen bulletin's render model.
        source_str = str(rm.get("source") or "")

        if not traits:
            # Quiet day: check for an elevation-band split first; otherwise
            # fall back to the headline danger key.
            band_split = _detect_elevation_band_split(rm_ratings)
            if band_split is not None:
                min_key, max_key = band_split
                logger.debug(
                    "Bulletin %s has empty traits but elevation-band split"
                    " (%r → %r); using band split.",
                    chosen.bulletin_id,
                    min_key,
                    max_key,
                )
            else:
                logger.debug(
                    "Bulletin %s has empty traits; using headline danger key %r.",
                    chosen.bulletin_id,
                    headline_key,
                )
                min_key = headline_key
                max_key = headline_key
            min_subdivision = headline_subdivision
            max_subdivision = headline_subdivision
            source_bulletin = chosen
        else:
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
                chosen.bulletin_id,
                no_rating,
                rm_ratings=rm_ratings,
            )
            source_bulletin = chosen if has_valid else None
            if not has_valid:
                min_key = no_rating
                min_subdivision = ""
                max_key = no_rating
                max_subdivision = ""
                am_key = None
                am_subdivision = ""
                pm_key = None
                pm_subdivision = ""

        # Derive bands for ALBINA bulletins.
        if source_str == Bulletin.Source.ALBINA:
            bands = _derive_albina_bands(rm)

    if not commit:
        logger.info(
            "[read-only] Would write RegionDayRating: region=%s date=%s min=%s max=%s",
            region.region_id,
            day,
            min_key,
            max_key,
        )
        return

    RegionDayRating.objects.update_or_create(
        region=region,
        date=day,
        defaults={
            "min_rating": min_key,
            "min_subdivision": min_subdivision,
            "max_rating": max_key,
            "max_subdivision": max_subdivision,
            "am_rating": am_key,
            "am_subdivision": am_subdivision,
            "pm_rating": pm_key,
            "pm_subdivision": pm_subdivision,
            "source_bulletin": source_bulletin,
            "version": DAY_RATING_VERSION,
            "source": source_str,
            "bands": bands,
        },
    )
    logger.debug(
        "RegionDayRating upserted: region=%s date=%s min=%s max=%s "
        "am=%s pm=%s source=%s",
        region.region_id,
        day,
        min_key,
        max_key,
        am_key,
        pm_key,
        source_str,
    )


def apply_bulletin_day_ratings(bulletin: "Bulletin") -> None:
    """
    Recompute RegionDayRating for the (region, target_day) pairs of a bulletin.

    A bulletin targets exactly one calendar day — determined by ``_target_day``:
    morning issues (valid_from.hour < 12) target their own date; evening issues
    (valid_from.hour >= 12) target the following date.

    For each region linked to the bulletin, calls ``recompute_region_day`` for
    that single target day.  The recompute also pulls in the complementary
    candidate (morning + prior-evening pair) so the chosen bulletin for the day
    is always up to date.

    After recomputing, invalidates the season-calendar fragment cache key for
    each affected region so the next HTMX open re-queries rather than serving
    stale markup. Cache failures are logged and never abort ingest.

    Designed to be called inline from ``upsert_bulletin`` after the
    RegionBulletin links are created.  Callers must wrap this in a
    try/except so that day-rating failures never abort ingest.

    Args:
        bulletin: The Bulletin whose linked (region, target_day) pairs to refresh.

    """
    target = _target_day(bulletin)

    # Gather distinct regions linked to this bulletin.
    regions = list(bulletin.regions.all())

    for region in regions:
        recompute_region_day(region, target, commit=True)

    # Invalidate the season-calendar response cache for each affected region so
    # the next HTMX open re-queries with the freshly written RegionDayRating rows.
    # Keyed to today (the date the response was cached on), not the bulletin's
    # target day — the cache is per-calendar-day, not per-bulletin.
    # Uses make_template_fragment_key to produce the same key that
    # season_calendar_partial stores on a cache miss.
    today_iso = timezone.localdate().isoformat()
    for region in regions:
        cache_key = make_template_fragment_key(
            "season_calendar", [region.canonical_region_id, today_iso]
        )
        try:
            cache.delete(cache_key)
        except Exception:
            logger.exception(
                "Failed to invalidate season_calendar cache key for region=%s;"
                " ingest continues.",
                region.canonical_region_id,
            )

    logger.debug(
        "apply_bulletin_day_ratings: bulletin=%s target_day=%s regions=%d",
        bulletin.bulletin_id,
        target,
        len(regions),
    )
