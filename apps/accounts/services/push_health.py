"""
apps/accounts/services/push_health.py — Delivery receipts and device retirement.

A push service accepting a push (201) says nothing about whether the device
ever showed it: a phone that was wiped, sold or had its browser data cleared
can leave a subscription the push service still accepts for, and the account
then gets alerts nobody sees (SNOW-1494). This module tracks, per
``PushSubscription``, whether the device answers.

**Receipts.** Every push to an ``SW`` device carries ``receipt``, a
``TimestampSigner`` token (salt ``push-receipt``) over ``{"s": uuid, "ttl":
ttl}``. The service worker posts it back to ``accounts:push_receipt`` when
it shows the notification, and :func:`confirm` sets ``confirmed_at`` and
clears the unconfirmed count. A token is good for the push's own TTL plus
``RECEIPT_GRACE_SECONDS``: a push the push service held for its whole
lifetime can still be confirmed when it finally lands.

**Counting.** An alert push (``dispatch_push(count_unconfirmed=True)``) is
counted by :func:`count_send` before it goes, uncounted by
:func:`uncount_send` if the push service did not take it, and followed by
:func:`retire_if_dead` if it did. A push is *trusted* when the device had
confirmed everything sent before it; FALLBACK email keys off trusted pushes
only. ``DECLARATIVE`` devices are never counted: the OS renders their
notification without running the service worker, so no receipt can come.

**Retirement.** A device with ``RETIRE_AFTER_PUSHES`` unconfirmed pushes,
the first of them at least ``RETIRE_AFTER`` ago, is retired
(``inactive_at`` set, ``retired_reason`` UNCONFIRMED) and the account is
emailed. The email goes at least once: the worker records
``retired_notice_sent_at`` only once the mail backend accepted it, so a
failed send leaves the row for :func:`enqueue_outstanding_retired_notices`,
which ``send_bulletin_alerts --commit`` calls every hour. A crash between
the send and the write can send it twice; that is the trade for never
losing it. A device the browser re-subscribed (``replaces`` on
``push_register``) is retired as REPLACED by :func:`retire_replaced`, with
no email.

Every write is a single conditional UPDATE, or runs under
``select_for_update``, so a receipt landing while an alert is being sent
neither loses the count nor revives a retired row.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, F, When
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy
from django_tasks import task

from apps.accounts.logging_utils import mask_email
from apps.accounts.models import Account, PushSubscription

logger = logging.getLogger(__name__)

RECEIPT_SALT = "push-receipt"

# How long after a push's TTL its receipt is still taken: clock skew between
# the device and the server, and the time the service worker takes to wake.
RECEIPT_GRACE_SECONDS = 300

# A device is retired once this many alert pushes went unconfirmed …
RETIRE_AFTER_PUSHES = 3
# … and the first of them was sent at least this long ago, so one bad
# morning on a phone left in a drawer does not retire it.
RETIRE_AFTER = datetime.timedelta(days=2)

_SUBJECT_DEVICE_RETIRED = gettext_lazy("Alerts stopped reaching one of your devices")


def receipt_token(sub: PushSubscription, ttl: int) -> str:
    """
    Return the signed receipt token one push to ``sub`` carries.

    Args:
        sub: The subscription the push goes to.
        ttl: The TTL, in seconds, the push is sent with.

    Returns:
        A ``TimestampSigner`` token over the row's uuid and the TTL.

    """
    signer = signing.TimestampSigner(salt=RECEIPT_SALT)
    return signer.sign_object({"s": str(sub.uuid), "ttl": int(ttl)})


def _read_token(token: str, now: datetime.datetime) -> uuid.UUID | None:
    """
    Return the subscription uuid a good, unexpired token names, else None.

    The age is measured against ``now`` rather than the wall clock, so the
    caller (and its tests) decide what "now" is.

    Args:
        token: A token from :func:`receipt_token`.
        now: The time the receipt arrived.

    Returns:
        The uuid, or None for a bad signature, a malformed body or a token
        older than its TTL plus ``RECEIPT_GRACE_SECONDS``.

    """
    signer = signing.TimestampSigner(salt=RECEIPT_SALT)
    try:
        data = signer.unsign_object(token)
        signed_at = signing.b62_decode(token.rsplit(signer.sep, 2)[-2])
        ttl = int(data["ttl"])
        sub_uuid = uuid.UUID(str(data["s"]))
    except signing.BadSignature, KeyError, TypeError, ValueError, IndexError:
        return None
    if now.timestamp() - signed_at > ttl + RECEIPT_GRACE_SECONDS:
        return None
    return sub_uuid


def confirm(token: str, now: datetime.datetime) -> bool:
    """
    Record a delivery receipt.

    One conditional UPDATE sets ``confirmed_at`` and clears the unconfirmed
    count. It never touches ``inactive_at``, so a late receipt from a
    retired device does not bring it back.

    Args:
        token: The receipt token the service worker posted.
        now: When the receipt arrived.

    Returns:
        True when the token was good, whether or not its row still exists;
        False for a bad or expired token.

    """
    sub_uuid = _read_token(token, now)
    if sub_uuid is None:
        return False
    updated = PushSubscription.objects.filter(uuid=sub_uuid).update(
        confirmed_at=now, unconfirmed_pushes=0, unconfirmed_since=None
    )
    logger.info("push receipt for subscription %s: %s row(s)", sub_uuid, updated)
    return True


def count_send(sub_pk: int, now: datetime.datetime) -> bool | None:
    """
    Count one alert push to ``sub_pk`` as unconfirmed, before it goes.

    Runs in its own transaction under ``select_for_update``, so a receipt
    landing at the same moment waits rather than being overwritten.

    Args:
        sub_pk: The subscription's pk.
        now: When the push is sent.

    Returns:
        Whether the push is trusted (the device had confirmed every earlier
        push), or None when the row is gone.

    """
    with transaction.atomic():
        sub = (
            PushSubscription.objects.select_for_update()
            .filter(pk=sub_pk)
            .only("pk", "unconfirmed_pushes", "unconfirmed_since")
            .first()
        )
        if sub is None:
            return None
        trusted = sub.unconfirmed_pushes == 0
        sub.unconfirmed_pushes += 1
        if sub.unconfirmed_since is None:
            sub.unconfirmed_since = now
        sub.save(update_fields=["unconfirmed_pushes", "unconfirmed_since"])
    return trusted


def uncount_send(sub_pk: int) -> None:
    """
    Take back the count for a push the push service did not accept.

    One conditional UPDATE: it never goes below zero, and clears
    ``unconfirmed_since`` once the count is back to zero.

    Args:
        sub_pk: The subscription's pk.

    """
    PushSubscription.objects.filter(pk=sub_pk, unconfirmed_pushes__gt=0).update(
        unconfirmed_pushes=F("unconfirmed_pushes") - 1,
        unconfirmed_since=Case(
            When(unconfirmed_pushes=1, then=None),
            default=F("unconfirmed_since"),
        ),
    )


def retire_if_dead(sub_pk: int, now: datetime.datetime) -> bool:
    """
    Retire ``sub_pk`` if its pushes have gone unconfirmed for too long.

    One conditional UPDATE, filtered on everything that makes a device
    dead, so two workers cannot both retire it. Only the one that did
    enqueues the email, once the transaction commits.

    Args:
        sub_pk: The subscription's pk, after an accepted, counted push.
        now: When the push was sent.

    Returns:
        True when this call retired the row.

    """
    retired = PushSubscription.objects.filter(
        pk=sub_pk,
        mechanism=PushSubscription.Mechanism.SW,
        inactive_at__isnull=True,
        unconfirmed_pushes__gte=RETIRE_AFTER_PUSHES,
        unconfirmed_since__lte=now - RETIRE_AFTER,
    ).update(inactive_at=now, retired_reason=PushSubscription.RetiredReason.UNCONFIRMED)
    if not retired:
        return False
    logger.info("retiring push subscription pk=%s: pushes unconfirmed", sub_pk)
    transaction.on_commit(lambda: _worker_send_device_retired_email.enqueue(sub_pk))
    return True


def retire_replaced(
    account: Account | None, old_endpoint: str, new_endpoint: str
) -> int:
    """
    Retire the subscription a browser's new one replaces.

    A browser that re-subscribes gets a new endpoint; the old row would
    otherwise be pushed to until it went unconfirmed. Only a live row on the
    same account is touched, so one account cannot retire another's device.
    No email: the person is holding the device that replaced it.

    Args:
        account: The registering account, or None for an anonymous staff row.
        old_endpoint: The endpoint the browser held before.
        new_endpoint: The endpoint it registered now.

    Returns:
        How many rows were retired (0 or 1).

    """
    if account is None or not old_endpoint or old_endpoint == new_endpoint:
        return 0
    retired = PushSubscription.objects.filter(
        account=account, endpoint=old_endpoint, inactive_at__isnull=True
    ).update(
        inactive_at=timezone.now(),
        retired_reason=PushSubscription.RetiredReason.REPLACED,
    )
    if retired:
        logger.info("retired %s replaced push subscription(s)", retired)
    return retired


def enqueue_outstanding_retired_notices() -> int:
    """
    Enqueue the retirement email for every row still owed one.

    ``send_bulletin_alerts --commit`` calls this every hour, so an email
    whose send failed goes again on the next run.

    Returns:
        How many tasks were enqueued.

    """
    # Not iterate_rows: this is a helper inside send_bulletin_alerts, not a
    # command of its own; it holds only pks, bounded by the owed-notice
    # backlog, and each enqueue is a write to the task table.
    pks = list(
        PushSubscription.objects.retired_notice_outstanding()
        .order_by("-id")
        .values_list("pk", flat=True)
    )
    for pk in pks:
        _worker_send_device_retired_email.enqueue(pk)
    return len(pks)


def _settings_url() -> str:
    """Return the absolute link to the settings page's alerts section."""
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{reverse('accounts:settings')}#settings-alerts"


