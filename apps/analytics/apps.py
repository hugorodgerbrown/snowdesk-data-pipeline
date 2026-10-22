"""
apps/analytics/apps.py — AppConfig for the analytics application.

Registers the analytics app with Django so it is discovered by the
framework. The app has no models or migrations; it provides ``track()``
(fivebar goal events, ``apps/analytics/fivebar.py``) and the
``/api/telemetry`` receiver. SNOW-1182 removed the PostHog client this
config used to initialise in ``ready()``.
"""

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    """Django application configuration for the analytics app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
