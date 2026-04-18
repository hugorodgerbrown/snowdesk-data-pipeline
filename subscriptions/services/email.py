# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib email package at runtime.
"""
subscriptions/services/email.py — Email delivery for the subscription flow.

Provides two public functions:

``send_account_access_email(email, *, request=None)``
    Generates an account-access token, builds an absolute URL pointing at
    ``/subscribe/account/<token>/``, renders plain-text and HTML templates,
    and dispatches via Django's configured mail backend.

``send_subscription_confirmation_email(email, *, region, request=None)``
    Sends a confirmation email to an already-active subscriber who just added
    a new region.  Generates an account-access token (same salt as the
    account-access flow) so the link in the email lands directly on the
    manage page.  Includes the region name in the subject and body.

``simulate_account_access_work(email)``
    Performs the same token generation and template rendering as
    ``send_account_access_email`` but does **not** call ``send_mail``.  Used
    on the unknown-email branch of ``POST /subscribe/manage/`` so the CPU
    timing profile roughly matches the real send path — a mitigation against
    enumeration timing attacks against the re-auth endpoint.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy

from pipeline.models import Region

from .token import SALT_ACCOUNT_ACCESS, generate_token

logger = logging.getLogger(__name__)

# Path template for account-access links: ``/subscribe/account/<token>/``
_ACCOUNT_PATH_PREFIX = "/subscribe/account/"

# Email subjects — gettext_lazy so xgettext / makemessages can extract them at
# module scope.  Use %-named placeholders (not f-strings) as xgettext cannot
# parse f-strings.
_SUBJECT_ACCESS = gettext_lazy("Your Snowdesk account link")
_SUBJECT_SUBSCRIBED = gettext_lazy("Snowdesk: you're subscribed to %(region_name)s")


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

    subject = str(_SUBJECT_ACCESS)
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


def send_subscription_confirmation_email(
    email: str,
    *,
    region: Region,
    request: HttpRequest | None = None,
) -> None:
    """
    Send a subscription confirmation email to an active subscriber.

    Called when an already-active subscriber adds a new region via the
    inline subscribe CTA.  Generates an account-access token (same salt as
    ``send_account_access_email``) so the embedded link lands directly on
    the manage page without re-authentication.

    Args:
        email: Recipient email address.
        region: The newly-added Region instance (provides ``region.name``).
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, request)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
        "region_name": region.name,
    }

    subject = str(_SUBJECT_SUBSCRIBED % {"region_name": region.name})
    plain_body = render_to_string(
        "subscriptions/emails/account_subscribed.txt", context
    )
    html_body = render_to_string(
        "subscriptions/emails/account_subscribed.html", context
    )

    logger.info(
        "Sending subscription confirmation email to %s for region %s",
        email,
        region.name,
    )

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


def simulate_account_access_work(email: str) -> None:
    """
    Perform the CPU work of ``send_account_access_email`` without sending.

    Generates a token and renders both email templates but skips the
    ``send_mail`` call.  This narrows the timing side-channel on the
    ``POST /subscribe/manage/`` unknown-email branch, where an attacker
    probing whether an address is registered would otherwise see a
    measurably faster response on unknown emails.

    Not a perfect equaliser — the real path still pays the SMTP round-trip
    cost.  Good enough given the 3/min rate limit on the endpoint.

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

    # Render both templates to mirror the real code path's CPU cost.
    render_to_string("subscriptions/emails/account_access.txt", context)
    render_to_string("subscriptions/emails/account_access.html", context)

    logger.debug("Simulated account-access work for %s (no message sent)", email)
