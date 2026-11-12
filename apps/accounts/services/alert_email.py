"""
apps/accounts/services/alert_email.py — The bulletin alert email (SNOW-1327).

Email is the alerts fallback: it reaches an account with no push device,
and anyone who asked for both. ``send_alert_email`` sends synchronously and
says whether the message went (SNOW-1362). Its callers are the alert
delivery worker (``apps.bulletins.services.alerts._worker_deliver_alert``),
already a django-tasks task, which needs the result to settle the send log
as SENT or FAILED, and the operator's ``resend_bulletin_alert`` command
(SNOW-1493). Invariant 3 holds: no SMTP round-trip on the request path.
SMTP errors propagate to the caller, which records the alert as not
emailed.

Every message carries ``List-Unsubscribe`` with a signed, never-expiring
link and ``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` (RFC 8058),
which Gmail and Yahoo require of bulk senders and which lets a mail client
offer its own unsubscribe button. Following the link sets the account's
``alert_email`` to NEVER; push alerts are untouched.

Sent from ``settings.ALERTS_FROM_EMAIL``, not the sign-in sender, so the
two reputations are separate (docs/alerts.md).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.logging_utils import mask_email
from apps.accounts.models import Account
from apps.accounts.services.token import (
    SALT_ALERT_UNSUBSCRIBE,
    generate_token,
    verify_token,
)

logger = logging.getLogger(__name__)


def _absolute(path: str) -> str:
    """Return ``path`` on ``SITE_BASE_URL``."""
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"


def unsubscribe_token(account: Account) -> str:
    """
    Return the signed token that stops alert emails for ``account``.

    Args:
        account: The account.

    Returns:
        A URL-safe token embedding the account's uuid.

    """
    return generate_token(str(account.uuid), salt=SALT_ALERT_UNSUBSCRIBE)


def account_for_unsubscribe(token: str) -> Account | None:
    """
    Return the account a token names, or None for a bad token.

    Args:
        token: A token from :func:`unsubscribe_token`.

    Returns:
        The account, or None.

    """
    value = verify_token(token, salt=SALT_ALERT_UNSUBSCRIBE, max_age=None)
    if value is None:
        return None
    return Account.objects.filter(uuid=value).select_related("user").first()


def unsubscribe_url(account: Account) -> str:
    """Return the absolute one-click unsubscribe URL for ``account``."""
    return _absolute(
        reverse(
            "accounts:alert_unsubscribe",
            kwargs={"token": unsubscribe_token(account)},
        )
    )


def send_alert_email(
    account: Account, title: str, body: str, url: str, *, force: bool = False
) -> bool:
    """
    Render and send one alert email for ``account``, now.

    Called from the alert delivery worker and from ``resend_bulletin_alert``,
    never on a request path. An account that turned email off, never
    verified or has no address is not emailed. SMTP errors are not caught:
    the caller records them.

    Args:
        account: The recipient, with ``user`` loaded.
        title: The alert's title, used as the subject.
        body: The alert's body.
        url: The site-relative link the alert opens.
        force: Send even when the account turned alert email off. An
            operator's explicit resend (SNOW-1493) sets it; an unverified
            or empty address is still never emailed.

    Returns:
        True only when the mail backend accepted the message.

    """
    if account.alert_email == Account.AlertEmail.NEVER and not force:
        return False
    email = account.user.email.lower()
    if not email or not account.is_verified:
        return False
    unsubscribe = unsubscribe_url(account)
    context = {
        "title": title,
        "body": body,
        "open_url": _absolute(url),
        "settings_url": _absolute(reverse("accounts:settings")),
        "unsubscribe_url": unsubscribe,
    }
    message = EmailMultiAlternatives(
        subject=title,
        body=render_to_string("accounts/emails/bulletin_alert.txt", context),
        from_email=settings.ALERTS_FROM_EMAIL,
        to=[email],
        headers={
            "List-Unsubscribe": f"<{unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    message.attach_alternative(
        render_to_string("accounts/emails/bulletin_alert.html", context), "text/html"
    )
    logger.info("Sending bulletin alert email to %s", mask_email(email))
    return message.send() > 0
