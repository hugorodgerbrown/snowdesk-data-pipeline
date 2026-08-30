"""
apps/routes/migrations/0004_routeshare.py — add the share-link table.

Creates ``RouteShare`` (SNOW-764): the tokenised link an owner hands to
another account, which claims a COPY of the route rather than taking it
over.

SCHEMA ONLY. Nothing existing changes shape and nothing is backfilled —
``Route`` is untouched, and a route that has never been shared simply has
no rows here. The two settings the table depends on
(``ROUTE_SHARE_MAX_AGE_DAYS``, ``ROUTE_SHARE_MAX_PENDING``) are read at
creation time in ``apps.routes.services.shares``, never stored per row, so
changing either affects new links without a data migration.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("routes", "0003_route_started_at_finished_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RouteShare",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        help_text="URL-safe random token used in the /routes/s/<token>/ short URL.",
                        max_length=32,
                        unique=True,
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        help_text="When the link stops being claimable. Set settings.ROUTE_SHARE_MAX_AGE_DAYS ahead at creation."
                    ),
                ),
                (
                    "claim_count",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="How many copies have been claimed through this link. Recorded, never enforced — the link is deliberately reusable.",
                    ),
                ),
                (
                    "last_claimed_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the most recent copy was claimed. Null until the first.",
                        null=True,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user who created this share link.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="route_shares",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "route",
                    models.ForeignKey(
                        blank=True,
                        help_text="The route this link hands out. Nulled when the owner deletes the route, which stops the link working; the share row is kept.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shares",
                        to="routes.route",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
    ]
