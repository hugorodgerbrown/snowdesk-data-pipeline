# Generated for SNOW-277 — core RequestLog model.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the RequestLog concrete model in the core app."""

    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="RequestLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "session_key",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Django session key at request time.",
                        max_length=64,
                    ),
                ),
                (
                    "method",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="HTTP method (e.g. GET, POST).",
                        max_length=16,
                    ),
                ),
                (
                    "path",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Request path (without query string).",
                        max_length=2048,
                    ),
                ),
                (
                    "referer",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="HTTP Referer header.",
                    ),
                ),
                (
                    "user_agent",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="HTTP User-Agent header.",
                    ),
                ),
                (
                    "ip_address",
                    models.GenericIPAddressField(
                        blank=True,
                        null=True,
                        help_text=(
                            "Client IP (leftmost XFF or REMOTE_ADDR). "
                            "Null when indeterminate."
                        ),
                    ),
                ),
                (
                    "country_code",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        help_text=(
                            "ISO 3166-1 alpha-2 country code (e.g. 'CH'). "
                            "Empty on lookup failure."
                        ),
                        max_length=2,
                    ),
                ),
                (
                    "subdivision_code",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "ISO 3166-2 subdivision code without the country prefix "
                            "(e.g. 'VS'). Empty when not in the database."
                        ),
                        max_length=10,
                    ),
                ),
                (
                    "city",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="City name in English. Empty when not in the database.",
                        max_length=128,
                    ),
                ),
                (
                    "latitude",
                    models.FloatField(
                        blank=True,
                        null=True,
                        help_text="WGS-84 latitude. Null when not in the database.",
                    ),
                ),
                (
                    "longitude",
                    models.FloatField(
                        blank=True,
                        null=True,
                        help_text="WGS-84 longitude. Null when not in the database.",
                    ),
                ),
                (
                    "accuracy_radius_km",
                    models.IntegerField(
                        blank=True,
                        null=True,
                        help_text=(
                            "MaxMind accuracy radius in kilometres. "
                            "Null when not in the database."
                        ),
                    ),
                ),
                (
                    "accept_language",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Raw HTTP Accept-Language header value.",
                        max_length=200,
                    ),
                ),
                (
                    "language",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Primary language subtag parsed from Accept-Language "
                            "(e.g. 'en', 'de', 'fr'). Empty when header is absent or "
                            "unparseable."
                        ),
                        max_length=8,
                    ),
                ),
                # subscriber FK added in core.0003_requestlog_subscriber_fk
                # after the subscriptions app is initialised (circular-dep break).
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
    ]
