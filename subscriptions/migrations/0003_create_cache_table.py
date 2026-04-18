# subscriptions/migrations/0003_create_cache_table.py
#
# Creates the django_cache table used by Django's DatabaseCache backend.
# DatabaseCache is the baseline shared cache for django-ratelimit across
# workers.  Upgrade to Redis when traffic warrants.
#
# createcachetable is idempotent — it skips creation if the table already
# exists — so this migration is safe to re-run.

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    """Create the django_cache table via Django's built-in createcachetable."""
    call_command("createcachetable", "django_cache")


def noop(apps, schema_editor):
    """Reverse migration is a no-op — we leave the cache table in place."""
    pass


class Migration(migrations.Migration):
    """Ensure the shared DatabaseCache table exists for django-ratelimit."""

    dependencies = [
        ("subscriptions", "0002_subscriber_status_confirmed_at"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, noop),
    ]
