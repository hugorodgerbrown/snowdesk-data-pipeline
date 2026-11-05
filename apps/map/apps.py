"""
apps/map/apps.py — Django app configuration for the interactive map.

Registers the map app so its templates are discovered. It has no models.
"""

from django.apps import AppConfig


class MapConfig(AppConfig):
    """App config for the interactive map."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.map"
    verbose_name = "Map"
