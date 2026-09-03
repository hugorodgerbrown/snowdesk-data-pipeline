"""
apps/locations/migrations/0004_location_short_id.py.

Add ``Location.short_id`` (SNOW-797) — nullable, unique, and with NO
database-side default.

The model field carries ``default=generate_short_id`` so every new row is
minted one. That default deliberately does not reach this migration's DDL:
Django evaluates a callable default ONCE when adding a column and stamps
every existing row with the same value, which the unique constraint would
then reject on any populated table. ``SeparateDatabaseAndState`` keeps the
default in the migration STATE (so ``makemigrations --check`` is quiet)
while the DATABASE gets a plain nullable column — existing rows land as
NULL, and ``backfill_location_short_ids --commit`` mints each one its own
id. A later migration tightens the column to ``null=False`` once every
environment has run that command. No data is written here (CLAUDE.md: no
bulk data updates in migrations).
"""

from django.db import migrations, models

import apps.locations.models

_HELP_TEXT = (
    "Eleven-character opaque URL identifier — /weather/<short_id>/ and the id "
    "weather.geojson emits (SNOW-797). Opaque rather than a slug because most "
    "public locations are unnamed region centroids. Null only on a row "
    "backfill_location_short_ids has not reached yet."
)


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0003_remove_location_forecast_cell"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AddField(
                    model_name="location",
                    name="short_id",
                    field=models.CharField(
                        blank=True,
                        editable=False,
                        help_text=_HELP_TEXT,
                        max_length=16,
                        null=True,
                        unique=True,
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="location",
                    name="short_id",
                    field=models.CharField(
                        blank=True,
                        default=apps.locations.models.generate_short_id,
                        editable=False,
                        help_text=_HELP_TEXT,
                        max_length=16,
                        null=True,
                        unique=True,
                    ),
                ),
            ],
        ),
    ]
