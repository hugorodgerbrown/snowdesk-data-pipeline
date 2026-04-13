"""
subscriptions/apps.py — AppConfig for the subscriptions application.

Registers the app with Django and performs any startup configuration.
"""

from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    """Django application configuration for the subscriptions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"
