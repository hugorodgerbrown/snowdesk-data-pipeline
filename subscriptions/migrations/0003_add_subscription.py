# subscriptions/migrations/0003_add_subscription.py
#
# Creates the Subscription model — kept separate from 0001_initial to avoid a
# circular dependency:  Subscription.region FKs to regions.MicroRegion, and
# regions migrations depend on pipeline, which depends on waffle, which
# dynamically depends on subscriptions.0001_initial (via AUTH_USER_MODEL M2M).
# Placing the regions FK here (after 0001_initial is resolved) breaks the cycle.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0002_create_cache_table"),
        ("regions", "0003_microregionneighbour_alter_microregion_neighbours"),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "region",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to="regions.microregion",
                    ),
                ),
                (
                    "subscriber",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["region__region_id"],
                "unique_together": {("subscriber", "region")},
            },
        ),
    ]
