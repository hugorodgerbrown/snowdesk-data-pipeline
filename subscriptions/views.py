"""
subscriptions/views.py — HTTP views for the subscriptions application.

Implements the subscription flow built around Django's TimestampSigner:

  subscribe_partial   POST — inline HTMX subscribe CTA on bulletin pages.
                            Requires a region_id; uses a four-case matrix keyed
                            on (subscriber_created, subscription_created) to
                            decide which email to send and which fragment to
                            return.
  account_view        GET  — verify account-access token; activate subscriber;
                            redirect to /subscribe/manage/?just_confirmed=1.
  manage_view         GET/POST — unauthenticated email entry OR
                               authenticated "your subscriptions" page.
  remove_region       POST — HTMX: remove one subscribed region card.
  delete_account      POST — HTMX: hard-delete subscriber and redirect to done.
  unsubscribe_view    GET/POST — token-verified one-click unsubscribe.

Rate limiting via django-ratelimit (block=False pattern):
  subscribe_partial:  5 requests/min per IP.
  manage_view POST (unauthenticated): 3 requests/min per IP.
  remove_region POST: 10 requests/min per IP.
  delete_account POST: 3 requests/min per IP.
  unsubscribe_view: 10 requests/min per IP.

Session key ``subscriber_uuid`` carries the authenticated subscriber's UUID
across the manage page steps.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit

from pipeline.decorators import require_htmx
from pipeline.models import Region

from .forms import EmailForm, SubscribeForm
from .models import Subscriber, Subscription
from .services.email import (
    send_account_access_email,
    send_subscription_confirmation_email,
    simulate_account_access_work,
)
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

# URL name for the manage page — used in redirects.
_MANAGE_URL = "/subscribe/manage/"

# URL for the unsubscribe-done page — used in HX-Redirect headers.
_UNSUBSCRIBE_DONE_URL = "/subscribe/unsubscribe-done/"


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

    Requires ``region_id`` in the POST data.  Uses a four-case matrix keyed
    on ``(subscriber_created, subscription_created)`` to decide which email
    to send and which success fragment to return:

    A. New subscriber (subscriber_created=True):
       Send account-access email → "Check your inbox" fragment.

    B. Existing pending subscriber (subscriber_created=False, status=PENDING):
       Resend account-access email → "Check your inbox" fragment.

    C. Existing active subscriber + new region (status=ACTIVE, sub_created=True):
       Send subscription confirmation email → "Added {region} to your alerts" fragment.

    D. Existing active subscriber + already subscribed (status=ACTIVE,
       sub_created=False):
       No email → "You're already subscribed to {region}" fragment.

    If ``region_id`` does not resolve to a known Region, returns a 400 error
    fragment — this path should not occur in normal use.

    Rate limited to 5 POST requests per minute per IP.  Exceeding the
    limit returns HTTP 429.

    Args:
        request: HTMX POST request containing ``email`` and ``region_id``.

    Returns:
        HTML fragment representing the outcome of the subscribe attempt.

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
    region_id: str = form.cleaned_data["region_id"]

    # Resolve the region — return a 400 error fragment if not found.
    # This should not occur in normal use (region_id is set by the template),
    # but is a required defensive path.
    try:
        region = Region.objects.get(region_id=region_id)
    except Region.DoesNotExist:
        logger.warning(
            "subscribe_partial: region_id %s not found in DB",
            region_id,
        )
        return render(
            request,
            "subscriptions/partials/subscribe_error.html",
            {},
            status=400,
        )

    subscriber, subscriber_created = Subscriber.objects.get_or_create(
        email__iexact=email,
        defaults={"email": email, "status": Subscriber.Status.PENDING},
    )

    # Persist the region subscription idempotently; capture whether it's new.
    _subscription, subscription_created = Subscription.objects.get_or_create(
        subscriber=subscriber, region=region
    )

    if subscriber_created:
        # Case A — new subscriber.
        logger.info("New subscriber created for %s (status=pending)", email)
        send_account_access_email(email, request=request)
        return render(
            request,
            "subscriptions/partials/subscribe_success_access.html",
            {},
        )

    if subscriber.status == Subscriber.Status.PENDING:
        # Case B — existing pending subscriber; resend the access link.
        logger.info("Resending account-access email to pending subscriber %s", email)
        send_account_access_email(email, request=request)
        return render(
            request,
            "subscriptions/partials/subscribe_success_access.html",
            {},
        )

    if subscription_created:
        # Case C — active subscriber, new region added.
        logger.info("Active subscriber %s added new region %s", email, region.region_id)
        send_subscription_confirmation_email(email, region=region, request=request)
        return render(
            request,
            "subscriptions/partials/subscribe_success_added.html",
            {"region_name": region.name},
        )

    # Case D — active subscriber, already subscribed to this region.
    logger.info(
        "Active subscriber %s already subscribed to region %s — no-op",
        email,
        region.region_id,
    )
    return render(
        request,
        "subscriptions/partials/subscribe_success_already.html",
        {"region_name": region.name},
    )


# ---------------------------------------------------------------------------
# account_view — verify account-access token
# ---------------------------------------------------------------------------


@require_GET
def account_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Verify an account-access token and activate the subscriber.

    On success: if the subscriber is pending, flip to active and stamp
    ``confirmed_at`` (idempotent — re-clicking the same link does not
    re-stamp).  Stores the subscriber UUID in the session and redirects
    to ``/subscribe/manage/?just_confirmed=1``.

    On failure (bad, tampered, or expired token): renders ``link_expired.html``
    with status 400.

    Args:
        request: Incoming GET request.
        token: The signed token from the URL path.

    Returns:
        302 redirect to manage page, or link-expired error page.

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

    return redirect(f"{_MANAGE_URL}?just_confirmed=1")


# ---------------------------------------------------------------------------
# manage_view — unauthenticated email entry OR subscriptions dashboard
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
      - GET: render the subscriptions dashboard (one card per subscribed
        region, with resort list and per-region remove button).
      - POST: not used — region management is now done via HTMX partials.

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
        # Unknown email — do not reveal account existence.  Perform the same
        # token-gen + template-render CPU work as the real send path so the
        # response timing profile does not leak whether the email is known.
        simulate_account_access_work(email)
        logger.debug("Manage POST for unknown email %s — no account found", email)

    return render(request, "subscriptions/manage_sent.html", {})


def _manage_authenticated(request: HttpRequest, subscriber: Subscriber) -> HttpResponse:
    """
    Handle manage page for visitors with a valid session.

    GET shows the subscriptions dashboard: one card per subscribed region,
    ordered by region name, with a resort list and per-region remove button.

    Args:
        request: Incoming HTTP request.
        subscriber: The authenticated Subscriber from the session.

    Returns:
        Rendered subscriptions dashboard.

    """
    just_confirmed = request.GET.get("just_confirmed") == "1"

    subscriptions = (
        Subscription.objects.filter(subscriber=subscriber)
        .select_related("region")
        .prefetch_related("region__resorts")
        .order_by("region__name")
    )

    return render(
        request,
        "subscriptions/manage.html",
        {
            "subscriber": subscriber,
            "subscriptions": subscriptions,
            "authenticated": True,
            "just_confirmed": just_confirmed,
        },
    )


# ---------------------------------------------------------------------------
# remove_region — HTMX: remove one subscribed region
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="10/m", block=False)
def remove_region(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Remove a single subscribed region for the session-authenticated subscriber.

    Deletes the ``(subscriber, region)`` Subscription row.  If this was the
    subscriber's last region, hard-deletes the subscriber row too (CASCADE
    handles the Subscription rows) and responds with an ``HX-Redirect``
    header pointing to the unsubscribe-done page.

    Guarded by session authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 10 requests/min per IP.

    Args:
        request: HTMX POST request.
        region_id: The SLF region identifier to remove.

    Returns:
        Empty 200 (card removed via outerHTML swap), HX-Redirect on last
        region, 403 when unauthenticated, or 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return HttpResponse(status=403)

    region = get_object_or_404(Region, region_id=region_id)
    Subscription.objects.filter(subscriber=subscriber, region=region).delete()
    logger.info(
        "Subscriber %s removed region %s via manage page",
        subscriber.email,
        region_id,
    )

    # If no subscriptions remain, hard-delete the subscriber.
    if not subscriber.subscriptions.exists():
        email = subscriber.email
        subscriber.delete()
        request.session.pop(_SESSION_KEY, None)
        logger.info(
            "Subscriber %s hard-deleted (last region removed via manage page)", email
        )
        response = HttpResponse(status=200)
        response["HX-Redirect"] = _UNSUBSCRIBE_DONE_URL
        return response

    # Return empty content — hx-swap="outerHTML" on the card will remove it.
    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# delete_account — HTMX: hard-delete subscriber
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="3/m", block=False)
def delete_account(request: HttpRequest) -> HttpResponse:
    """
    Hard-delete the session-authenticated subscriber and all their subscriptions.

    Clears the session and responds with an ``HX-Redirect`` header pointing
    to the unsubscribe-done page.

    Guarded by session authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 3 requests/min per IP.

    Args:
        request: HTMX POST request.

    Returns:
        200 with HX-Redirect header, 403 when unauthenticated, or 429 when
        rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return HttpResponse(status=403)

    email = subscriber.email
    subscriber.delete()
    request.session.pop(_SESSION_KEY, None)
    logger.info("Subscriber %s hard-deleted via delete_account", email)

    response = HttpResponse(status=200)
    response["HX-Redirect"] = _UNSUBSCRIBE_DONE_URL
    return response


# ---------------------------------------------------------------------------
# unsubscribe_done — standalone page for post-unsubscribe landing
# ---------------------------------------------------------------------------


@require_GET
def unsubscribe_done_view(request: HttpRequest) -> HttpResponse:
    """
    Render the "you've been unsubscribed" confirmation page.

    This view exists so that HTMX HX-Redirect from remove_region and
    delete_account can point to a stable GET URL rather than relying on
    the unsubscribe flow's POST-only done path.

    Args:
        request: Incoming GET request.

    Returns:
        Rendered unsubscribe-done page.

    """
    return render(request, "subscriptions/unsubscribe_done.html", {})


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
