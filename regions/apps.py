"""
regions/apps.py — AppConfig for the regions application.

The ``regions`` app owns the static, fixture-backed geographic
reference data (EAWS hierarchy and resorts) shared across the rest
of the project. Split out from the legacy ``pipeline`` app in
SNOW-140 so that the app boundary aligns with the underlying axis
of the data: this app is reference data; ``bulletins`` is the data
sourced from external APIs (SLF, Open-Meteo, …).
"""

from django.apps import AppConfig


class RegionsConfig(AppConfig):
    """Django application configuration for the regions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "regions"
    verbose_name = "Regions"
