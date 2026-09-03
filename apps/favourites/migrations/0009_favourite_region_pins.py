"""
apps/favourites/migrations/0009_favourite_region_pins.py.

The region-pin shape (SNOW-802): ``latitude`` and ``longitude`` become
nullable — a region pin has no coordinate — and a partial unique constraint
keeps one region pin per ``(user, region)`` without colliding with placed
pins that merely resolved to a region. No data is written here: the
``Subscription`` rows become region pins through
``backfill_subscriptions_to_region_pins --commit``, and the table itself is
dropped in a later, separate deploy (SNOW-805).
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0008_remove_favourite_forecast_point"),
        ("locations", "0004_location_short_id"),
        ("regions", "0021_resort_slug"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="favourite",
            name="elevation",
            field=models.FloatField(
                blank=True,
                help_text="Elevation in metres. Superseded by location.elevation_m (SNOW-704); dropped once nothing reads it. Null on a region pin (SNOW-802), which has no elevation.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="favourite",
            name="latitude",
            field=models.FloatField(
                blank=True,
                help_text="WGS-84 latitude of the saved pin. Superseded by location.latitude (SNOW-704); dropped once nothing reads it. Null on a region pin (SNOW-802), which has no coordinate.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="favourite",
            name="longitude",
            field=models.FloatField(
                blank=True,
                help_text="WGS-84 longitude of the saved pin. Superseded by location.longitude (SNOW-704); dropped once nothing reads it. Null on a region pin (SNOW-802), which has no coordinate.",
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="favourite",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("latitude__isnull", True),
                    ("location__isnull", True),
                    ("region__isnull", False),
                ),
                fields=("user", "region"),
                name="favourite_unique_user_region_pin",
            ),
        ),
    ]
