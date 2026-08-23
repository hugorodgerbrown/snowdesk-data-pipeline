"""
apps/weather/apps.py — AppConfig for the weather application.

Owns the Open-Meteo domain end to end: the ``WeatherSnapshot`` /
``ForecastCell`` / ``ForecastCellWeather`` /
``ForecastCellWeatherHistory`` models, the fetch, quantisation, elevation
and display services that produce and render them, and the
``fetch_weather`` / ``prune_forecast_points`` commands. The CAAML
avalanche bulletins (SLF, ALBINA, Météo-France) live in ``bulletins``;
the static reference data (EAWS hierarchy, resorts) lives in ``regions``.

The models were split out of ``bulletins`` by SNOW-654 without touching
the database — each one pins its original ``bulletins_*`` table name via
``Meta.db_table`` — see
``docs/decisions/weather-is-its-own-app.md``.
"""

from django.apps import AppConfig


class WeatherConfig(AppConfig):
    """Django application configuration for the weather app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.weather"
