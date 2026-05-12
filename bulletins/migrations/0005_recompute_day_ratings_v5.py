"""
0005_recompute_day_ratings_v5 — Re-derive RegionDayRating under the v5 policy.

Data migration for SNOW-138: the v5 aggregation policy sets both ``min_rating``
and ``max_rating`` to the bulletin's headline ``render_model["danger"]["key"]``
so the heatmap tile always matches the Day Risk Profile panel.  Previously (v4)
``min_rating`` was the lowest trait-level and ``max_rating`` the highest, which
produced split diagonal tiles for bulletins with elevation-banded traits.

The original RunPython body has been extracted to the ``recompute_day_ratings``
management command so it can be run as a controlled post-deployment step rather
than locking the table during the migration run. This migration is now a no-op
to preserve the migration history.

Run ``recompute_day_ratings --commit`` to apply the v5 policy to all existing rows.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """No-op stub — logic moved to the recompute_day_ratings management command."""

    dependencies = [
        ("bulletins", "0004_remove_weather_header_flag"),
    ]

    operations = []
