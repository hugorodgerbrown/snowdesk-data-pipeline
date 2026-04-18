"""
subscriptions/views.py — HTTP views for the subscriptions application.

Implements the new subscription flow built around Django's TimestampSigner:

  subscribe_partial   POST — inline HTMX subscribe CTA on bulletin pages.
  account_view        GET  — verify account-access token; activate subscriber.
  manage_view         GET/POST — unauthenticated email entry OR
                               authenticated region checkbox management.
  unsubscribe_view    GET/POST — token-verified one-click unsubscribe.

Rate limiting via django-ratelimit (block=False pattern):
  subscribe_partial: 5 requests/min per IP.
  manage_view POST (unauthenticated): 3 requests/min per IP.
  unsubscribe_view: 10 requests/min per IP.

Session key ``subscriber_uuid`` carries the authenticated subscriber's UUID
across the manage page steps.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit

from pipeline.decorators import require_htmx
from pipeline.models import Region

from .forms import EmailForm, RegionSelectionForm, SubscribeForm
from .models import Subscriber, Subscription
from .services.email import send_account_access_email, send_noop_email
from .services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_unsubscribe_token,
    verify_token,
    verify_unsubscribe_token,
)

logger = logging.getLogger(__name__)

# Session key that stores the authenticated subscriber's UUID string.
_SESSION_KEY = "subscriber_uuid"

# Template for the generic link-expired / bad-token error page.
_LINK_EXPIRED_TEMPLATE = "subscriptions/link_expired.html"


def _get_subscriber_from_session(request: HttpRequest) -> Subscriber | None:
    """
    Look up an active Subscriber from the session, or return None.

    Reads ``session['subscriber_uuid']``, attempts to fetch the matching
    active Subscriber, and returns it.  Returns None when the key is absent,
    the UUID is malformed, or no active subscriber is found.

    Args:
        request: The incoming HTTP request with an attached session.

    Returns:
        The active Subscriber instance, or None.

    """
    uuid_str = request.session.get(_SESSION_KEY)
    if not uuid_str:
        return None
    try:
        subscriber: Subscriber = Subscriber.objects.active().get(uuid=uuid_str)
        return subscriber
    except (Subscriber.DoesNotExist, ValueError):
        logger.debug("Subscriber not found for session uuid %s", uuid_str)
        # Purge the stale session key so subsequent requests start clean.
        request.session.pop(_SESSION_KEY, None)
        return None


# ---------------------------------------------------------------------------
# subscribe_partial — inline HTMX form on bulletin pages
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="5/m", block=False)
def subscribe_partial(request: HttpRequest) -> HttpResponse:
    """
    Accept a POST from the inline bulletin-page subscribe CTA.

    Returns the same ``subscribe_success.html`` fragment for all three
    subscriber branches (new / pending / active) so the response is
    byte-identical and does not leak account existence.

    Rate limited to 5 POST requests per minute per IP.  Exceeding the
    limit returns HTTP 429.

    Args:
        request: HTMX POST request containing ``email`` and ``region_id``.

    Returns:
        HTML fragment — always the success card.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    form = SubscribeForm(request.POST)
    if not form.is_valid():
        # Re-render the form with validation errors instead of the success card.
        return render(
            request,
            "subscriptions/partials/subscribe_form.html",
            {"form": form, "region_id": request.POST.get("region_id", "")},
        )

    email: str = form.cleaned_data["email"]

    subscriber, created = Subscriber.objects.get_or_create(
        email__iexact=email,
        defaults={"email": email, "status": Subscriber.Status.PENDING},
    )

    if created:
        # New subscriber — record just created as pending.
        logger.info("New subscriber created for %s (status=pending)", email)
        send_account_access_email(email, request=request)
    elif subscriber.status == Subscriber.Status.PENDING:
        # Existing pending subscriber — resend the access link.
        logger.info("Resending account-access email to pending subscriber %s", email)
        send_account_access_email(email, request=request)
    else:
        # Active subscriber — perform noop to equalise timing; do not send.
        logger.info("Subscribe attempt for active subscriber %s — sending noop", email)
        send_noop_email(email)

    # CRITICAL: render with an empty context so the fragment is byte-identical
    # across all three branches.  Do NOT embed subscriber-specific data here.
    return render(request, "subscriptions/partials/subscribe_success.html", {})


# ---------------------------------------------------------------------------
# account_view — verify account-access token
# ---------------------------------------------------------------------------


