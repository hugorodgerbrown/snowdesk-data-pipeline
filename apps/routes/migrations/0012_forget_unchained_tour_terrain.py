"""
0012_forget_unchained_tour_terrain — Forget a multi-part tour's terrain.

Until SNOW-1282 a tour's parts were joined in the order swisstopo stores
them, which for 258 tours doubled the line back on itself, and the terrain
sampled under that line was kept on ``SkiTour.slope_samples``. The line is
now chained from start to finish, so a record sampled under the old join
describes other points (SNOW-1282). Clearing it means the next open samples the
chained line. One-part tours are untouched. Routes already saved from a
tour keep their own copy, as any saved route does.
"""

from typing import Any

from django.db import migrations


def forget_unchained_terrain(apps: Any, schema_editor: Any) -> None:
    """Null ``slope_samples`` on every sampled tour of more than one part."""
    SkiTour = apps.get_model("routes", "SkiTour")
    stale = []
    sampled = SkiTour.objects.filter(slope_samples__isnull=False).only("geometry")
    for tour in sampled.iterator():
        geometry = tour.geometry or {}
        if (
            geometry.get("type") == "MultiLineString"
            and len(geometry.get("coordinates") or []) > 1
        ):
            stale.append(tour.pk)
    SkiTour.objects.filter(pk__in=stale).update(slope_samples=None)


class Migration(migrations.Migration):
    """Clear the terrain kept under a multi-part tour's old join."""

    dependencies = [
        ("routes", "0011_ski_tour_routes"),
    ]

    operations = [
        migrations.RunPython(forget_unchained_terrain, migrations.RunPython.noop),
    ]
