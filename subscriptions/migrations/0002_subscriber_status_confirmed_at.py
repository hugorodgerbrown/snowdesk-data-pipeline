# subscriptions/migrations/0002_subscriber_status_confirmed_at.py
#
# Hand-written migration: replace is_active + last_authenticated_at with
# status (TextChoices: pending / active) + confirmed_at.
#
# Operation order matters:
#   1. Add the new columns with safe defaults (no existing rows are invalid).
#   2. RunPython: backfill status / confirmed_at from is_active /
#      last_authenticated_at.
#   3. Remove the old columns.
#
# The reverse migration restores is_active and last_authenticated_at so that
# rolling back is safe.

from django.db import migrations, models


def forwards(apps, schema_editor):
    """Backfill status and confirmed_at from is_active / last_authenticated_at."""
    Subscriber = apps.get_model("subscriptions", "Subscriber")
    Subscriber.objects.filter(is_active=True).update(
        status="active",
        confirmed_at=models.F("last_authenticated_at"),
    )
    # Rows with is_active=False remain status="pending" (the new default).


def backwards(apps, schema_editor):
    """Restore is_active and last_authenticated_at from status / confirmed_at."""
    Subscriber = apps.get_model("subscriptions", "Subscriber")
    Subscriber.objects.filter(status="active").update(
        is_active=True,
        last_authenticated_at=models.F("confirmed_at"),
    )
    # Rows with status != "active" remain is_active=False (the restored default).


class Migration(migrations.Migration):
    """Replace is_active/last_authenticated_at with status/confirmed_at."""

    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        # 1. Add new columns with safe defaults.
        migrations.AddField(
            model_name="subscriber",
            name="status",
            field=models.CharField(
                max_length=16,
                choices=[("pending", "Pending"), ("active", "Active")],
                default="pending",
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name="subscriber",
            name="confirmed_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Timestamp of first account-link verification.",
            ),
        ),
        # 2. Backfill from the old columns.
        migrations.RunPython(forwards, backwards),
        # 3. Drop the old columns.
        migrations.RemoveField(
            model_name="subscriber",
            name="is_active",
        ),
        migrations.RemoveField(
            model_name="subscriber",
            name="last_authenticated_at",
        ),
    ]
