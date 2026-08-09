"""
apps/favourites/migrations/0005_alter_favourite_forecast_point.py.

State-only repoint of ``Favourite.forecast_point`` from
``bulletins.ForecastPoint`` to ``weather.ForecastPoint`` (SNOW-654). The
target model moved app, not table — its ``Meta.db_table`` still reads
``bulletins_forecastpoint`` — so the column and its foreign-key
constraint are unchanged and ``sqlmigrate`` emits no DDL.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Repoint Favourite.forecast_point at weather.ForecastPoint in state only."""

    dependencies = [
        ("favourites", "0004_favourite_resort_and_more"),
        ("weather", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="favourite",
                    name="forecast_point",
                    field=models.ForeignKey(
                        help_text="Shared ForecastPoint this pin's coordinates resolved to.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="favourites",
                        to="weather.forecastpoint",
                    ),
                ),
            ],
            # Empty by design: the column and its FK constraint are
            # untouched — only the model's app label changed.
            database_operations=[],
        ),
    ]
