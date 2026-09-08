# SNOW-805 — drop the Subscription table.
#
# ``Subscription`` was justified as a notification channel and never became
# one — nothing ever sent a bulletin — so SNOW-802 turned each row into the
# region pin it always was, and SNOW-875 removed the email machinery that
# referenced it. This migration removes the table itself.
#
# Its own deploy, on purpose. ``bin/build.sh`` migrates on every deploy, so
# this drop can never travel with the ``backfill_subscriptions_to_region_pins``
# run that empties it; that command was run on production on 2026-09-08.
#
# Schema only — no data operations. Bulk dataset work in a migration locks
# the table and breaks the Render deploy (CLAUDE.md), which is precisely why
# the backfill was a separate command.
#
# ``AlterUniqueTogether`` runs before ``DeleteModel``, following the
# precedent set by 0010_drop_subscriber: SQLite rebuilds the table on a
# constraint change and wants a consistent ``unique_together`` at each step.

from django.db import migrations


class Migration(migrations.Migration):
    """Drop the (account, region) uniqueness constraint, then Subscription."""

    dependencies = [
        ("accounts", "0011_uppercase_geo_match_kind_and_mechanism"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="subscription",
            unique_together=None,
        ),
        migrations.DeleteModel(
            name="Subscription",
        ),
    ]
