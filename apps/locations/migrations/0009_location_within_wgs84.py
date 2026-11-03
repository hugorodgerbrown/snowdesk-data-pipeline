"""
apps/locations/migrations/0009_location_within_wgs84.py.

WGS-84 range guards on ``Location`` (SNOW-714). ``Favourite`` and
``FieldObservation`` carried them on their own coordinate copies (SNOW-464);
those copies leave the models in this ticket, so the guard moves to the row
that holds the coordinate. Production had no out-of-range row when this was
written (0 of 646, 2026-11-03).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0008_location_short_id_not_null"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="location",
            constraint=models.CheckConstraint(
                condition=models.Q(("latitude__gte", -90), ("latitude__lte", 90)),
                name="location_latitude_within_wgs84",
            ),
        ),
        migrations.AddConstraint(
            model_name="location",
            constraint=models.CheckConstraint(
                condition=models.Q(("longitude__gte", -180), ("longitude__lte", 180)),
                name="location_longitude_within_wgs84",
            ),
        ),
    ]
