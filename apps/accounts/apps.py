"""
apps/accounts/apps.py — AppConfig for the accounts application.

Registers the app with Django and performs any startup configuration.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Django application configuration for the accounts app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"

    def ready(self) -> None:
        """Register system checks declared in ``apps/accounts/checks.py``."""
        from . import checks  # noqa: F401 — import side-effect: registers checks
