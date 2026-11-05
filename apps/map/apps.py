"""
apps/map/apps.py — Django app configuration for the interactive map.

Registers the map app: its templates, and the Map3dShare model.
"""

from django.apps import AppConfig


class MapConfig(AppConfig):
    """App config for the interactive map."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.map"
    verbose_name = "Map"
