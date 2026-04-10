"""
public/apps.py — Django app configuration for the public bulletin site.

Registers the public-facing avalanche bulletin viewer as a Django
application so that templates, template tags, and URL routing are
discovered automatically.
"""

from django.apps import AppConfig


class PublicConfig(AppConfig):
    """App config for the public bulletin viewer."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "public"
    verbose_name = "Public Bulletin Site"