@task()
def _worker_send_device_retired_email(sub_pk: int) -> None:
    """
    Background worker: email the account that one of its devices was retired.

    Claims the row under ``select_for_update`` and returns early if the
    notice already went or the row is not retired as UNCONFIRMED (it was
    re-registered, say). ``retired_notice_sent_at`` is set only once
    ``send_mail`` has returned, so a send that raises leaves it null, the
    task fails (and is logged) and the hourly sweep enqueues it again.

    Args:
        sub_pk: The retired subscription's pk.

    """
    with transaction.atomic():
        sub = (
            PushSubscription.objects.select_for_update()
            .select_related("account__user")
            .filter(pk=sub_pk)
            .first()
        )
        if (
            sub is None
            or sub.retired_notice_sent_at is not None
            or sub.retired_reason != PushSubscription.RetiredReason.UNCONFIRMED
            or sub.inactive_at is None
            or sub.account is None
        ):
            return
        email = sub.account.user.email.lower()
        if not email or not sub.account.is_verified:
            return
        context = {
            "set_up": sub.created_at,
            "settings_url": _settings_url(),
        }
        logger.info(
            "Sending device-retired email for subscription pk=%s to %s",
            sub.pk,
            mask_email(email),
        )
        send_mail(
            subject=str(_SUBJECT_DEVICE_RETIRED),
            message=render_to_string("accounts/emails/device_retired.txt", context),
            from_email=settings.ALERTS_FROM_EMAIL,
            recipient_list=[email],
            html_message=render_to_string(
                "accounts/emails/device_retired.html", context
            ),
            fail_silently=False,
        )
        sub.retired_notice_sent_at = timezone.now()
        sub.save(update_fields=["retired_notice_sent_at"])
