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

    def ready(self) -> None:
        """Register URL path converters that belong to the regions domain.

        ``RegionIdConverter`` is defined in ``regions/`` and used by URL patterns
        in ``public/``, ``public/api_urls.py``, and ``accounts/``.  Registering
        it here — in ``AppConfig.ready()`` — guarantees the converter is available
        the moment any URLconf is resolved, regardless of the include order in
        ``config/urls.py`` or whether a test imports a URL module in isolation.
        Keeping the call inside ``ready()`` (rather than at module import time)
        follows Django convention: app startup code runs after the ORM is ready
        and avoids circular-import hazards during the app-loading phase.
        """
        from django.urls import register_converter

        from regions.converters import RegionIdConverter

        register_converter(RegionIdConverter, "region_id")
