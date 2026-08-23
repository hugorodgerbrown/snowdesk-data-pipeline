"""
apps/weather/migrations/0004_alter_forecastcellweather_options_and_more.py.

State-only follow-up to ``0003_rename_forecastpoint_to_forecastcell``.

Django re-derives an index from the field names it covers, so renaming
``forecast_point`` to ``forecast_cell`` makes it want to drop and
recreate both indexes — even though their names are pinned and the
underlying column is unchanged, because ``db_column`` is pinned too.
The same applies to the ``AlterField`` operations: they carry the
``db_column`` pin and some help-text edits, neither of which touches a
table.

So this is wrapped in ``SeparateDatabaseAndState`` with an empty
``database_operations`` list, exactly like 0003. ``manage.py sqlmigrate
weather 0004`` emits no DDL, and the DROP/CREATE INDEX pair — a real
lock on a large table — never runs.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("weather", "0003_rename_forecastpoint_to_forecastcell"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelOptions(
                    name="forecastcellweather",
                    options={"ordering": ["-valid_for_date", "forecast_cell__id"]},
                ),
                migrations.RemoveIndex(
                    model_name="forecastcellweather",
                    name="bulletins_f_forecas_e18a91_idx",
                ),
                migrations.RemoveIndex(
                    model_name="forecastcellweatherhistory",
                    name="bulletins_f_forecas_95f2d5_idx",
                ),
                migrations.AlterField(
                    model_name="forecastcell",
                    name="elevation_band",
                    field=models.IntegerField(
                        help_text="floor(elevation / ELEVATION_BAND_SIZE) — see forecast_cells.py."
                    ),
                ),
                migrations.AlterField(
                    model_name="forecastcell",
                    name="lat_cell",
                    field=models.IntegerField(
                        help_text="floor(latitude / LAT_CELL_SIZE) — see forecast_cells.py."
                    ),
                ),
                migrations.AlterField(
                    model_name="forecastcell",
                    name="lon_cell",
                    field=models.IntegerField(
                        help_text="floor(longitude / LON_CELL_SIZE) — see forecast_cells.py."
                    ),
                ),
                migrations.AlterField(
                    model_name="forecastcellweather",
                    name="forecast_cell",
                    field=models.ForeignKey(
                        db_column="forecast_point_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weather_snapshots",
                        to="weather.forecastcell",
                    ),
                ),
                migrations.AlterField(
                    model_name="forecastcellweatherhistory",
                    name="forecast_cell",
                    field=models.ForeignKey(
                        db_column="forecast_point_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="forecast_history",
                        to="weather.forecastcell",
                    ),
                ),
                migrations.AddIndex(
                    model_name="forecastcellweather",
                    index=models.Index(
                        fields=["forecast_cell", "valid_for_date"],
                        name="bulletins_f_forecas_e18a91_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name="forecastcellweatherhistory",
                    index=models.Index(
                        fields=["forecast_cell", "valid_for_date"],
                        name="bulletins_f_forecas_95f2d5_idx",
                    ),
                ),
            ],
        ),
    ]
