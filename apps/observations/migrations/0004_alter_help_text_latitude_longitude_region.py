# Generated for SNOW-330 — update help_text on latitude, longitude, and region
# to reflect that these now record the report location (not necessarily the raw
# GPS fix) and may be derived from a MANUAL pin or a GPS_REFINED drag.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """AlterField help_text for latitude, longitude, and region FK.

    These are purely cosmetic schema-metadata changes — no data movement.
    """

    dependencies = [
        ("observations", "0003_fieldobservation_location_source_gps_coords"),
        ("regions", "0006_majorregion_display_on_map"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fieldobservation",
            name="latitude",
            field=models.FloatField(
                help_text=(
                    "WGS-84 latitude of the report location "
                    "(GPS fix, refined pin, or manually-placed pin)."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="longitude",
            field=models.FloatField(
                help_text=(
                    "WGS-84 longitude of the report location "
                    "(GPS fix, refined pin, or manually-placed pin)."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="region",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Best-effort MicroRegion resolved from the report location. "
                    "Null when the point cannot be matched to a known boundary."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="field_observations",
                to="regions.microregion",
            ),
        ),
    ]
