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

Both enqueue work via the django-tasks ``@task`` decorator so the SMTP
round-trip runs off the request cycle, returning immediately (SNOW-26).

In development and tests the ``ImmediateBackend`` runs tasks inline;
production uses ``django_tasks_db.DatabaseBackend`` with a Render Background
Worker consuming the queue via ``python manage.py db_worker``.

Worker functions receive only JSON-serialisable primitives (str, int) so they
can be serialised into the DB and replayed on retry.  Token generation is
deferred into the worker so that a retry issues a fresh token rather than
replaying a potentially-stale one.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy
from django_tasks import task

from regions.models import MicroRegion

from ..logging_utils import mask_email
from .token import SALT_ACCOUNT_ACCESS, generate_token

logger = logging.getLogger(__name__)

# Path template for account-access links: ``/subscribe/account/<token>/``
_ACCOUNT_PATH_PREFIX = "/subscribe/account/"

# Email subjects — gettext_lazy so xgettext / makemessages can extract them at
# module scope.  Use %-named placeholders (not f-strings) as xgettext cannot
# parse f-strings.
_SUBJECT_ACCESS = gettext_lazy("Your Snowdesk account link")
_SUBJECT_SUBSCRIBED = gettext_lazy("Snowdesk: you're subscribed to %(region_name)s")


def _build_account_url(token: str, base_url: str | None) -> str:
    """
    Build the absolute account-access URL for a given token.

    Uses ``base_url`` when provided; falls back to ``settings.SITE_BASE_URL``
    so that callers outside a request context (management commands, background
    tasks) still produce a valid URL.

    Args:
        token: The signed token string.
        base_url: Optional absolute base URL (scheme + host) extracted from
            the originating request before enqueueing.

    Returns:
        Absolute URL string, e.g. ``https://example.com/subscribe/account/<token>/``.

    """
    path = f"{_ACCOUNT_PATH_PREFIX}{token}/"
    resolved_base = (
        base_url
        if base_url is not None
        else getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    return f"{resolved_base.rstrip('/')}{path}"


def _extract_base_url(request: HttpRequest | None) -> str | None:
    """
    Extract the absolute base URL (scheme + host) from an HTTP request.

    Returns ``None`` when no request is available; ``_build_account_url``
    falls back to ``settings.SITE_BASE_URL`` in that case.

    Args:
        request: Optional incoming HTTP request.

    Returns:
        Absolute base URL string (no trailing slash) or ``None``.

    """
    if request is None:
        return None
    # build_absolute_uri("/") gives "https://host/" — strip the trailing slash.
    return request.build_absolute_uri("/").rstrip("/")


# ---------------------------------------------------------------------------
# Worker functions — decorated with @task so django-tasks can enqueue and
# replay them.  Each accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_send_account_access_email(email: str, base_url: str | None) -> None:
    """
    Background worker: generate a token and send the account-access email.

    Token generation is deferred into the worker so that a retry issues a
    fresh token rather than replaying a stale one from the original enqueue.

    Args:
        email: Recipient email address.
        base_url: Absolute base URL (scheme + host) extracted from the
            originating request, or ``None`` to fall back to SITE_BASE_URL.

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, base_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
    }

    subject = str(_SUBJECT_ACCESS)
    plain_body = render_to_string("subscriptions/emails/account_access.txt", context)
    html_body = render_to_string("subscriptions/emails/account_access.html", context)

    logger.info("Sending account-access email to %s", mask_email(email))

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


@task()
def _worker_send_subscription_confirmation_email(
    email: str,
    region_name: str,
    base_url: str | None,
) -> None:
    """
    Background worker: generate a token and send the subscription confirmation email.

    Token generation is deferred into the worker so that a retry issues a
    fresh token rather than replaying a stale one from the original enqueue.

    Args:
        email: Recipient email address.
        region_name: Human-readable name of the newly-subscribed region.
        base_url: Absolute base URL (scheme + host) extracted from the
            originating request, or ``None`` to fall back to SITE_BASE_URL.

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, base_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
        "region_name": region_name,
    }

    subject = str(_SUBJECT_SUBSCRIBED % {"region_name": region_name})
    plain_body = render_to_string(
        "subscriptions/emails/account_subscribed.txt", context
    )
    html_body = render_to_string(
        "subscriptions/emails/account_subscribed.html", context
    )

    logger.info(
        "Sending subscription confirmation email to %s for region %s",
        mask_email(email),
        region_name,
    )

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


# ---------------------------------------------------------------------------
# Public API — thin wrappers that extract request data and enqueue a worker.
# ---------------------------------------------------------------------------


def send_account_access_email(
    email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue an account-access email to ``email``.

    Extracts the base URL from ``request`` (if provided) before enqueueing so
    the worker carries no request dependency.  Token generation is deferred to
    the worker so a retry issues a fresh token.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_account_access_email.enqueue(email, base_url)


def send_subscription_confirmation_email(
    email: str,
    *,
    region: MicroRegion,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue a subscription confirmation email to an active subscriber.

    Called when an already-active subscriber adds a new region via the
    inline subscribe CTA.  The embedded link uses an account-access token
    so the subscriber lands directly on the manage page.

    Args:
        email: Recipient email address.
        region: The newly-added MicroRegion instance (provides ``region.name``).
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_subscription_confirmation_email.enqueue(email, region.name, base_url)
