# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the apps/accounts/services/ package so it does not shadow
# the stdlib email package at runtime.
"""
apps/accounts/services/email.py — Email delivery for the subscription flow.

Provides two public functions:

``send_account_access_email(email, *, request=None, next_url=None)``
    Generates an account-access token, builds an absolute URL pointing at
    ``/account/access/<token>/``, renders plain-text and HTML templates,
    and dispatches via Django's configured mail backend.  A ``next_url``
    (already validated by the calling view) rides along as a URL-encoded
    ``?next=`` parameter so the recipient lands where they were heading
    before sign-in (SNOW-825).

``send_verification_email(email, *, request=None)``
    Generates an email-verification token (``SALT_EMAIL_VERIFICATION``),
    builds an absolute URL pointing at ``/account/verify/<token>/``, and
    dispatches the registration verification email (SNOW-430).

``send_password_reset_email(email, *, request=None)``
    Sends a single-use password-reset link (``SALT_PASSWORD_RESET``) to
    ``/account/reset-password/<token>/`` — but only for a real account with a
    usable password; unknown / passwordless addresses are a silent no-op
    (SNOW-432).

``send_subscription_confirmation_email(email, *, region, request=None)``
    Sends a confirmation email to an already-active subscriber who just added
    a new region.  Generates an account-access token (same salt as the
    account-access flow) so the link in the email lands directly on the
    manage page.  Includes the region name in the subject and body.

``send_email_change_confirmation(user, new_email, *, request=None)``
    Sends a confirmation link (``SALT_EMAIL_CHANGE``) to the **new** address; on
    confirmation the account's email is swapped (SNOW-433).

``send_email_change_notice(email, *, stage, request=None)``
    Sends a link-free FYI to the **old** address on both request and completion
    of an email change (SNOW-433).

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
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy
from django_tasks import task

from apps.regions.models import MicroRegion

if TYPE_CHECKING:
    from django.contrib.auth.models import User

from ..logging_utils import mask_email
from .token import (
    SALT_ACCOUNT_ACCESS,
    SALT_EMAIL_VERIFICATION,
    generate_email_change_token,
    generate_password_reset_token,
    generate_token,
)

logger = logging.getLogger(__name__)

# Path template for account-access links: ``/account/access/<token>/``
_ACCOUNT_PATH_PREFIX = "/account/access/"

# Path template for registration email-verification links:
# ``/account/verify/<token>/`` (SNOW-430).
_VERIFY_PATH_PREFIX = "/account/verify/"

# Path template for password-reset confirm links:
# ``/account/reset-password/<token>/`` (SNOW-432).
_RESET_PATH_PREFIX = "/account/reset-password/"

# Path template for email-change confirm links:
# ``/account/change-email/<token>/`` (SNOW-433).
_EMAIL_CHANGE_PATH_PREFIX = "/account/change-email/"

# Email subjects — gettext_lazy so xgettext / makemessages can extract them at
# module scope.  Use %-named placeholders (not f-strings) as xgettext cannot
# parse f-strings.
_SUBJECT_ACCESS = gettext_lazy("Your Snowdesk account link")
_SUBJECT_SUBSCRIBED = gettext_lazy("Snowdesk: you're subscribed to %(region_name)s")
_SUBJECT_VERIFY = gettext_lazy("Verify your Snowdesk email address")
_SUBJECT_RESET = gettext_lazy("Reset your Snowdesk password")
_SUBJECT_EMAIL_CHANGE = gettext_lazy("Confirm your new Snowdesk email address")
_SUBJECT_EMAIL_CHANGE_NOTICE = gettext_lazy("Your Snowdesk email address is changing")


def _build_account_url(
    token: str, base_url: str | None, next_url: str | None = None
) -> str:
    """
    Build the absolute account-access URL for a given token.

    Uses ``base_url`` when provided; falls back to ``settings.SITE_BASE_URL``
    so that callers outside a request context (management commands, background
    tasks) still produce a valid URL.

    ``next_url`` is appended as a URL-encoded ``?next=`` query parameter
    (SNOW-825) so the person following the link lands where they were heading
    before they were asked to sign in. It rides in the query string rather
    than inside the signed token because the token is what authenticates and
    ``next`` is only a destination; ``apps.accounts.views.account_view``
    re-validates it with ``safe_next`` on arrival, so a tampered value can
    only redirect the tamperer, and never off-site.

    Args:
        token: The signed token string.
        base_url: Optional absolute base URL (scheme + host) extracted from
            the originating request before enqueueing.
        next_url: Optional same-site destination, already validated by the
            view that accepted it.

    Returns:
        Absolute URL string, e.g. ``https://example.com/account/access/<token>/``.

    """
    path = f"{_ACCOUNT_PATH_PREFIX}{token}/"
    resolved_base = (
        base_url
        if base_url is not None
        else getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    query = f"?{urlencode({'next': next_url})}" if next_url else ""
    return f"{resolved_base.rstrip('/')}{path}{query}"


def _build_verify_url(token: str, base_url: str | None) -> str:
    """
    Build the absolute email-verification URL for a given token.

    Mirrors ``_build_account_url`` but points at the registration
    verification route (``/account/verify/<token>/``).

    Args:
        token: The signed token string.
        base_url: Optional absolute base URL (scheme + host) extracted from
            the originating request before enqueueing.

    Returns:
        Absolute URL string, e.g. ``https://example.com/account/verify/<token>/``.

    """
    path = f"{_VERIFY_PATH_PREFIX}{token}/"
    resolved_base = (
        base_url
        if base_url is not None
        else getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    return f"{resolved_base.rstrip('/')}{path}"


def _build_reset_url(token: str, base_url: str | None) -> str:
    """
    Build the absolute password-reset confirm URL for a given token.

    Args:
        token: The signed reset token string.
        base_url: Optional absolute base URL (scheme + host) extracted from
            the originating request before enqueueing.

    Returns:
        Absolute URL string, e.g. ``https://example.com/account/reset-password/<token>/``.

    """
    path = f"{_RESET_PATH_PREFIX}{token}/"
    resolved_base = (
        base_url
        if base_url is not None
        else getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    return f"{resolved_base.rstrip('/')}{path}"


def _build_email_change_url(token: str, base_url: str | None) -> str:
    """
    Build the absolute email-change confirm URL for a given token.

    Args:
        token: The signed email-change token string.
        base_url: Optional absolute base URL (scheme + host).

    Returns:
        Absolute URL string, e.g. ``https://example.com/account/change-email/<token>/``.

    """
    path = f"{_EMAIL_CHANGE_PATH_PREFIX}{token}/"
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
def _worker_send_account_access_email(
    email: str, base_url: str | None, next_url: str | None = None
) -> None:
    """
    Background worker: generate a token and send the account-access email.

    Token generation is deferred into the worker so that a retry issues a
    fresh token rather than replaying a stale one from the original enqueue.

    Args:
        email: Recipient email address.
        base_url: Absolute base URL (scheme + host) extracted from the
            originating request, or ``None`` to fall back to SITE_BASE_URL.
        next_url: Optional same-site destination to return the recipient to
            after they sign in (SNOW-825), already validated by the view.
            Defaults to ``None`` so a task row enqueued before this argument
            existed still replays.

    """
    token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
    account_url = _build_account_url(token, base_url, next_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "account_url": account_url,
        "expiry_hours": expiry_hours,
    }

    subject = str(_SUBJECT_ACCESS)
    plain_body = render_to_string("accounts/emails/account_access.txt", context)
    html_body = render_to_string("accounts/emails/account_access.html", context)

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
    plain_body = render_to_string("accounts/emails/account_subscribed.txt", context)
    html_body = render_to_string("accounts/emails/account_subscribed.html", context)

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


@task()
def _worker_send_verification_email(email: str, base_url: str | None) -> None:
    """
    Background worker: generate a token and send the email-verification email.

    Token generation is deferred into the worker so that a retry issues a
    fresh token rather than replaying a stale one from the original enqueue.
    Uses ``SALT_EMAIL_VERIFICATION`` so a verification token cannot be
    replayed against the account-access flow (or vice versa).

    Args:
        email: Recipient email address.
        base_url: Absolute base URL (scheme + host) extracted from the
            originating request, or ``None`` to fall back to SITE_BASE_URL.

    """
    token = generate_token(email, salt=SALT_EMAIL_VERIFICATION)
    verify_url = _build_verify_url(token, base_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {
        "verify_url": verify_url,
        "expiry_hours": expiry_hours,
    }

    subject = str(_SUBJECT_VERIFY)
    plain_body = render_to_string("accounts/emails/verification.txt", context)
    html_body = render_to_string("accounts/emails/verification.html", context)

    logger.info("Sending email-verification email to %s", mask_email(email))

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


@task()
def _worker_send_password_reset_email(email: str, base_url: str | None) -> None:
    """
    Background worker: send a password-reset email to eligible accounts only.

    Only real accounts with a usable password receive a link.
    Unknown emails and passwordless (magic-link / passkey-only) accounts are a
    silent no-op: no email is sent, but the request view returns the same
    "check your inbox" response either way, so nothing is leaked.  Token
    generation is deferred into the worker (fresh token on retry) and binds
    the current password hash, making the token single-use.

    Args:
        email: Recipient email address (lowercased username).
        base_url: Absolute base URL (scheme + host), or ``None`` for
            SITE_BASE_URL.

    """
    user_model = get_user_model()
    try:
        user = user_model.objects.get(username=email.lower())
    except user_model.DoesNotExist:
        logger.info(
            "Password reset requested for unknown %s — no email", mask_email(email)
        )
        return
    if not user.has_usable_password():
        logger.info(
            "Password reset requested for passwordless account %s — no email",
            mask_email(email),
        )
        return

    token = generate_password_reset_token(user)
    reset_url = _build_reset_url(token, base_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {"reset_url": reset_url, "expiry_hours": expiry_hours}
    subject = str(_SUBJECT_RESET)
    plain_body = render_to_string("accounts/emails/password_reset.txt", context)
    html_body = render_to_string("accounts/emails/password_reset.html", context)

    logger.info("Sending password-reset email to %s", mask_email(email))

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_body,
        fail_silently=False,
    )


@task()
def _worker_send_email_change_confirmation(
    user_pk: int, new_email: str, base_url: str | None
) -> None:
    """
    Background worker: send the email-change confirmation to the NEW address.

    The confirmation link, once clicked and confirmed, swaps the account's
    address (SNOW-433).  Token generation is deferred into the worker and binds
    the user + the specific new address.

    Args:
        user_pk: Primary key of the account requesting the change.
        new_email: The requested new address (the recipient).
        base_url: Absolute base URL (scheme + host), or ``None``.

    """
    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_pk)
    except user_model.DoesNotExist:
        return

    token = generate_email_change_token(user, new_email)
    confirm_url = _build_email_change_url(token, base_url)
    expiry_hours = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400) // 3600

    context = {"confirm_url": confirm_url, "expiry_hours": expiry_hours}
    subject = str(_SUBJECT_EMAIL_CHANGE)
    plain_body = render_to_string("accounts/emails/email_change_confirm.txt", context)
    html_body = render_to_string("accounts/emails/email_change_confirm.html", context)

    logger.info("Sending email-change confirmation to %s", mask_email(new_email))

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[new_email],
        html_message=html_body,
        fail_silently=False,
    )


@task()
def _worker_send_email_change_notice(email: str, stage: str) -> None:
    """
    Background worker: send an FYI notice to the OLD address (no link).

    Sent both when an email change is requested and when it completes so the
    original address always learns about the change.  Carries no link and does
    not disclose the new address.

    Args:
        email: The old (current) address — the recipient.
        stage: ``"requested"`` or ``"completed"``.

    """
    context = {"stage": stage}
    subject = str(_SUBJECT_EMAIL_CHANGE_NOTICE)
    plain_body = render_to_string("accounts/emails/email_change_notice.txt", context)
    html_body = render_to_string("accounts/emails/email_change_notice.html", context)

    logger.info("Sending email-change notice (%s) to %s", stage, mask_email(email))

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
    next_url: str | None = None,
) -> None:
    """
    Enqueue an account-access email to ``email``.

    Extracts the base URL from ``request`` (if provided) before enqueueing so
    the worker carries no request dependency.  Token generation is deferred to
    the worker so a retry issues a fresh token.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.
        next_url: Optional same-site destination for the link to return to
            after sign-in (SNOW-825). The CALLER validates it — pass the
            output of ``apps.accounts.redirects.safe_next``, never a raw
            request value.

    """
    base_url = _extract_base_url(request)
    _worker_send_account_access_email.enqueue(email, base_url, next_url)


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


def send_verification_email(
    email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue an email-verification email to ``email`` (SNOW-430).

    Sent during registration (and on re-registration of an unverified email).
    Extracts the base URL from ``request`` (if provided) before enqueueing so
    the worker carries no request dependency.  Token generation is deferred to
    the worker so a retry issues a fresh token.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_verification_email.enqueue(email, base_url)


def send_password_reset_email(
    email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue a password-reset email to ``email`` (SNOW-432).

    Always safe to call regardless of whether the address exists or has a
    password — the worker no-ops silently in those cases while the caller
    returns the same "check your inbox" response, preserving the
    anti-enumeration invariant.

    Args:
        email: Recipient email address.
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_password_reset_email.enqueue(email, base_url)


def send_email_change_confirmation(
    user: User,
    new_email: str,
    *,
    request: HttpRequest | None = None,
) -> None:
    """
    Enqueue an email-change confirmation to the new address (SNOW-433).

    Args:
        user: The account requesting the change (only ``user.pk`` is used).
        new_email: The requested new address (the recipient).
        request: Optional HttpRequest used to derive the absolute base URL.

    """
    base_url = _extract_base_url(request)
    _worker_send_email_change_confirmation.enqueue(user.pk, new_email, base_url)


def send_email_change_notice(
    email: str,
    *,
    stage: str,
    request: HttpRequest | None = None,  # noqa: ARG001 — kept for call-site symmetry
) -> None:
    """
    Enqueue an FYI notice (no link) to the old address (SNOW-433).

    Sent on both request and completion of an email change.

    Args:
        email: The old (current) address — the recipient.
        stage: ``"requested"`` or ``"completed"``.
        request: Unused; accepted so callers can pass it uniformly.

    """
    _worker_send_email_change_notice.enqueue(email, stage)
