"""
subscriptions/apps.py — AppConfig for the subscriptions application.

Registers the app with Django and performs any startup configuration.
"""

from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    """Django application configuration for the subscriptions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"

    def ready(self) -> None:
        """Register system checks declared in ``subscriptions/checks.py``."""
        from . import checks  # noqa: F401 — import side-effect: registers checks
