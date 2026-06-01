"""
analytics/apps.py — AppConfig for the analytics application.

Registers the analytics app with Django so it is discovered by the
framework. The app has no models, migrations, or URLs — it exists only
to provide the ``track()`` and ``alias()`` wrappers that fire server-side
events to PostHog.
"""

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    """Django application configuration for the analytics app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"
