"""
apps/design_system/apps.py — Django app configuration for the component library.

Registers the staff component library so its templates and template tags
are discovered, and registers the design-token system checks on ready.
"""

from django.apps import AppConfig


class DesignSystemConfig(AppConfig):
    """App config for the staff component library."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.design_system"
    verbose_name = "Design System"

    def ready(self) -> None:
        """Register system checks declared in ``apps/design_system/checks.py``."""
        from . import checks  # noqa: F401 — import side-effect: registers checks
