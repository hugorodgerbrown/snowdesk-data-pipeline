"""
core/apps.py — AppConfig for the core application.

Holds shared abstractions used across other apps (notably ``BaseModel``).
Ships no concrete tables — registered as an installed app so Django
discovers it cleanly and so future shared queryset / manager utilities
have an obvious home.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Django application configuration for the core app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
