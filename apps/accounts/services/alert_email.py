"""
apps/accounts/services/alert_email.py — The bulletin alert email (SNOW-1327).

Email is the alerts fallback: it reaches an account with no push device,
and anyone who asked for both. ``send_alert_email`` enqueues a django-tasks
worker (Invariant 3: never an SMTP round-trip on the request path, and the
send command is not a request but keeps the same shape so the two paths
cannot drift).

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
from django_tasks import task

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


@task()
def _worker_send_alert_email(account_id: int, title: str, body: str, url: str) -> None:
    """
    Background worker: render and send one alert email.

    Re-reads the account so a person who turned email off between the run
    and the send is not emailed.

    Args:
        account_id: The account's pk.
        title: The alert's title, used as the subject.
        body: The alert's body.
        url: The site-relative link the alert opens.

    """
    account = Account.objects.filter(pk=account_id).select_related("user").first()
    if account is None or account.alert_email == Account.AlertEmail.NEVER:
        return
    email = account.user.email.lower()
    if not email or not account.is_verified:
        return
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
    message.send()


def send_alert_email(account: Account, title: str, body: str, url: str) -> None:
    """
    Enqueue one alert email for ``account``.

    Args:
        account: The recipient.
        title: The alert's title.
        body: The alert's body.
        url: The site-relative link the alert opens.

    """
    _worker_send_alert_email.enqueue(account.pk, title, body, url)
