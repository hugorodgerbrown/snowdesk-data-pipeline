"""
apps/data_feed/apps.py — AppConfig for the data_feed application.

Production's read-only data feed at ``/api/data/v1/`` and its client
command, ``pull_production_data`` (SNOW-1197). No models. See
``docs/data-feed.md``.
"""

from django.apps import AppConfig


class DataFeedConfig(AppConfig):
    """AppConfig for the data_feed application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.data_feed"
    verbose_name = "Data feed"
