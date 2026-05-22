# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib email package at runtime.
"""
subscriptions/services/email.py — Email delivery for the subscription flow.

Provides three public functions:

``send_account_access_email(email, *, request=None)``
    Generates an account-access token, builds an absolute URL pointing at
    ``/subscribe/account/<token>/``, renders plain-text and HTML templates,
    and enqueues delivery via ``django.tasks``.

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

All three public functions enqueue work through ``django.tasks`` so the
SMTP round-trip runs off the request cycle and the request handler returns
immediately, closing the timing-side-channel on the manage POST endpoint
(SNOW-26).  The active backend is determined by the ``TASKS["default"]``
setting:

- Development/tests: ``ImmediateBackend`` — runs tasks synchronously inline,
  so Mailhog and ``mail.outbox`` see every message without any worker process.
- Production: currently also ``ImmediateBackend`` (runs inline), pending
  availability of ``DatabaseBackend`` in a future Django release.  Upgrade
  ``TASKS["default"]`` in ``config/settings/production.py`` once a
  persistent backend ships.

Worker functions (``_worker_send_account_access``,
``_worker_send_subscription_confirmation``, ``_worker_simulate``) take only
JSON-serialisable primitives so that any persistent backend can safely
serialise and replay them.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpRequest
from django.tasks import task
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy

from regions.models import MicroRegion

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
        base_url: Optional base URL string (scheme + host, no trailing slash),
            extracted from the request before enqueueing so the worker does
            not need a live request object.

    Returns:
        Absolute URL string, e.g. ``https://example.com/subscribe/account/<token>/``.

    """
    path = f"{_ACCOUNT_PATH_PREFIX}{token}/"
    if base_url is not None:
        return f"{base_url.rstrip('/')}{path}"
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"


def _extract_base_url(request: HttpRequest | None) -> str | None:
    """
    Extract the scheme-and-host base URL from an HttpRequest.

    Returns None when no request is available, which causes the worker to
    fall back to ``settings.SITE_BASE_URL``.

    Args:
        request: Optional incoming HTTP request.

    Returns:
        Base URL string (e.g. ``https://snowdesk.info``) or None.

    """
    if request is None:
        return None
    return request.build_absolute_uri("/").rstrip("/")


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------
# Decorated with @task so django.tasks can enqueue (and optionally serialise)
# them.  Arguments are primitives only: str, int, float, bool, None — no
# model instances, no request objects.


@task
def _worker_send_account_access(email: str, base_url: str | None) -> None:
    """
    Task worker: generate token, render templates, dispatch account-access email.

    Executed by the configured ``TASKS["default"]`` backend.  Generating the
    token inside the worker means any retried execution issues a fresh token
    rather than replaying a potentially stale one — preferable given the 24h
    TTL on account-access tokens.

    Args:
        email: Recipient email address.
        base_url: Scheme-and-host base URL for the account-access link, or
            None to use ``settings.SITE_BASE_URL``.

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

    logger.info("Sending account-access email to %s", email)
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


@task
def _worker_send_subscription_confirmation(
    email: str,
    region_name: str,
    base_url: str | None,
) -> None:
    """
    Task worker: generate token, render templates, dispatch subscription confirmation.

    Args:
        email: Recipient email address.
        region_name: Display name of the newly-added region (serialised as a
            plain string so the worker carries no ORM dependency).
        base_url: Scheme-and-host base URL for the account-access link, or
            None to use ``settings.SITE_BASE_URL``.

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
        email,
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


@task
def _worker_simulate(email: str) -> None:
    """
    Task worker: perform token-gen + template-render without dispatching any email.

    Mirrors ``_worker_send_account_access``'s CPU cost so the unknown-email
    branch of ``POST /subscribe/manage/`` is timing-indistinguishable from the
    real send path (SNOW-26 mitigation).

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


# ---------------------------------------------------------------------------
# Public API — thin wrappers that extract request-derived values and enqueue
# ---------------------------------------------------------------------------


def send_account_access_email(
    email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue an account-access email for ``email``.

    Extracts the base URL from the request (if available) and passes it as a
    primitive to the task worker so the worker carries no request dependency.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_account_access.enqueue(email, base_url)


def send_subscription_confirmation_email(
    email: str,
    *,
    region: MicroRegion,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue a subscription confirmation email for an active subscriber.

    Called when an already-active subscriber adds a new region via the
    inline subscribe CTA.

    Args:
        email: Recipient email address.
        region: The newly-added MicroRegion instance (provides ``region.name``).
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_subscription_confirmation.enqueue(email, region.name, base_url)


def simulate_account_access_work(email: str) -> None:
    """
    Enqueue simulated CPU work to equalise timing on the unknown-email branch.

    Generates a token and renders both email templates but skips the
    ``send_mail`` call.  Used on the unknown-email branch of
    ``POST /subscribe/manage/`` so the response timing profile matches the
    real send path — both branches enqueue through ``django.tasks`` and
    return to the client immediately (SNOW-26).

    Args:
        email: The email address to use for token generation (not sent to).

    """
    _worker_simulate.enqueue(email)
