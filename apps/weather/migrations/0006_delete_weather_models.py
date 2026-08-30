"""
apps/weather/migrations/0006_delete_weather_models.py.

Drop the four Open-Meteo models and their four ``bulletins_*`` tables
(SNOW-762). The weather data is explicitly disposable — SNOW-757 rebuilds
the domain from scratch on a Location-anchored model rather than
migrating this one forward.

Depends on the three FK-removal migrations: ``Location.forecast_cell``,
``Favourite.forecast_point`` and ``Resort.forecast_point`` all pointed
here under ``on_delete=PROTECT``, so the tables cannot be dropped until
those columns are gone.

The app itself survives as migration history only — see
``apps/weather/__init__.py`` for why it cannot be deleted outright.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0008_remove_favourite_forecast_point"),
        ("locations", "0003_remove_location_forecast_cell"),
        ("regions", "0018_remove_resort_forecast_point"),
        ("weather", "0005_rename_content_types"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="forecastcell",
            unique_together=None,
        ),
        migrations.AlterUniqueTogether(
            name="forecastcellweather",
            unique_together=None,
        ),
        migrations.RemoveIndex(
            model_name="forecastcellweather",
            name="bulletins_f_forecas_e18a91_idx",
        ),
        migrations.AlterUniqueTogether(
            name="forecastcellweatherhistory",
            unique_together=None,
        ),
        migrations.RemoveIndex(
            model_name="forecastcellweatherhistory",
            name="bulletins_f_valid_f_dd24eb_idx",
        ),
        migrations.RemoveIndex(
            model_name="forecastcellweatherhistory",
            name="bulletins_f_forecas_95f2d5_idx",
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
            model_name="forecastcellweatherhistory",
            name="forecast_cell",
        ),
        migrations.RemoveField(
            model_name="weathersnapshot",
            name="region",
        ),
        migrations.DeleteModel(
            name="ForecastCellWeather",
        ),
        migrations.DeleteModel(
            name="ForecastCell",
        ),
        migrations.DeleteModel(
            name="ForecastCellWeatherHistory",
        ),
        migrations.DeleteModel(
            name="WeatherSnapshot",
        ),
    ]
