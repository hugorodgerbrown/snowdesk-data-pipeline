"""
apps/locations/migrations/0003_remove_location_forecast_cell.py.

Drop ``Location.forecast_cell`` (SNOW-762) and refresh ``elevation_m``'s
help text, which named the deleted ``link_location_forecast_cells``
command. The elevation itself stays: it is location domain, resolved via
the rehomed ``apps.locations.services.elevation.fetch_elevation``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0002_qualify_coordinate_help_text"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="location",
            name="forecast_cell",
        ),
        migrations.AlterField(
            model_name="location",
            name="elevation_m",
            field=models.FloatField(
                blank=True,
                help_text="Elevation in metres, resolved once via fetch_elevation. Null until an out-of-band resolution pass has run.",
                null=True,
            ),
        ),
    ]
