"""
pipeline/migrations/0007_backfill_regiondayrating.py — Data migration.

Backfills RegionDayRating for every (region, date) pair covered by an
existing Bulletin whose render_model_version >= 3 (RENDER_MODEL_VERSION).

This migration is idempotent: it uses update_or_create so re-running is safe.
Version=0 rows (render model build failures) and stale-version rows are excluded
from aggregation, consistent with the live service in day_rating.py.

Both min_rating/min_subdivision and max_rating/max_subdivision are computed in
a single pass over the candidates for each (region, date) pair.  Tie-break for
both min and max: latest valid_from wins when multiple bulletins share the same
extreme rating.

The computation is inlined here (no import of service modules) so this
migration remains schema-version-safe regardless of future service changes.
The reverse operation is a no-op: existing rows are not deleted on rollback
because they may have been updated by subsequent ingest runs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import migrations

logger = logging.getLogger(__name__)

# Canonical ordering from lowest to highest.
_RATING_ORDER = ("low", "moderate", "considerable", "high", "very_high")

_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}


def _rating_rank(key: str) -> int:
    """Return ordinal rank (higher = more dangerous). -1 for unknown keys."""
    try:
        return _RATING_ORDER.index(key)
    except ValueError:
        return -1


def _extract_danger_from_render_model(render_model: dict) -> tuple[str, str]:
    """
    Extract the highest danger key and subdivision from a stored render model.

    Returns (key, subdivision) where key defaults to "low" and subdivision
    to "" when the render model lacks the expected fields.
    """
    danger = render_model.get("danger") or {}
    key: str = danger.get("key") or "low"
    raw_sub: str = danger.get("subdivision") or ""
    subdivision = _SUBDIVISION_SUFFIX.get(raw_sub, "")
    return key, subdivision


def backfill_day_ratings(apps, schema_editor):
    """
    Backfill RegionDayRating rows for all qualifying existing bulletins.

    Qualifying means render_model_version >= 3 (matches RENDER_MODEL_VERSION in
    pipeline/services/render_model.py — update this literal when the version is bumped).
    For each (region, date) pair, picks the bulletin with the highest danger
    rating as max_source_bulletin and the one with the lowest rating as the
    min contributor.  Ties (equal rating) are broken by latest valid_from for
    both trackers.
    """
    Bulletin = apps.get_model("pipeline", "Bulletin")
    RegionDayRating = apps.get_model("pipeline", "RegionDayRating")
    RegionBulletin = apps.get_model("pipeline", "RegionBulletin")

    # Collect all qualifying bulletins (version >= 3; excludes error sentinels and
    # stale rows from earlier render model versions).
    # 3 == RENDER_MODEL_VERSION in pipeline/services/render_model.py — see module comment.
    qualifying = list(
        Bulletin.objects.filter(render_model_version__gte=3).order_by("valid_from")
    )

    # Build a mapping: (region_id, date) -> list of (rank, valid_from, key, subdivision, bulletin)
    pairs: dict[tuple, list] = defaultdict(list)

    for bulletin in qualifying:
        rm = bulletin.render_model or {}
        key, subdivision = _extract_danger_from_render_model(rm)
        rank = _rating_rank(key)

        # Find all regions linked to this bulletin.
        region_links = list(RegionBulletin.objects.filter(bulletin=bulletin))

        start_day = bulletin.valid_from.date()
        end_day = bulletin.valid_to.date()

        day = start_day
        while day <= end_day:
            for link in region_links:
                pair_key = (link.region_id, day)
                pairs[pair_key].append(
                    (rank, bulletin.valid_from, key, subdivision, bulletin)
                )
            day += timedelta(days=1)

    created = 0
    for (region_id, day), candidates in pairs.items():
        # Single pass: track best (highest rank, tie: latest valid_from) and
        # worst (lowest rank, tie: latest valid_from).
        best = max(candidates, key=lambda c: (c[0], c[1]))
        worst = min(candidates, key=lambda c: (c[0], -c[1].timestamp()))
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
                "version": 1,
            },
        )
        created += 1

    logger.info("Backfilled %d RegionDayRating rows", created)


def noop(apps, schema_editor):
    """Reverse migration: no-op — do not delete existing day ratings."""
    pass


class Migration(migrations.Migration):
    """Backfill RegionDayRating rows from existing bulletins."""

    dependencies = [
        ("pipeline", "0006_regiondayrating"),
    ]

    operations = [
        migrations.RunPython(
            backfill_day_ratings, reverse_code=migrations.RunPython.noop
        ),
    ]
