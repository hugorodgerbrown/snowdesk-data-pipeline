"""
apps/weather/migrations/0003_rename_forecastpoint_to_forecastcell.py.

State-only rename of the three forecast models, which SNOW-703 renamed to
say what they are: ``ForecastPoint`` was never a domain entity, only the
quantised cell at which Open-Meteo is called
(``docs/decisions/location-is-the-primitive.md``).

**No table is touched.** Every renamed model keeps the ``bulletins_*``
``db_table`` it has had since SNOW-654, and the two renamed foreign keys
pin ``db_column="forecast_point_id"``. Both halves are wrapped in
``SeparateDatabaseAndState`` with an empty ``database_operations`` list, so
``manage.py sqlmigrate weather 0003`` emits no DDL — which is the check that
this is a code change and not a data one.

Follows the SNOW-654 playbook exactly; ``0002_move_content_types`` is the
sibling that did the same for the app-label move.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Rename the three forecast models in state only."""

    dependencies = [
        ("weather", "0002_move_content_types"),
        # Every migration that names `weather.forecastpoint` must run
        # BEFORE the rename, or its `to=` target dangles and the schema
        # editor fails with "'str' object has no attribute '_meta'".
        # So these pin the LAST migration in each app to reference the old
        # name, not the one that first established the FK — SNOW-704's
        # favourites/0006 re-declares that FK, and pinning 0005 let the
        # rename be ordered ahead of it.
        ("favourites", "0006_favourite_location_alter_favourite_elevation_and_more"),
        ("regions", "0016_alter_resort_forecast_point"),
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(
                    old_name="ForecastPoint",
                    new_name="ForecastCell",
                ),
                migrations.RenameModel(
                    old_name="ForecastPointWeather",
                    new_name="ForecastCellWeather",
                ),
                migrations.RenameModel(
                    old_name="ForecastPointWeatherHistory",
                    new_name="ForecastCellWeatherHistory",
                ),
                migrations.RenameField(
                    model_name="forecastcellweather",
                    old_name="forecast_point",
                    new_name="forecast_cell",
                ),
                migrations.RenameField(
                    model_name="forecastcellweatherhistory",
                    old_name="forecast_point",
                    new_name="forecast_cell",
                ),
            ],
        ),
    ]
