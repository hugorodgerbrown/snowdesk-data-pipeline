"""
Add the reservation lifecycle to IdempotencyRecord (SNOW-545).

``status`` distinguishes an in-flight reservation from a completed,
replayable response, and ``response_status`` becomes nullable so a
reservation can exist before the view has produced anything.

The AddField deliberately backfills ``COMPLETED`` rather than the model
default (``RESERVED``): every pre-existing row was written the old way —
*after* the view returned — so it already holds a cached response.
Backfilling ``RESERVED`` would make each of them answer ``409`` for the
rest of its 24h window. ``preserve_default=False`` applies COMPLETED to
existing rows only; new rows still default to RESERVED.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add ``status``; relax ``response_status`` to nullable."""

    dependencies = [
        ("core", "0006_requestlog_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="idempotencyrecord",
            name="status",
            field=models.CharField(
                choices=[("RESERVED", "Reserved"), ("COMPLETED", "Completed")],
                db_index=True,
                default="COMPLETED",
                help_text=(
                    "RESERVED while the view is executing, COMPLETED once a "
                    "cacheable response has been captured. Only COMPLETED rows "
                    "are replayable."
                ),
                max_length=16,
            ),
            preserve_default=False,
        ),
        # ``preserve_default=False`` drops the default from migration
        # state entirely, so restate the field with the model's own
        # RESERVED default — otherwise every subsequent makemigrations
        # run detects a phantom change.
        migrations.AlterField(
            model_name="idempotencyrecord",
            name="status",
            field=models.CharField(
                choices=[("RESERVED", "Reserved"), ("COMPLETED", "Completed")],
                db_index=True,
                default="RESERVED",
                help_text=(
                    "RESERVED while the view is executing, COMPLETED once a "
                    "cacheable response has been captured. Only COMPLETED rows "
                    "are replayable."
                ),
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="idempotencyrecord",
            name="response_status",
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=None,
                help_text=(
                    "HTTP status code of the cached response, or NULL while "
                    "the row is still a RESERVED placeholder."
                ),
                null=True,
            ),
        ),
    ]
