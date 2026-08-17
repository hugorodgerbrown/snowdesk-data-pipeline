"""
apps/routes/apps.py — AppConfig for the routes application.

The routes app owns the ``Route`` model: a polyline an authenticated user
imported from a GPX file, stored as a coordinate list with derived
distance, ascent and bounds. The uploaded file itself is parsed and
discarded — see
``docs/decisions/gpx-uploads-are-parsed-not-stored.md``.
"""

from django.apps import AppConfig


class RoutesConfig(AppConfig):
    """AppConfig for the routes application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.routes"
    verbose_name = "Routes"
