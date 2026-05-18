"""
subscriptions/views.py — HTTP views for the subscriptions application.

Implements the subscription flow built around Django's TimestampSigner:

  sign_in_view        GET/POST — dedicated sign-in page (email entry / passkey).
                               POST: rate-limited (3/m per IP); sends magic link.
  subscribe_partial   POST — inline HTMX subscribe CTA on bulletin pages.
                            Requires a region_id; uses a four-case matrix keyed
                            on (subscriber_created, subscription_created) to
                            decide which email to send and which fragment to
                            return.
  account_view        GET  — verify account-access token; activate subscriber;
                            log in via Django auth; redirect to /subscribe/manage/.
  manage_view         GET  — authenticated "your subscriptions" page.
                            Unauthenticated requests redirect to /sign-in/.
  remove_region       POST — HTMX: remove one subscribed region card.
  delete_account      POST — HTMX: hard-delete subscriber and redirect to done.
  unsubscribe_view    GET/POST — token-verified one-click unsubscribe.

Rate limiting via django-ratelimit (block=False pattern):
  subscribe_partial:  5 requests/min per IP.
  sign_in_view POST:  3 requests/min per IP.
  remove_region POST: 10 requests/min per IP.
  delete_account POST: 3 requests/min per IP.
  unsubscribe_view: 10 requests/min per IP.

Authentication uses Django's standard session auth (request.user).  After
a token is verified in account_view or passkey authentication completes in
views_passkey.py, django.contrib.auth.login() establishes the session.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit

from core.decorators import require_htmx
from regions.models import MicroRegion

from .forms import EmailForm, SubscribeForm
from .models import Subscriber, Subscription
from .services.email import (
    send_account_access_email,
    send_subscription_confirmation_email,
)
from .services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_unsubscribe_token,
    verify_token,
    verify_unsubscribe_token,
)

logger = logging.getLogger(__name__)

# The backend used when calling login() after token/passkey verification.
_TOKEN_BACKEND = "subscriptions.backends.TokenBackend"  # noqa: S105 — backend path, not a password

# Template for the generic link-expired / bad-token error page.
_LINK_EXPIRED_TEMPLATE = "subscriptions/link_expired.html"

# URL name for the manage page — used in redirects.
_MANAGE_URL = "/subscribe/manage/"

# URL for the unsubscribe-done page — used in HX-Redirect headers.
_UNSUBSCRIBE_DONE_URL = "/subscribe/unsubscribe-done/"


def _get_subscriber(request: HttpRequest) -> Subscriber | None:
    """Return the authenticated Subscriber from request.user, or None."""
    if request.user.is_authenticated:
        return request.user
    return None


# ---------------------------------------------------------------------------
# sign_in_view — dedicated sign-in page
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def sign_in_view(request: HttpRequest) -> HttpResponse:
    """
    Dedicated sign-in page for returning subscribers.

    GET: render the email entry form with passkey conditional UI.
    If the user is already authenticated, redirect to the manage page.

    POST: rate-limited (3/m per IP); always returns the same "check your
    inbox" response regardless of whether the email is known.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered sign-in page or redirect.

    """
    if request.user.is_authenticated:
        return redirect("subscriptions:manage")

    if request.method == "GET":
        return render(request, "subscriptions/sign_in.html", {"form": EmailForm()})

    # POST — rate-limit then send (or noop).
    usage = get_usage(
        request,
        group="subscriptions.sign_in.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(request, "subscriptions/sign_in.html", {"form": form})

    email: str = form.cleaned_data["email"]
    Subscriber.objects.get_or_create(
        email=email,
        defaults={"status": Subscriber.Status.PENDING},
    )
    send_account_access_email(email, request=request)
    logger.info("Account-access email sent to %s via sign-in page", email)

    return render(request, "subscriptions/manage_sent.html", {})


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

    If ``region_id`` does not resolve to a known MicroRegion, returns a 400 error
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
    try:
        region = MicroRegion.objects.get(region_id=region_id)
    except MicroRegion.DoesNotExist:
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
        email=email,
        defaults={"status": Subscriber.Status.PENDING},
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
    Verify an account-access token, activate the subscriber, and log them in.

    On success: if the subscriber is pending, flip to active and stamp
    ``confirmed_at`` (idempotent — re-clicking the same link does not
    re-stamp).  Calls ``django.contrib.auth.login()`` to establish the
    Django session and redirects to ``/subscribe/manage/?just_confirmed=1``.

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
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
    else:
        try:
            subscriber = Subscriber.objects.get(email__iexact=email)
        except Subscriber.DoesNotExist:
            logger.warning("account_view: valid token for unknown email %s", email)
            response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        else:
            if subscriber.status == Subscriber.Status.PENDING:
                subscriber.status = Subscriber.Status.ACTIVE
                subscriber.confirmed_at = timezone.now()
                subscriber.save(update_fields=["status", "confirmed_at", "updated_at"])
                logger.info("Subscriber %s activated via account link", email)
            login(request, subscriber, backend=_TOKEN_BACKEND)
            response = redirect(f"{_MANAGE_URL}?just_confirmed=1")

    # Tokens appear in this view's URL path — suppress Referer leakage.
    response["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# manage_view — authenticated subscriptions dashboard
# ---------------------------------------------------------------------------


@require_GET
def manage_view(request: HttpRequest) -> HttpResponse:
    """
    Show the subscriptions dashboard for the authenticated subscriber.

    Unauthenticated visitors are redirected to the sign-in page.

    GET: render the subscriptions dashboard (one card per subscribed
    region, with resort list and per-region remove button).

    Context keys:
        subscriber       — authenticated Subscriber instance.
        subscriptions    — queryset of Subscription rows for the subscriber.
        just_confirmed   — True when arriving via the confirmation link.
        today            — today's date (datetime.date) for the bulletin link label.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered page or redirect to sign-in.

    """
    subscriber = _get_subscriber(request)

    if subscriber is None:
        return redirect("subscriptions:sign_in")

    just_confirmed = request.GET.get("just_confirmed") == "1"

    subscriptions = (
        Subscription.objects.filter(subscriber=subscriber)
        .select_related("region", "region__subregion__major")
        .prefetch_related("region__resorts")
        .order_by("region__name")
    )

    return render(
        request,
        "subscriptions/manage.html",
        {
            "subscriber": subscriber,
            "subscriptions": subscriptions,
            "just_confirmed": just_confirmed,
            "today": timezone.now().date(),
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
    Remove a single subscribed region for the authenticated subscriber.

    Deletes the ``(subscriber, region)`` Subscription row.  If this was the
    subscriber's last region, hard-deletes the subscriber row too (CASCADE
    handles the Subscription rows) and responds with an ``HX-Redirect``
    header pointing to the unsubscribe-done page.

    Guarded by authentication (no session → 403), ``@require_POST``,
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

    subscriber = _get_subscriber(request)
    if subscriber is None:
        return HttpResponse(status=403)

    region = get_object_or_404(MicroRegion, region_id=region_id)
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
        logout(request)
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
    Hard-delete the authenticated subscriber and all their subscriptions.

    Calls ``django.contrib.auth.logout()`` to clear the Django session and
    responds with an ``HX-Redirect`` header pointing to the unsubscribe-done
    page.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 3 requests/min per IP.

    Args:
        request: HTMX POST request.

    Returns:
        200 with HX-Redirect header, 403 when unauthenticated, or 429 when
        rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    subscriber = _get_subscriber(request)
    if subscriber is None:
        return HttpResponse(status=403)

    email = subscriber.email
    subscriber.delete()
    logout(request)
    logger.info("Subscriber %s hard-deleted via delete_account", email)

    response = HttpResponse(status=200)
    response["HX-Redirect"] = _UNSUBSCRIBE_DONE_URL
    return response


# ---------------------------------------------------------------------------
# sign_out — log out the subscriber
# ---------------------------------------------------------------------------


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    """
    Log out the subscriber and redirect to the sign-in page.

    Args:
        request: POST request (CSRF-protected via the standard Django form token).

    Returns:
        Redirect to the sign-in page.

    """
    logout(request)
    return redirect("subscriptions:sign_in")


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
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        response["Referrer-Policy"] = "no-referrer"
        return response

    email, region_id = result

    # Look up the region — 404 if deleted from the pipeline side.
    region = get_object_or_404(MicroRegion, region_id=region_id)

    if request.method == "GET":
        response = render(
            request,
            "subscriptions/unsubscribe.html",
            {"email": email, "region": region, "token": token},
        )
        response["Referrer-Policy"] = "no-referrer"
        return response

    # POST — execute unsubscribe.
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        # Already unsubscribed (perhaps from a different link) — idempotent.
        logger.info(
            "unsubscribe_view: subscriber %s not found — already deleted", email
        )
        response = render(request, "subscriptions/unsubscribe_done.html", {})
        response["Referrer-Policy"] = "no-referrer"
        return response

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

    response = render(request, "subscriptions/unsubscribe_done.html", {})
    response["Referrer-Policy"] = "no-referrer"
    return response


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
