"""
0002_create_cache_table — Create the django_cache table.

DatabaseCache is the baseline shared cache for django-ratelimit across
workers (see ``config/settings/production.py``). ``createcachetable`` is
idempotent — it skips creation if the table already exists — so this
migration is safe to re-run.
"""

from typing import Any

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps: Any, schema_editor: Any) -> None:
    """Create the django_cache table via Django's built-in createcachetable."""
    call_command("createcachetable", "django_cache")


def noop(apps: Any, schema_editor: Any) -> None:
    """Reverse migration is a no-op — we leave the cache table in place."""


class Migration(migrations.Migration):
    """Ensure the shared DatabaseCache table exists for django-ratelimit."""

    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, reverse_code=noop),
    ]
