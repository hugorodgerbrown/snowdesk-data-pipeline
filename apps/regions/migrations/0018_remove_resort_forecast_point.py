"""
apps/regions/migrations/0018_remove_resort_forecast_point.py.

Drop ``Resort.forecast_point`` (SNOW-762), retiring the column
``link_resort_forecast_points`` used to fill. SNOW-757 re-anchors resort
weather on linked Locations instead.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("regions", "0017_microregion_centroid_location"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="resort",
            name="forecast_point",
        ),
    ]
