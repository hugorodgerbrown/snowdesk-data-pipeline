# SNOW-514 — collapse Subscriber into Account, step 3 of 3.
#
# By this point every Subscription and PushSubscription row already carries
# a backfilled ``account`` FK (accounts/0009) and RequestLog no longer
# references Subscriber at all (core/0006), so the old ``subscriber`` fields
# and the Subscriber table itself can be dropped safely.
#
# Also depends on observations/0005, which replaced FieldObservation's own
# (unrelated) ``subscriber`` FK with a direct ``user`` FK — without this edge
# Django's global migration planner is free to interleave DeleteModel(
# Subscriber) before that observations migration runs, and the CREATE TABLE
# for FieldObservation's *original* migration state (0001, which still has
# the old accounts.Subscriber FK) fails to resolve the deleted model on a
# fresh test database.

from django.db import migrations


class Migration(migrations.Migration):
    """Drop the old subscriber FKs, repoint uniqueness, and delete Subscriber."""

    dependencies = [
        ("accounts", "0009_collapse_subscriber_add_and_backfill"),
        ("core", "0006_requestlog_account"),
        ("observations", "0005_fieldobservation_user"),
    ]

    operations = [
        # Drop the old (subscriber, region) uniqueness constraint before
        # removing the subscriber field — SQLite rebuilds the table on
        # RemoveField and needs a consistent unique_together at each step.
        migrations.AlterUniqueTogether(
            name="subscription",
            unique_together={("account", "region")},
        ),
        migrations.RemoveField(
            model_name="subscription",
            name="subscriber",
        ),
        migrations.RemoveField(
            model_name="pushsubscription",
            name="subscriber",
        ),
        migrations.DeleteModel(
            name="Subscriber",
        ),
    ]
