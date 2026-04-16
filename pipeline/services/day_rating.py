"""
pipeline/services/day_rating.py — Per-(region, date) danger rating aggregation.

Maintains the RegionDayRating denormalisation table. Each row stores both the
minimum and maximum danger ratings (within one chosen bulletin) for a single
(region, calendar day) pair.

Aggregation policy (v4 — single-bulletin):
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
  - Aggregate *within* the chosen bulletin's ``render_model["traits"]``:
    each trait's ``danger_level`` int is mapped to a rating key.
    ``max_rating`` is the highest; ``min_rating`` is the lowest.
  - Bulletins with an empty traits list (quiet day) fall back to the
    bulletin's aggregate ``render_model["danger"]["key"]`` for both min and
    max (debug log).
  - Bulletins with a completely malformed render_model (empty dict or missing
    both ``danger`` and ``traits``) → write ``no_rating``.
  - Traits with missing or non-integer ``danger_level`` are skipped (debug log).
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

from pipeline.services.render_model import RENDER_MODEL_VERSION

if TYPE_CHECKING:
    from pipeline.models import Bulletin, Region

logger = logging.getLogger(__name__)

DAY_RATING_VERSION: int = 4

# Canonical ordering from lowest to highest (mirrors _DANGER_ORDER in render_model).
_RATING_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

# Map trait danger_level int (1–5) to rating key string.
_DANGER_LEVEL_TO_KEY: dict[int, str] = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}

# Map CAAML customData.CH.subdivision strings to the suffix stored in
# RegionDayRating.max_subdivision / min_subdivision.
_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}


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


def _rating_rank(key: str) -> int:
    """
    Return the ordinal rank of a danger rating key.

    Higher is more dangerous. Returns -1 for unrecognised keys so they
    sort below all valid levels.

    Args:
        key: A CAAML mainValue string (e.g. ``"considerable"``).

    Returns:
        Integer rank in [0, len(_RATING_ORDER)-1], or -1.

    """
    try:
        return _RATING_ORDER.index(key)
    except ValueError:
        return -1


def _extract_headline_from_render_model(render_model: dict) -> tuple[str, str]:
    """
    Extract the headline danger key and subdivision suffix from a render model.

    Reads ``render_model["danger"]["key"]`` (the pre-computed aggregate) and its
    ``subdivision`` field. Used as the subdivision source and as the min/max
    value for quiet-day bulletins with empty traits.

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


def recompute_region_day(
    region: "Region",
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

    Aggregates at the trait level within the chosen bulletin: each trait's
    ``danger_level`` int is mapped to a rating key. Bulletins with an empty
    traits list (quiet day) fall back to the bulletin's aggregate
    ``render_model["danger"]["key"]``.

    Writes ``no_rating`` when no qualifying bulletin exists or when the
    chosen bulletin's render_model is entirely malformed.

    Args:
        region: The Region to aggregate for.
        day: The calendar date to aggregate.
        commit: When True (default), upsert the RegionDayRating row.
                When False, log what would be written without touching the DB.

    """
    # Avoid circular import — models is always available at call time.
    from pipeline.models import Bulletin, RegionDayRating

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

        if not traits:
            # Quiet day: no traits — fall back to headline danger key.
            logger.debug(
                "Bulletin %s has empty traits; using headline danger key %r.",
                chosen.bulletin_id,
                headline_key,
            )
            min_key = headline_key
            min_subdivision = headline_subdivision
            max_key = headline_key
            max_subdivision = headline_subdivision
            source_bulletin = chosen
        else:
            # Extract valid trait danger levels from the chosen bulletin only.
            valid_keys: list[str] = []
            for trait in traits:
                raw_level = trait.get("danger_level")
                if (
                    not isinstance(raw_level, int)
                    or raw_level not in _DANGER_LEVEL_TO_KEY
                ):
                    logger.debug(
                        "Bulletin %s trait has missing/invalid danger_level"
                        " %r; skipping.",
                        chosen.bulletin_id,
                        raw_level,
                    )
                    continue
                valid_keys.append(_DANGER_LEVEL_TO_KEY[raw_level])

            if not valid_keys:
                # All trait levels were invalid — no usable data.
                min_key = no_rating
                min_subdivision = ""
                max_key = no_rating
                max_subdivision = ""
                source_bulletin = None
            else:
                max_key = max(valid_keys, key=_rating_rank)
                min_key = min(valid_keys, key=_rating_rank)
                max_subdivision = headline_subdivision
                min_subdivision = headline_subdivision
                source_bulletin = chosen

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
            "source_bulletin": source_bulletin,
            "version": DAY_RATING_VERSION,
        },
    )
    logger.debug(
        "RegionDayRating upserted: region=%s date=%s min=%s max=%s",
        region.region_id,
        day,
        min_key,
        max_key,
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

    logger.debug(
        "apply_bulletin_day_ratings: bulletin=%s target_day=%s regions=%d",
        bulletin.bulletin_id,
        target,
        len(regions),
    )
