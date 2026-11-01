"""
apps/trips/migrations/0006_trip_crossed_regions.py — Trip.crossed_regions (SNOW-945).

The ordered MicroRegion.region_ids the track crosses. Nullable: null means
not yet resolved, which backfill_crossed_regions fills.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add Trip.crossed_regions."""

    dependencies = [
        ("trips", "0005_track_render"),
    ]

    operations = [
        migrations.AddField(
            model_name="trip",
            name="crossed_regions",
            field=models.JSONField(
                blank=True,
                help_text=(
                    "The MicroRegion.region_ids the track crosses, in the order it "
                    "first enters each (SNOW-945). Null until resolved; [] when the "
                    "track is outside coverage."
                ),
                null=True,
            ),
        ),
    ]
