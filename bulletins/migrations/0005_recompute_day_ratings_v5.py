"""
0005_recompute_day_ratings_v5 — Re-derive RegionDayRating under the v5 policy.

Data migration for SNOW-138: the v5 aggregation policy sets both ``min_rating``
and ``max_rating`` to the bulletin's headline ``render_model["danger"]["key"]``
so the heatmap tile always matches the Day Risk Profile panel.  Previously (v4)
``min_rating`` was the lowest trait-level and ``max_rating`` the highest, which
produced split diagonal tiles for bulletins with elevation-banded traits.

This migration calls ``recompute_region_day`` for every distinct (region, date)
pair already present in the ``RegionDayRating`` table so all existing rows are
updated to the new policy.

Reversible: the reverse is a no-op — rolling back leaves rows at v5 values.
Run ``rebuild_render_models --commit`` to fully re-derive from scratch if needed.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations


def recompute_all_day_ratings(apps: Any, schema_editor: Any) -> None:
    """Re-derive every RegionDayRating row under the v5 headline-only policy."""
    from bulletins.models import RegionDayRating
    from bulletins.services.day_rating import recompute_region_day
    from regions.models import Region

    pairs = set(RegionDayRating.objects.values_list("region_id", "date"))
    region_cache: dict[Any, Any] = {}
    for region_id, day in pairs:
        if region_id not in region_cache:
            region_cache[region_id] = Region.objects.get(pk=region_id)
        recompute_region_day(region_cache[region_id], day, commit=True)


class Migration(migrations.Migration):
    """Re-derive RegionDayRating rows for SNOW-138 v5 headline-only policy."""

    dependencies = [
        ("bulletins", "0004_remove_weather_header_flag"),
    ]

    operations = [
        migrations.RunPython(
            recompute_all_day_ratings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