@require_GET
def account_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Verify an account-access token and activate the subscriber.

    On success: if the subscriber is pending, flip to active and stamp
    ``confirmed_at`` (idempotent — re-clicking the same link does not
    re-stamp).  Stores the subscriber UUID in the session and renders
    ``account.html``.

    On failure (bad, tampered, or expired token): renders ``link_expired.html``
    with status 400.

    Args:
        request: Incoming GET request.
        token: The signed token from the URL path.

    Returns:
        Rendered account page or link-expired error page.

    """
    max_age = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400)
    email = verify_token(token, salt=SALT_ACCOUNT_ACCESS, max_age=max_age)

    if email is None:
        logger.debug("account_view received an invalid/expired token")
        return render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)

    try:
        subscriber = Subscriber.objects.get(email__iexact=email)
    except Subscriber.DoesNotExist:
        # Token was valid but the subscriber was deleted — treat as expired.
        logger.warning("account_view: valid token for unknown email %s", email)
        return render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)

    if subscriber.status == Subscriber.Status.PENDING:
        subscriber.status = Subscriber.Status.ACTIVE
        subscriber.confirmed_at = timezone.now()
        subscriber.save(update_fields=["status", "confirmed_at", "updated_at"])
        logger.info("Subscriber %s activated via account link", email)

    request.session[_SESSION_KEY] = str(subscriber.uuid)

    all_regions = Region.objects.all()
    current_region_pks = set(
        Region.objects.filter(subscriptions__subscriber=subscriber).values_list(
            "pk", flat=True
        )
    )

    return render(
        request,
        "subscriptions/account.html",
        {
            "subscriber": subscriber,
            "all_regions": all_regions,
            "current_region_pks": current_region_pks,
            "form": RegionSelectionForm(initial={"regions": list(current_region_pks)}),
        },
    )


# ---------------------------------------------------------------------------
# manage_view — unauthenticated email entry OR region management
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def manage_view(request: HttpRequest) -> HttpResponse:
    """
    Manage subscriptions — dual-mode view.

    Without a session (unauthenticated):
      - GET: render an email entry form.
      - POST: rate-limited (3/m per IP); always returns an identical
        "check your inbox" response regardless of whether the email is
        known.  Known email → ``send_account_access_email``; unknown →
        ``send_noop_email``.

    With a valid session (authenticated):
      - GET: render region checkbox form pre-checked with current subscriptions.
      - POST: replace all subscriptions with the submitted selection.
        If the resulting set is empty, hard-delete the Subscriber.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered page or HTTP response.

    """
    subscriber = _get_subscriber_from_session(request)

    # --- Authenticated path ---
    if subscriber is not None:
        return _manage_authenticated(request, subscriber)

    # --- Unauthenticated path ---
    return _manage_unauthenticated(request)


def _manage_unauthenticated(request: HttpRequest) -> HttpResponse:
    """
    Handle manage page for visitors without a valid session.

    GET renders an email entry form.  POST rate-limits at 3/m per IP and
    always returns the same "check your inbox" fragment.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered email-entry page or "check your inbox" page.

    """
    if request.method == "GET":
        return render(
            request,
            "subscriptions/manage.html",
            {"form": EmailForm(), "authenticated": False},
        )

    # POST — rate-limit then send (or noop).
    # We apply ratelimit programmatically here so it only fires on POST.
    usage = get_usage(
        request,
        group="subscriptions.manage.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "subscriptions/manage.html",
            {"form": form, "authenticated": False},
        )

    email: str = form.cleaned_data["email"]

    try:
        Subscriber.objects.get(email__iexact=email)
        send_account_access_email(email, request=request)
        logger.info("Account-access email sent to existing subscriber %s", email)
    except Subscriber.DoesNotExist:
        send_noop_email(email)
        logger.debug("Manage POST for unknown email %s — noop sent", email)

    return render(request, "subscriptions/manage_sent.html", {})


def _manage_authenticated(request: HttpRequest, subscriber: Subscriber) -> HttpResponse:
    """
    Handle manage page for visitors with a valid session.

    GET shows the region checkbox form pre-checked with current subscriptions.
    POST replaces all subscriptions; if the resulting set is empty the
    Subscriber record is hard-deleted (cascades to its Subscription rows).

    Args:
        request: Incoming HTTP request.
        subscriber: The authenticated Subscriber from the session.

    Returns:
        Rendered management form or redirect-equivalent response.

    """
    current_region_pks = set(
        Region.objects.filter(subscriptions__subscriber=subscriber).values_list(
            "pk", flat=True
        )
    )

    if request.method == "GET":
        form = RegionSelectionForm(initial={"regions": list(current_region_pks)})
        all_regions = Region.objects.all()
        return render(
            request,
            "subscriptions/manage.html",
            {
                "subscriber": subscriber,
                "form": form,
                "current_region_pks": current_region_pks,
                "all_regions": all_regions,
                "authenticated": True,
            },
        )

    # POST — replace subscriptions.
    form = RegionSelectionForm(request.POST)
    if not form.is_valid():
        all_regions = Region.objects.all()
        return render(
            request,
            "subscriptions/manage.html",
            {
                "subscriber": subscriber,
                "form": form,
                "current_region_pks": current_region_pks,
                "all_regions": all_regions,
                "authenticated": True,
            },
        )

    selected_regions = set(form.cleaned_data["regions"])

    if not selected_regions:
        # Hard-delete the subscriber — cascades to their subscriptions.
        email = subscriber.email
        subscriber.delete()
        request.session.pop(_SESSION_KEY, None)
        logger.info(
            "Subscriber %s deleted (all subscriptions removed via manage page)", email
        )
        return render(request, "subscriptions/unsubscribe_done.html", {})

    # Replace the subscription set.
    subscriber.subscriptions.exclude(region__in=selected_regions).delete()
    for region in selected_regions:
        Subscription.objects.get_or_create(subscriber=subscriber, region=region)

    logger.info(
        "Subscriber %s updated regions: now %d",
        subscriber.email,
        len(selected_regions),
    )
    return render(
        request,
        "subscriptions/manage_saved.html",
        {"subscriber": subscriber},
    )


# ---------------------------------------------------------------------------
# unsubscribe_view — token-verified one-click unsubscribe
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", block=False)
def unsubscribe_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Confirm and execute a single-region unsubscribe.

    Verifies the unsubscribe token (no expiry — tokens are permanent) to
    extract ``(email, region_id)``.

    GET: render a confirmation page showing which region will be removed.
    POST: delete that region's Subscription; if it was the last one for
          the subscriber, hard-delete the Subscriber row.  Idempotent
          on re-submit (already deleted → renders done page anyway).

    Rate limited to 10 requests per minute per IP.

    Args:
        request: Incoming HTTP request.
        token: The signed unsubscribe token from the URL path.

    Returns:
        Rendered confirmation, done, or error page.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    result = verify_unsubscribe_token(token)
    if result is None:
        logger.debug("unsubscribe_view received an invalid token")
        return render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)

    email, region_id = result

    # Look up the region — 404 if deleted from the pipeline side.
    region = get_object_or_404(Region, region_id=region_id)

    if request.method == "GET":
        return render(
            request,
            "subscriptions/unsubscribe.html",
            {"email": email, "region": region, "token": token},
        )

    # POST — execute unsubscribe.
    try:
        subscriber = Subscriber.objects.get(email__iexact=email)
    except Subscriber.DoesNotExist:
        # Already unsubscribed (perhaps from a different link) — idempotent.
        logger.info(
            "unsubscribe_view: subscriber %s not found — already deleted", email
        )
        return render(request, "subscriptions/unsubscribe_done.html", {})

    # Delete the specific subscription.
    Subscription.objects.filter(subscriber=subscriber, region=region).delete()
    logger.info("Subscriber %s unsubscribed from region %s", email, region_id)

    # If no subscriptions remain, hard-delete the subscriber.
    if not subscriber.subscriptions.exists():
        subscriber.delete()
        logger.info(
            "Subscriber %s hard-deleted (last subscription removed via unsubscribe)",
            email,
        )

    return render(request, "subscriptions/unsubscribe_done.html", {})


# ---------------------------------------------------------------------------
# Unsubscribe token helper (used in bulletin email templates)
# ---------------------------------------------------------------------------


def build_unsubscribe_url(
    email: str, region_id: str, request: HttpRequest | None = None
) -> str:
    """
    Build an absolute unsubscribe URL for the given email and region.

    Convenience helper for use in bulletin email templates and management
    commands that need to embed per-region unsubscribe links.

    Args:
        email: The subscriber's email address.
        region_id: The SLF region identifier.
        request: Optional request used to derive the base URL.

    Returns:
        Absolute URL string.

    """
    token = generate_unsubscribe_token(email, region_id)
    path = f"/subscribe/unsubscribe/{token}/"
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"
