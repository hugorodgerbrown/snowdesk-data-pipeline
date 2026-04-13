# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib email package at runtime.
"""
subscriptions/services/email.py — Magic-link email delivery.

Provides a single function that generates a signed magic-link token, builds
the verification URL, renders plain-text and HTML templates, and dispatches
the email via Django's mail backend.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string

from .token import generate_magic_link_token

logger = logging.getLogger(__name__)

# Path prefix for the magic-link verification endpoint.
_VERIFY_PATH = "/subscribe/verify/"


def send_magic_link_email(
    email: str,
    purpose: str = "login",
    request: HttpRequest | None = None,
) -> None:
    """
    Generate a magic-link token and send it to the given email address.

    Builds the verification URL from settings.MAGIC_LINK_BASE_URL (or the
    request origin when provided), renders both plain-text and HTML
    templates, and dispatches via Django's configured mail backend.

    Args:
        email: Recipient email address.
        purpose: Token purpose label shown in the email body (e.g. "login").
        request: Optional HttpRequest used to derive the base URL.

    """
    token = generate_magic_link_token(email=email, purpose=purpose)

    if request is not None:
        base_url = request.build_absolute_uri("/").rstrip("/")
    else:
        base_url = settings.MAGIC_LINK_BASE_URL.rstrip("/")

    magic_link_url = f"{base_url}{_VERIFY_PATH}?{urlencode({'token': token})}"
    expiry_minutes = settings.MAGIC_LINK_EXPIRY_SECONDS // 60

    context = {
        "magic_link_url": magic_link_url,
        "purpose": purpose,
        "expiry_minutes": expiry_minutes,
    }

    subject = f"Your SnowDesk {purpose} link"
    plain_body = render_to_string("subscriptions/emails/magic_link.txt", context)
    html_body = render_to_string("subscriptions/emails/magic_link.html", context)

    logger.info("Sending magic-link email to %s (purpose=%s)", email, purpose)

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )
