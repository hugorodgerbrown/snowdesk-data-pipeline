"""
pipeline/migrations/0010_single_bulletin_day_rating.py — Data migration.

Re-derives all RegionDayRating rows under the v4 single-bulletin policy.

The v3 policy (migration 0009) aggregated min/max ratings across *all*
qualifying bulletins for a day (both morning-of-X and prior-evening-of-(X-1)).
The v4 policy selects a single bulletin — the one most recently published by
~10am on that day — and aggregates only within that bulletin's own traits:

  - Morning-of-X (valid_from.date() == X, hour < 12) wins when available,
    because it has a later valid_from than prior-evening-of-(X-1).
  - Prior-evening-of-(X-1) is the fallback when no morning-of-X exists.
  - Evening-of-X is still excluded (its target day is X+1).

For rows where the chosen bulletin's render_model is malformed, or where
no target-day candidate exists, the row is set to no_rating with
source_bulletin=None rather than deleted, keeping the calendar tile stable.

Idempotent via update_or_create.  Reverse is a no-op.  Stamps version=4.

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
_DAY_RATING_VERSION = 4

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


def _aggregate_bulletin(render_model: dict) -> tuple[str, str, str, str] | None:
    """
    Compute (max_key, max_subdivision, min_key, min_subdivision) for one bulletin.

    Returns None when the render_model is so malformed that no usable data
    can be extracted and the caller should write no_rating.

    Empty traits → quiet-day fallback to headline danger key.
    Non-empty traits → derive min/max from valid danger_level ints.
    All trait levels invalid → returns None.
    """
    headline_key, headline_sub = _extract_headline(render_model)
    traits = render_model.get("traits") or []

    if not traits:
        return headline_key, headline_sub, headline_key, headline_sub

    valid_keys: list[str] = []
    for trait in traits:
        raw_level = trait.get("danger_level")
        if isinstance(raw_level, int) and raw_level in _DANGER_LEVEL_TO_KEY:
            valid_keys.append(_DANGER_LEVEL_TO_KEY[raw_level])

    if not valid_keys:
        return None

    max_key = max(valid_keys, key=_rating_rank)
    min_key = min(valid_keys, key=_rating_rank)
    return max_key, headline_sub, min_key, headline_sub


def _build_region_day_candidates(qualifying, RegionBulletin) -> dict:
    """
    Build a mapping of (region_id, day) → list of bulletins targeting that day.

    Returns a defaultdict(list) where each value is a list of Bulletin objects.
    """
    region_day_map: dict = defaultdict(list)
    for bulletin in qualifying:
        day = _target_day(bulletin)
        region_links = list(RegionBulletin.objects.filter(bulletin=bulletin))
        for link in region_links:
            region_day_map[(link.region_id, day)].append(bulletin)
    return region_day_map


def single_bulletin_day_rating(apps, schema_editor):
    """
    Re-derive RegionDayRating rows using the v4 single-bulletin policy.

    For each (region, day) pair, picks the qualifying bulletin with the
    latest valid_from (morning-of-day wins over prior-evening) and
    aggregates min/max within that bulletin's traits only.

    Rows that have no candidate, or whose chosen bulletin's render_model is
    malformed, are set to no_rating / source_bulletin=None.
    """
    Bulletin = apps.get_model("pipeline", "Bulletin")
    RegionDayRating = apps.get_model("pipeline", "RegionDayRating")
    RegionBulletin = apps.get_model("pipeline", "RegionBulletin")

    qualifying = list(
        Bulletin.objects.filter(
            render_model_version__gte=_RENDER_MODEL_VERSION,
        ).order_by("valid_from")
    )

    region_day_map = _build_region_day_candidates(qualifying, RegionBulletin)

    upserted = 0
    zeroed = 0
    computed_keys: set[tuple] = set()

    for (region_id, day), bulletins in region_day_map.items():
        computed_keys.add((region_id, day))

        # Single-bulletin policy: pick the one with the latest valid_from.
        chosen = max(bulletins, key=lambda b: b.valid_from)
        rm = chosen.render_model or {}
        result = _aggregate_bulletin(rm)

        if result is None:
            max_key = _NO_RATING
            max_sub = ""
            min_key = _NO_RATING
            min_sub = ""
            source = None
        else:
            max_key, max_sub, min_key, min_sub = result
            source = chosen

        RegionDayRating.objects.update_or_create(
            region_id=region_id,
            date=day,
            defaults={
                "min_rating": min_key,
                "min_subdivision": min_sub,
                "max_rating": max_key,
                "max_subdivision": max_sub,
                "source_bulletin": source,
                "version": _DAY_RATING_VERSION,
            },
        )
        upserted += 1

    # Zero out any existing rows that no longer have a qualifying candidate
    # (e.g. their only bulletin was an error sentinel, now excluded).
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

    logger.info(
        "0010 single_bulletin_day_rating: upserted=%d zeroed=%d RegionDayRating rows (v4)",
        upserted,
        zeroed,
    )


class Migration(migrations.Migration):
    """Re-derive RegionDayRating rows using the v4 single-bulletin policy."""

    dependencies = [
        ("pipeline", "0009_retarget_regiondayrating_issue_day"),
    ]

    operations = [
        migrations.RunPython(
            single_bulletin_day_rating, reverse_code=migrations.RunPython.noop
        ),
    ]
