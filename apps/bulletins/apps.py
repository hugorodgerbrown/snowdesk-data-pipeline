"""
apps/bulletins/apps.py — AppConfig for the bulletins application.

Owns the CAAML avalanche bulletins fetched from the three providers (SLF,
ALBINA, Météo-France) plus the fetch and denormalisation services that
produce them. The Open-Meteo weather models and services live in
``weather`` (split out by SNOW-654); the static reference data (EAWS
hierarchy, resorts) lives in ``regions``; cross-cutting plumbing (HTMX
decorators, security middleware, HTML utilities) lives in ``core``.
"""

from django.apps import AppConfig


class BulletinsConfig(AppConfig):
    """Django application configuration for the bulletins app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bulletins"
