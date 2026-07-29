# SNOW-514 — collapse Subscriber into Account, step 1 of 3.
#
# Adds the fields the new (account, region) shape needs — Account gets
# acquisition_request (moved off Subscriber); Subscription and
# PushSubscription each get a nullable ``account`` FK alongside their
# existing ``subscriber`` FK. A RunPython step then backfills an Account
# for every Subscriber (ACTIVE -> is_verified=True, verified_at=confirmed_at;
# PENDING -> is_verified=False) and repoints every Subscription /
# PushSubscription row's new ``account`` FK from its ``subscriber_id``.
#
# ``Subscription.account`` is then promoted to non-null now that every row
# has been backfilled. The old ``subscriber`` fields are dropped later, in
# accounts/0010, once core/0006 has also migrated RequestLog off Subscriber
# (RequestLog.subscriber FKs Subscriber, so it must be dropped before the
# Subscriber table itself).

import django.db.models.deletion
from django.db import migrations, models


def _backfill_accounts(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Create/update an Account for every Subscriber, then repoint FKs.

    ACTIVE subscribers become verified accounts (``verified_at`` carried
    over from ``confirmed_at``); PENDING subscribers become unverified
    accounts. When a matching Account already exists (e.g. the same user
    also registered directly), it is only ever promoted towards verified —
    never demoted — and its ``acquisition_request`` is filled in if empty
    (first-observation wins).
    """
    Subscriber = apps.get_model("accounts", "Subscriber")
    Account = apps.get_model("accounts", "Account")
    Subscription = apps.get_model("accounts", "Subscription")
    PushSubscription = apps.get_model("accounts", "PushSubscription")

    for subscriber in Subscriber.objects.all().iterator():
        is_active = subscriber.status == "active"
        defaults = {
            "is_verified": is_active,
            "verified_at": subscriber.confirmed_at if is_active else None,
            "acquisition_request_id": subscriber.acquisition_request_id,
        }
        account, created = Account.objects.get_or_create(
            user_id=subscriber.user_id, defaults=defaults
        )
        if not created:
            update_fields = []
            if is_active and not account.is_verified:
                account.is_verified = True
                account.verified_at = account.verified_at or subscriber.confirmed_at
                update_fields += ["is_verified", "verified_at"]
            if (
                account.acquisition_request_id is None
                and subscriber.acquisition_request_id is not None
            ):
                account.acquisition_request_id = subscriber.acquisition_request_id
                update_fields.append("acquisition_request")
            if update_fields:
                account.save(update_fields=update_fields)

        Subscription.objects.filter(subscriber_id=subscriber.pk).update(
            account_id=account.pk
        )
        PushSubscription.objects.filter(subscriber_id=subscriber.pk).update(
            account_id=account.pk
        )

    # Django creates Postgres FK constraints as DEFERRABLE INITIALLY DEFERRED,
    # so every INSERT/UPDATE above only *queues* a deferred FK-check trigger
    # event — it does not fire until COMMIT. The AlterField DDL that follows
    # this RunPython (promoting Subscription.account to non-null) runs in the
    # same atomic transaction, and Postgres refuses to ALTER a table that has
    # pending trigger events. Flush them now so the checks fire immediately
    # (surfacing any FK inconsistency here) and the pending queue is drained
    # before the DDL. No-op on SQLite (dev/CI/tests), which has no deferred
    # triggers and rejects the statement.
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _reverse_backfill(apps, schema_editor):  # type: ignore[no-untyped-def]
    """No-op reverse — merged/backfilled Account rows cannot be unmerged."""


class Migration(migrations.Migration):
    """Add Account.acquisition_request + nullable account FKs; backfill from Subscriber."""

    dependencies = [
        ("accounts", "0008_seed_sync_log_flag"),
        ("core", "0005_idempotencyrecord_body_hash_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="acquisition_request",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Request that first created this account. "
                    "First-observation wins; never overwritten."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="acquired_accounts",
                to="core.requestlog",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="account",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subscriptions",
                to="accounts.account",
            ),
        ),
        migrations.AddField(
            model_name="pushsubscription",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="push_subscriptions",
                to="accounts.account",
            ),
        ),
        migrations.RunPython(_backfill_accounts, reverse_code=_reverse_backfill),
        migrations.AlterField(
            model_name="subscription",
            name="account",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subscriptions",
                to="accounts.account",
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="geo_match_kind",
            field=models.CharField(
                choices=[
                    ("in_region", "In region"),
                    ("in_neighbour", "In neighbouring region"),
                    ("elsewhere", "Elsewhere"),
                    ("unknown", "Unknown"),
                ],
                db_index=True,
                default="unknown",
                help_text=(
                    "How the account's geolocation (from subscribed_via) relates "
                    "to the subscribed region at sign-up time. Frozen on INSERT; "
                    "never updated. Raw geo fields live on subscribed_via "
                    "(RequestLog)."
                ),
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="geo_matched_region",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The specific MicroRegion the account's geolocation fell "
                    "inside (target region itself, or the first matching "
                    "neighbour). Null when geo_match_kind is elsewhere or "
                    "unknown. Analytics-only; not surfaced on the public region "
                    "API."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="regions.microregion",
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="subscribed_via",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Request that created this subscription. First-observation "
                    "wins on the (account, region) pair."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="initiated_subscriptions",
                to="core.requestlog",
            ),
        ),
    ]
