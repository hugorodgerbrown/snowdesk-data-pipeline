"""
apps/bulletins/migrations/0018_move_weather_models_to_weather_app.py.

State-only removal of the four Open-Meteo models, which SNOW-654 moved to
``apps.weather``. The tables are NOT dropped: every moved model pins
``Meta.db_table`` to the ``bulletins_*`` name it already had, and
``weather/0001_initial`` re-declares them in the new app's state against
those same tables. Both halves are wrapped in
``SeparateDatabaseAndState`` with an empty ``database_operations`` list,
so ``sqlmigrate`` on either emits no DDL.

Depends on ``favourites`` and ``regions``: their foreign keys must point
at ``weather.ForecastPoint`` in the migration state before
``bulletins.ForecastPoint`` can be deleted from it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Delete the weather models from the bulletins app state only."""

    dependencies = [
        ("bulletins", "0017_weathersnapshot_snowfall_sum_and_more"),
        ("favourites", "0005_alter_favourite_forecast_point"),
        ("regions", "0016_alter_resort_forecast_point"),
        ("weather", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterUniqueTogether(
                    name="forecastpoint",
                    unique_together=None,
                ),
                migrations.AlterUniqueTogether(
                    name="forecastpointweather",
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name="forecastpointweather",
                    name="bulletins_f_forecas_e18a91_idx",
                ),
                migrations.AlterUniqueTogether(
                    name="forecastpointweatherhistory",
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name="forecastpointweatherhistory",
                    name="bulletins_f_forecas_95f2d5_idx",
                ),
                migrations.RemoveIndex(
                    model_name="forecastpointweatherhistory",
                    name="bulletins_f_valid_f_dd24eb_idx",
                ),
                migrations.AlterUniqueTogether(
                    name="weathersnapshot",
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name="weathersnapshot",
                    name="bulletins_w_region__26008e_idx",
                ),
                migrations.RemoveField(
                    model_name="forecastpointweatherhistory",
                    name="forecast_point",
                ),
                migrations.RemoveField(
                    model_name="weathersnapshot",
                    name="region",
                ),
                migrations.DeleteModel(
                    name="ForecastPointWeather",
                ),
                migrations.DeleteModel(
                    name="ForecastPoint",
                ),
                migrations.DeleteModel(
                    name="ForecastPointWeatherHistory",
                ),
                migrations.DeleteModel(
                    name="WeatherSnapshot",
                ),
            ],
            # Empty by design: the tables live on unchanged, now owned by
            # apps.weather.
            database_operations=[],
        ),
    ]
