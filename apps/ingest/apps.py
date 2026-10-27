"""
apps/ingest/apps.py — AppConfig for the ingest application.

Holds the providers' fetch and normalisation code (SNOW-1247). No models yet:
its own tables arrive with SNOW-1250 and SNOW-1253. See
``docs/decisions/ingestion-is-a-rehostable-unit.md``.
"""

from django.apps import AppConfig


class IngestConfig(AppConfig):
    """AppConfig for the ingest application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ingest"
    label = "ingest"
    verbose_name = "Ingest"
