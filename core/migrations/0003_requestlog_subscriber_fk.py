# SNOW-313 — Add RequestLog.subscriber FK to subscriptions.Subscriber.
#
# core.0001_requestlog created the RequestLog table without this FK to avoid
# a circular dependency (subscriptions.0001_initial depends on core because
# Subscriber.acquisition_request points to RequestLog).  This migration adds
# the FK after both apps are initialised.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add RequestLog.subscriber FK after subscriptions app is initialised."""

    dependencies = [
        ("core", "0002_requestlog_sec_purpose"),
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestlog",
            name="subscriber",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="request_logs",
                to="subscriptions.subscriber",
                help_text=(
                    "Authenticated subscriber at request time, if any. "
                    "Null for anonymous requests (e.g. sign-up before the "
                    "row exists)."
                ),
            ),
        ),
    ]
