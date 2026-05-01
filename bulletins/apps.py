"""
bulletins/apps.py — AppConfig for the bulletins application.

Owns SLF bulletin ingestion and storage. The pipeline app retains the
region hierarchy and HTTP-layer concerns; this app holds bulletin-derived
models and the fetch + denormalisation services that produce them.

Currently a shell — models, services, admin, and management commands
land in subsequent SNOW-88 child tickets.
"""

from django.apps import AppConfig


class BulletinsConfig(AppConfig):
    """Django application configuration for the bulletins app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "bulletins"
