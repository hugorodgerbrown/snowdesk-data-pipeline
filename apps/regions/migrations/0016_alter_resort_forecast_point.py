"""
apps/regions/migrations/0016_alter_resort_forecast_point.py.

State-only repoint of ``Resort.forecast_point`` from
``bulletins.ForecastPoint`` to ``weather.ForecastPoint`` (SNOW-654). The
target model moved app, not table — its ``Meta.db_table`` still reads
``bulletins_forecastpoint`` — so the column and its foreign-key
constraint are unchanged and ``sqlmigrate`` emits no DDL.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Repoint Resort.forecast_point at weather.ForecastPoint in state only."""

    dependencies = [
        ("regions", "0015_uppercase_resort_geocode_source"),
        ("weather", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="resort",
                    name="forecast_point",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Shared weather-sampling point; resolved once from lat/lon.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="resorts",
                        to="weather.forecastpoint",
                    ),
                ),
            ],
            # Empty by design: the column and its FK constraint are
            # untouched — only the model's app label changed.
            database_operations=[],
        ),
    ]
