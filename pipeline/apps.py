"""
pipeline/apps.py — AppConfig for the pipeline application.

Registers the app with Django and performs any startup configuration.
"""

from django.apps import AppConfig


class PipelineConfig(AppConfig):
    """Django application configuration for the pipeline app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pipeline"
