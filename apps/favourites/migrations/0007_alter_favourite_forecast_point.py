"""
apps/favourites/migrations/0007_alter_favourite_forecast_point.py.

State-only: ``Favourite.forecast_point``'s target renamed.

SNOW-703 renamed ``weather.ForecastPoint`` to ``weather.ForecastCell``, so
this FK's ``to=`` has to follow it in the migration state. The column, its
name and its contents are untouched — the field on ``Favourite`` keeps the
name ``forecast_point``, because it is deprecated and SNOW-714 drops it.

Nothing here touches a table, so it is wrapped in
``SeparateDatabaseAndState`` with an empty ``database_operations`` list,
like the three ``weather`` migrations it follows.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0006_favourite_location_alter_favourite_elevation_and_more"),
        ("weather", "0005_rename_content_types"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="favourite",
                    name="forecast_point",
                    field=models.ForeignKey(
                        help_text="Shared ForecastCell this pin's coordinates resolved to. Superseded by location.forecast_cell (SNOW-704); dropped once nothing reads it.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="favourites",
                        to="weather.forecastcell",
                    ),
                ),
            ],
        ),
    ]
