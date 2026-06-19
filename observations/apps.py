"""
observations/apps.py — AppConfig for the observations application.

The observations app owns GPS-gated field reports submitted by authenticated
subscribers from the map page. Reports are stored as ``FieldObservation``
rows and surfaced as per-type counts on the current-day bulletin page.
"""

from django.apps import AppConfig


class ObservationsConfig(AppConfig):
    """AppConfig for the observations application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "observations"
    verbose_name = "Observations"
