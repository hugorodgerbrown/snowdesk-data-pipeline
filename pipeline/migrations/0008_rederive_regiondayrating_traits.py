"""
pipeline/migrations/0008_rederive_regiondayrating_traits.py — Data migration.

Re-derives RegionDayRating rows using trait-level min/max aggregation
(DAY_RATING_VERSION = 2). Version 1 rows used the bulletin's aggregate headline
``render_model["danger"]["key"]``; this migration re-reads each trait's
``danger_level`` integer to compute the true spread across a day.

Idempotent: uses update_or_create. Reverse is a no-op.

The computation is inlined (no import of service modules) so this migration
remains schema-version-safe regardless of future service changes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import migrations

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inlined constants (do not import from service modules)
# ---------------------------------------------------------------------------

# Canonical ordering from lowest to highest.
_RATING_ORDER = ("low", "moderate", "considerable", "high", "very_high")

# Map trait danger_level int (1–5) to rating key string.
_DANGER_LEVEL_TO_KEY: dict[int, str] = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}

_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}

# Current render model version — update this literal when RENDER_MODEL_VERSION bumps.
_RENDER_MODEL_VERSION = 3

# Target day-rating version written by this migration.
_DAY_RATING_VERSION = 2


def _rating_rank(key: str) -> int:
    """Return ordinal rank (higher = more dangerous). -1 for unknown keys."""
    try:
        return _RATING_ORDER.index(key)
    except ValueError:
        return -1


def _extract_headline(render_model: dict) -> tuple[str, str]:
    """
    Return the aggregate headline (key, subdivision) from a render model.

    Used as the subdivision source and as the fallback value for bulletins
    with empty traits (quiet days).
    """
    danger = render_model.get("danger") or {}
    key: str = danger.get("key") or "low"
    raw_sub: str = danger.get("subdivision") or ""
    subdivision = _SUBDIVISION_SUFFIX.get(raw_sub, "")
    return key, subdivision


def _contributions_for_bulletin(
    render_model: dict,
) -> list[tuple[str, str]]:
    """
    Return (rating_key, subdivision) contributions for a single bulletin.

    Empty traits → single headline-key entry (quiet-day fallback).
    Non-empty traits → one entry per trait with a valid danger_level int.
    Returns an empty list when all traits have invalid danger_level.
    """
    headline_key, headline_sub = _extract_headline(render_model)
    traits = render_model.get("traits") or []

    if not traits:
        return [(headline_key, headline_sub)]

    result = []
    for trait in traits:
        raw_level = trait.get("danger_level")
        if isinstance(raw_level, int) and raw_level in _DANGER_LEVEL_TO_KEY:
            result.append((_DANGER_LEVEL_TO_KEY[raw_level], headline_sub))
    return result


def _accumulate_pairs(qualifying, RegionBulletin, pairs):
    """
    Populate pairs dict: (region_id, date) → list of contribution tuples.

    Each tuple is (rank, valid_from, key, subdivision, bulletin).
    """
    for bulletin in qualifying:
        rm = bulletin.render_model or {}
        contributions = _contributions_for_bulletin(rm)

        if not contributions:
            continue

        region_links = list(RegionBulletin.objects.filter(bulletin=bulletin))
        start_day = bulletin.valid_from.date()
        end_day = bulletin.valid_to.date()

        day = start_day
        while day <= end_day:
            for link in region_links:
                pair_key = (link.region_id, day)
                for key, subdivision in contributions:
                    rank = _rating_rank(key)
                    pairs[pair_key].append(
                        (rank, bulletin.valid_from, key, subdivision, bulletin)
                    )
            day += timedelta(days=1)


def rederive_day_ratings(apps, schema_editor):
    """
    Re-derive RegionDayRating rows from trait-level danger_level integers.

    For each qualifying bulletin (render_model_version >= 3), iterates traits
    and reads each trait's ``danger_level`` int (1–5). Bulletins with empty
    traits fall back to the headline ``render_model["danger"]["key"]``.
    Writes min_rating/max_rating from the trait-level spread and stamps version=2.
    """
    Bulletin = apps.get_model("pipeline", "Bulletin")
    RegionDayRating = apps.get_model("pipeline", "RegionDayRating")
    RegionBulletin = apps.get_model("pipeline", "RegionBulletin")

    qualifying = list(
        Bulletin.objects.filter(
            render_model_version__gte=_RENDER_MODEL_VERSION
        ).order_by("valid_from")
    )

    # Build: (region_id, date) -> list of (rank, valid_from, key, subdivision, bulletin)
    pairs: dict[tuple, list] = defaultdict(list)
    _accumulate_pairs(qualifying, RegionBulletin, pairs)

    upserted = 0
    for (region_id, day), all_contributions in pairs.items():
        # Best (max): highest rank, tie-break by latest valid_from.
        best = max(all_contributions, key=lambda c: (c[0], c[1]))
        # Worst (min): lowest rank among valid (rank >= 0), tie-break by latest valid_from.
        valid_contributions = [c for c in all_contributions if c[0] >= 0]
        if valid_contributions:
            worst = min(valid_contributions, key=lambda c: (c[0], -c[1].timestamp()))
        else:
            worst = best  # all unrecognised — shouldn't happen after level checks

        _, _, best_key, best_subdivision, best_bulletin = best
        _, _, worst_key, worst_subdivision, _ = worst

        RegionDayRating.objects.update_or_create(
            region_id=region_id,
            date=day,
            defaults={
                "min_rating": worst_key,
                "min_subdivision": worst_subdivision,
                "max_rating": best_key,
                "max_subdivision": best_subdivision,
                "source_bulletin": best_bulletin,
                "version": _DAY_RATING_VERSION,
            },
        )
        upserted += 1

    logger.info(
        "0008 rederive_day_ratings: upserted %d RegionDayRating rows (trait-level v2)",
        upserted,
    )


class Migration(migrations.Migration):
    """Re-derive RegionDayRating rows using trait-level danger_level aggregation."""

    dependencies = [
        ("pipeline", "0007_backfill_regiondayrating"),
    ]

    operations = [
        migrations.RunPython(
            rederive_day_ratings, reverse_code=migrations.RunPython.noop
        ),
    ]
