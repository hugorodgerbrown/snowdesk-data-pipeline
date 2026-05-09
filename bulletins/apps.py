"""
bulletins/apps.py — AppConfig for the bulletins application.

Owns every model sourced from an external API (SLF avalanche bulletins,
Open-Meteo weather snapshots) plus the fetch and denormalisation
services that produce them. The static reference data (EAWS hierarchy,
resorts) lives in ``regions``; cross-cutting plumbing (HTMX
decorators, security middleware, HTML utilities) lives in ``core``.
"""

from django.apps import AppConfig


class BulletinsConfig(AppConfig):
    """Django application configuration for the bulletins app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "bulletins"
