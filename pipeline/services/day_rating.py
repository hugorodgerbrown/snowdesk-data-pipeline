"""
pipeline/services/day_rating.py — Per-(region, date) danger rating aggregation.

Maintains the RegionDayRating denormalisation table. Each row stores both the
minimum and maximum danger ratings (across all qualifying bulletins) for a
single (region, calendar day) pair.

Tie-break policy (locked in by the architect):
  - ``max_rating`` is the highest mainValue across every qualifying bulletin
    that covers (region, date). "Qualifying" means
    render_model_version >= RENDER_MODEL_VERSION (version=0 rows excluded).
  - ``min_rating`` is the lowest mainValue across the same set.
  - When two bulletins share the same extreme rating, the one whose
    ``valid_from`` is latest is chosen as the subdivision contributor.
  - ``source_bulletin`` always points to the bulletin that supplied
    ``max_rating`` (tie-broken by latest valid_from).
  - Only bulletins explicitly linked to the region via RegionBulletin are
    considered (no cross-region fallback).

This module intentionally does NOT use post_save signals.
Call ``apply_bulletin_day_ratings`` from ``upsert_bulletin`` inline.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from pipeline.services.render_model import RENDER_MODEL_VERSION

if TYPE_CHECKING:
    from pipeline.models import Bulletin, Region

logger = logging.getLogger(__name__)

DAY_RATING_VERSION: int = 1

# Canonical ordering from lowest to highest (mirrors _DANGER_ORDER in views).
_RATING_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

# Map CAAML customData.CH.subdivision strings to the suffix stored in
# RegionDayRating.max_subdivision.
_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}


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


def _extract_max_from_render_model(render_model: dict) -> tuple[str, str]:
    """
    Extract the highest danger rating key and subdivision suffix from a render model.

    Reads ``render_model["danger"]["key"]`` (the pre-computed max) and its
    ``subdivision`` field.

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

    Considers all Bulletin rows linked to ``region`` whose validity window
    overlaps ``day`` and whose ``render_model_version >= RENDER_MODEL_VERSION``
    (version=0 error sentinels are excluded). Writes ``no_rating`` when no
    qualifying bulletin exists.

    Args:
        region: The Region to aggregate for.
        day: The calendar date to aggregate.
        commit: When True (default), upsert the RegionDayRating row.
                When False, log what would be written without touching the DB.

    """
    # Avoid circular import — models is always available at call time.
    from pipeline.models import Bulletin, RegionDayRating

    # All bulletins whose validity window overlaps ``day``.
    candidates = list(
        Bulletin.objects.filter(
            regions=region,
            valid_from__date__lte=day,
            valid_to__date__gte=day,
            render_model_version__gte=RENDER_MODEL_VERSION,
        ).order_by("valid_from")
    )

    best_key: str
    worst_key: str
    if not candidates:
        best_key = RegionDayRating.Rating.NO_RATING
        best_subdivision = ""
        best_bulletin = None
        worst_key = RegionDayRating.Rating.NO_RATING
        worst_subdivision = ""
    else:
        best_key = RegionDayRating.Rating.NO_RATING
        best_rank = -1
        best_valid_from = None
        best_bulletin = None
        best_subdivision = ""

        # Worst tracker: initialise to a sentinel rank above any valid rating so
        # the first real candidate always wins.  Uses len(_RATING_ORDER) as the
        # "above everything" sentinel value.
        worst_key = RegionDayRating.Rating.NO_RATING
        worst_rank = len(_RATING_ORDER)
        worst_valid_from = None
        worst_subdivision = ""

        for bulletin in candidates:
            rm = bulletin.render_model or {}
            key, subdivision = _extract_max_from_render_model(rm)
            rank = _rating_rank(key)

            # Max tracker: pick this bulletin if it has a strictly higher rating,
            # or ties with the current best but has a later valid_from.
            if rank > best_rank or (
                rank == best_rank
                and best_valid_from is not None
                and bulletin.valid_from > best_valid_from
            ):
                best_key = key
                best_rank = rank
                best_valid_from = bulletin.valid_from
                best_bulletin = bulletin
                best_subdivision = subdivision

            # Min tracker: pick this bulletin if it has a strictly lower rank, or
            # ties with the current worst but has a later valid_from.
            # Malformed bulletins (rank == -1) are excluded from the min tracker
            # to avoid promoting an unrecognised key as the minimum.
            if rank >= 0 and (
                rank < worst_rank
                or (
                    rank == worst_rank
                    and worst_valid_from is not None
                    and bulletin.valid_from > worst_valid_from
                )
            ):
                worst_key = key
                worst_rank = rank
                worst_valid_from = bulletin.valid_from
                worst_subdivision = subdivision

        # If every candidate had an unrecognised key (rank == -1), the worst
        # tracker never updated — leave worst as NO_RATING (already set above).

    if not commit:
        logger.info(
            "[read-only] Would write RegionDayRating: region=%s date=%s min=%s max=%s",
            region.region_id,
            day,
            worst_key,
            best_key,
        )
        return

    RegionDayRating.objects.update_or_create(
        region=region,
        date=day,
        defaults={
            "min_rating": worst_key,
            "min_subdivision": worst_subdivision,
            "max_rating": best_key,
            "max_subdivision": best_subdivision,
            "source_bulletin": best_bulletin,
            "version": DAY_RATING_VERSION,
        },
    )
    logger.debug(
        "RegionDayRating upserted: region=%s date=%s rating=%s",
        region.region_id,
        day,
        best_key,
    )


def apply_bulletin_day_ratings(bulletin: "Bulletin") -> None:
    """
    Recompute RegionDayRating for every (region, day) covered by a bulletin.

    Iterates each region linked to the bulletin via RegionBulletin and each
    calendar day in [valid_from.date(), valid_to.date()], calling
    ``recompute_region_day`` for each pair.

    Designed to be called inline from ``upsert_bulletin`` after the
    RegionBulletin links are created.  Callers must wrap this in a
    try/except so that day-rating failures never abort ingest.

    Args:
        bulletin: The Bulletin whose linked (region, date) pairs to refresh.

    """
    start_day = bulletin.valid_from.date()
    end_day = bulletin.valid_to.date()

    # Gather distinct regions linked to this bulletin.
    regions = list(bulletin.regions.all())

    day = start_day
    while day <= end_day:
        for region in regions:
            recompute_region_day(region, day, commit=True)
        day += timedelta(days=1)

    logger.debug(
        "apply_bulletin_day_ratings: bulletin=%s regions=%d days=%d",
        bulletin.bulletin_id,
        len(regions),
        (end_day - start_day).days + 1,
    )
