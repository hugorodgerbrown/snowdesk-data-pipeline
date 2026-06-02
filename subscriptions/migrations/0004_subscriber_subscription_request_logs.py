# Generated for SNOW-277 — add RequestLog FKs to Subscriber and Subscription.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add acquisition_request to Subscriber and subscribed_via to Subscription."""

    dependencies = [
        ("subscriptions", "0003_pushsubscription"),
        ("core", "0001_requestlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriber",
            name="acquisition_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="acquired_subscribers",
                to="core.requestlog",
                help_text=(
                    "Request that first created this subscriber. "
                    "First-observation wins; never overwritten."
                ),
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="subscribed_via",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="initiated_subscriptions",
                to="core.requestlog",
                help_text=(
                    "Request that created this subscription. "
                    "First-observation wins on the (subscriber, region) pair."
                ),
            ),
        ),
    ]
