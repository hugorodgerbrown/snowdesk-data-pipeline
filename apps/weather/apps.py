"""
apps/weather/apps.py — AppConfig for the weather application.

The Open-Meteo domain: one ``Weather`` model, one scheduled fetch. See the
package docstring in ``apps/weather/__init__.py`` for why it is its own app
and why its migration history starts before its models do.
"""

from django.apps import AppConfig


class WeatherConfig(AppConfig):
    """Django application configuration for the weather app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.weather"
