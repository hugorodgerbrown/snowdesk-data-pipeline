"""
apps/weather/apps.py — AppConfig for the (model-less) weather application.

The app is registered only so its migration history stays resolvable for
the four other apps that depend on it. It declares no models. See the
package docstring in ``apps/weather/__init__.py`` for why it survives
SNOW-762 and what would let it go.
"""

from django.apps import AppConfig


class WeatherConfig(AppConfig):
    """Django application configuration for the weather app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.weather"
