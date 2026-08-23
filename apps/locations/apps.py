"""
apps/locations/apps.py — AppConfig for the locations application.

The locations app owns ``Location``, the domain primitive every place in
Snowdesk reaches: a resort's village, its mid-station and its peak, a saved
favourite, a field observation, a region centroid. See
``docs/decisions/location-is-the-primitive.md``.

It is its own app rather than part of ``regions`` because ``regions``,
``weather``, ``favourites`` and ``observations`` all reference ``Location``
— housing it in ``regions`` would make four apps depend on ``regions`` and
worsen the existing ``regions`` <-> ``weather`` mutual FK. ``core`` is wrong
too: it holds abstract bases and HTTP-layer infrastructure, not domain
tables.
"""

from django.apps import AppConfig


class LocationsConfig(AppConfig):
    """AppConfig for the locations application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.locations"
    verbose_name = "Locations"
