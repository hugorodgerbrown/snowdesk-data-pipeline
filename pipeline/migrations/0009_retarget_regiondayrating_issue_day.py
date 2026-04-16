"""
pipeline/migrations/0009_retarget_regiondayrating_issue_day.py — Data migration.

Re-derives all RegionDayRating rows under the v3 target-day semantic.

The v1/v2 migrations used a validity-range-overlap filter to assign bulletins
to days.  This was wrong for SLF bulletins because the evening issue of day X
(valid_from on day X, hour >= 12:00 UTC) forecasts day X+1, not day X.  The
v3 rule:

  - valid_from.hour < 12  → morning issue → target_day = valid_from.date()
  - valid_from.hour >= 12 → evening issue → target_day = valid_from.date() + 1

For rows where the new aggregation has zero candidates (because the only
contributing bulletin was an evening-of-X issue, whose target is now X+1),
the row is set to no_rating with source_bulletin=None rather than deleted.
This keeps the (region, date) tile stable in the calendar widget — it renders
as grey/unclickable rather than appearing as a gap.

Idempotent via update_or_create.  Reverse is a no-op.  Stamps version=3.

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
# Inlined constants — do not import from service modules
# ---------------------------------------------------------------------------

_RATING_ORDER = ("low", "moderate", "considerable", "high", "very_high")

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
_DAY_RATING_VERSION = 3

_NO_RATING = "no_rating"


def _rating_rank(key: str) -> int:
    """Return ordinal rank (higher = more dangerous). -1 for unknown keys."""
    try:
        return _RATING_ORDER.index(key)
    except ValueError:
        return -1


def _target_day(bulletin) -> object:
    """
    Return the calendar date that a bulletin is forecasting.

    morning issue (valid_from.hour < 12)  → valid_from.date()
    evening issue (valid_from.hour >= 12) → valid_from.date() + 1 day
    """
    vf = bulletin.valid_from
    if vf.hour < 12:
        return vf.date()
    return (vf + timedelta(days=1)).date()


def _extract_headline(render_model: dict) -> tuple[str, str]:
    """Return the aggregate (key, subdivision) from a render model."""
    danger = render_model.get("danger") or {}
    key: str = danger.get("key") or "low"
    raw_sub: str = danger.get("subdivision") or ""
    subdivision = _SUBDIVISION_SUFFIX.get(raw_sub, "")
    return key, subdivision


def _contributions_for_bulletin(render_model: dict) -> list[tuple[str, str]]:
    """
    Return (rating_key, subdivision) contributions for a single bulletin.

    Empty traits → single headline-key entry (quiet-day fallback).
    Non-empty traits → one entry per trait with a valid danger_level int.
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


def _build_pairs(qualifying, RegionBulletin) -> dict:
    """
    Accumulate trait contributions keyed by (region_id, target_day).

    Returns a defaultdict(list) where each entry is a list of
    (rank, valid_from, key, subdivision, bulletin) tuples.
    """
    pairs: dict[tuple, list] = defaultdict(list)
    for bulletin in qualifying:
        rm = bulletin.render_model or {}
        contributions = _contributions_for_bulletin(rm)
        if not contributions:
            continue
        day = _target_day(bulletin)
        region_links = list(RegionBulletin.objects.filter(bulletin=bulletin))
        for link in region_links:
            pair_key = (link.region_id, day)
            for key, subdivision in contributions:
                rank = _rating_rank(key)
                pairs[pair_key].append(
                    (rank, bulletin.valid_from, key, subdivision, bulletin)
                )
    return pairs


def _upsert_pair(RegionDayRating, region_id, day, all_contributions) -> None:
    """Upsert a single RegionDayRating row from its list of contributions."""
    # Best (max): highest rank, tie-break by latest valid_from.
    best = max(all_contributions, key=lambda c: (c[0], c[1]))
    # Worst (min): lowest rank among valid (rank >= 0), tie-break latest valid_from.
    valid_contributions = [c for c in all_contributions if c[0] >= 0]
    worst = (
        min(valid_contributions, key=lambda c: (c[0], -c[1].timestamp()))
        if valid_contributions
        else best
    )
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


def _zero_orphan_rows(RegionDayRating, computed_keys: set) -> int:
    """
    Set rows with no v3 candidates to no_rating and return the count zeroed.

    Rows that were not touched by the upsert pass (because their only
    contributing bulletin was an evening-of-X issue now retargeted to X+1)
    are set to no_rating with source_bulletin=None rather than deleted, so
    the calendar tile renders grey/unclickable instead of disappearing.
    """
    zeroed = 0
    for row in RegionDayRating.objects.exclude(version=_DAY_RATING_VERSION):
        if (row.region_id, row.date) not in computed_keys:
            row.min_rating = _NO_RATING
            row.min_subdivision = ""
            row.max_rating = _NO_RATING
            row.max_subdivision = ""
            row.source_bulletin = None
            row.version = _DAY_RATING_VERSION
            row.save()
            zeroed += 1
    return zeroed


def retarget_day_ratings(apps, schema_editor):
    """
    Re-derive RegionDayRating rows using the v3 target-day semantic.

    For each qualifying bulletin, computes target_day and accumulates
    trait-level contributions keyed by (region_id, target_day).  Then
    upserts RegionDayRating rows with version=3.

    Rows that previously existed but now have zero candidates (because their
    only contributing bulletin was an evening-of-X issue now retargeted to
    X+1) are set to no_rating with source_bulletin=None.
    """
    Bulletin = apps.get_model("pipeline", "Bulletin")
    RegionDayRating = apps.get_model("pipeline", "RegionDayRating")
    RegionBulletin = apps.get_model("pipeline", "RegionBulletin")

    qualifying = list(
        Bulletin.objects.filter(
            render_model_version__gte=_RENDER_MODEL_VERSION,
        ).order_by("valid_from")
    )

    pairs = _build_pairs(qualifying, RegionBulletin)

    upserted = 0
    computed_keys: set[tuple] = set()
    for (region_id, day), all_contributions in pairs.items():
        computed_keys.add((region_id, day))
        _upsert_pair(RegionDayRating, region_id, day, all_contributions)
        upserted += 1

    zeroed = _zero_orphan_rows(RegionDayRating, computed_keys)

    logger.info(
        "0009 retarget_day_ratings: upserted=%d zeroed=%d RegionDayRating rows (v3)",
        upserted,
        zeroed,
    )


class Migration(migrations.Migration):
    """Re-derive RegionDayRating rows using the v3 target-day semantic."""

    dependencies = [
        ("pipeline", "0008_rederive_regiondayrating_traits"),
    ]

    operations = [
        migrations.RunPython(
            retarget_day_ratings, reverse_code=migrations.RunPython.noop
        ),
    ]
