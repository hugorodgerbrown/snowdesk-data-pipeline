# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib email package at runtime.
"""
subscriptions/services/email.py — Account-access email delivery.

Provides two public functions:

``send_account_access_email(email, *, request=None)``
    Generates an account-access token, builds an absolute URL pointing at
    ``/subscribe/account/<token>/``, renders plain-text and HTML templates,
    and dispatches via Django's configured mail backend.

``send_noop_email(email)``
    Performs the same token-generation and template-render work as
    ``send_account_access_email`` but does **not** call ``send_mail``.
    Use this on the active-subscriber branch of the subscribe form so the
    timing profile (token gen + render) is similar to the real path without
    leaking account existence to an observer.

    Limitation: The noop path saves ~1 ms of network I/O vs the real send,
    so it is not a perfect timing equaliser — sufficient for v1.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy

from .token import SALT_ACCOUNT_ACCESS, generate_token

logger = logging.getLogger(__name__)

# Path template for account-access links: ``/subscribe/account/<token>/``
_ACCOUNT_PATH_PREFIX = "/subscribe/account/"

# Email subject used for all account-access messages.
# gettext_lazy is required here (not plain gettext) so xgettext / makemessages
# can extract the string from module scope.
_SUBJECT = gettext_lazy("Your Snowdesk account link")


def _build_account_url(token: str, request: HttpRequest | None) -> str:
    """
    Build the absolute account-access URL for a given token.

    Uses ``request.build_absolute_uri()`` when available; falls back to
    ``settings.SITE_BASE_URL`` so that callers outside a request context
    (management commands, background tasks) still produce a valid URL.

    Args:
        token: The signed token string.
        request: Optional incoming HTTP request.

    Returns:
        Absolute URL string, e.g. ``https://example.com/subscribe/account/<token>/``.

    """
    path = f"{_ACCOUNT_PATH_PREFIX}{token}/"
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"


def send_account_access_email(
    email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Send an account-access email to ``email``.

    Generates a short-lived token signed with ``SALT_ACCOUNT_ACCESS``,
    builds an absolute URL, renders templates, and dispatches via the
    configured ``EMAIL_BACKEND``.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, request)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
    }

    subject = str(_SUBJECT)
    plain_body = render_to_string("subscriptions/emails/account_access.txt", context)
    html_body = render_to_string("subscriptions/emails/account_access.html", context)

    logger.info("Sending account-access email to %s", email)

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


def send_noop_email(email: str) -> None:
    """
    Perform the same work as ``send_account_access_email`` without sending.

    Generates a token and renders the templates to maintain a similar timing
    profile to the real send path.  Used on the active-subscriber branch of
    the subscribe form so the response time does not reveal whether an email
    address is already registered.

    Note: This is a best-effort timing equaliser only.  The actual network
    I/O of ``send_mail`` is skipped, so there is a small timing difference.

    Args:
        email: The email address to use for token generation (not sent to).

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, None)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
    }

    # Render templates to mirror the real code path's CPU work.
    render_to_string("subscriptions/emails/account_access.txt", context)
    render_to_string("subscriptions/emails/account_access.html", context)

    logger.debug("Noop email for %s (no message sent)", email)
