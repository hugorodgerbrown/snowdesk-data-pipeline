# SNOW-514 — collapse Subscriber into Account, step 2 of 3.
#
# RequestLog.subscriber FKs accounts.Subscriber, so it must move onto
# accounts.Account before accounts/0010 can drop the Subscriber table.
# Adds the nullable ``account`` FK, backfills it from ``subscriber_id`` (the
# matching Account rows already exist — accounts/0009 created one for every
# Subscriber), then drops the old ``subscriber`` field.

import django.db.models.deletion
from django.db import migrations, models


def _backfill_requestlog_account(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Repoint every RequestLog.account from its (soon-to-be-dropped) subscriber_id."""
    RequestLog = apps.get_model("core", "RequestLog")
    Subscriber = apps.get_model("accounts", "Subscriber")
    Account = apps.get_model("accounts", "Account")

    user_id_by_subscriber_id = dict(Subscriber.objects.values_list("id", "user_id"))
    account_id_by_user_id = dict(Account.objects.values_list("user_id", "id"))

    for row in (
        RequestLog.objects.filter(subscriber_id__isnull=False)
        .only("id", "subscriber_id")
        .iterator()
    ):
        user_id = user_id_by_subscriber_id.get(row.subscriber_id)
        account_id = account_id_by_user_id.get(user_id) if user_id is not None else None
        if account_id is not None:
            RequestLog.objects.filter(pk=row.pk).update(account_id=account_id)


def _reverse_backfill(apps, schema_editor):  # type: ignore[no-untyped-def]
    """No-op reverse — the dropped subscriber_id values cannot be restored."""


class Migration(migrations.Migration):
    """Add RequestLog.account, backfill it, and drop RequestLog.subscriber."""

    dependencies = [
        ("core", "0005_idempotencyrecord_body_hash_and_more"),
        ("accounts", "0009_collapse_subscriber_add_and_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestlog",
            name="account",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Authenticated account at request time, if any. "
                    "Null for anonymous requests (e.g. sign-up before the row "
                    "exists)."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="request_logs",
                to="accounts.account",
            ),
        ),
        migrations.RunPython(
            _backfill_requestlog_account, reverse_code=_reverse_backfill
        ),
        migrations.RemoveField(
            model_name="requestlog",
            name="subscriber",
        ),
    ]
