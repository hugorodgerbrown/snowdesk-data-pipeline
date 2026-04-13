"""
subscriptions/views.py — HTTP views for the subscriptions application.

Implements the magic-link subscription flow:
  1. enter_email   — subscriber enters their email address
  2. email_sent    — confirmation that the link was sent
  3. verify_token  — validates the JWT from the magic-link URL
  4. pick_regions  — new subscriber chooses regions (first login)
  5. manage_regions — returning subscriber updates their regions
  6. confirmed     — success page shown after saving subscriptions
  7. region_search_partial — HTMX fragment: live-search region checkboxes

Session key ``subscriber_uuid`` carries the authenticated subscriber's UUID
across the region selection/management steps.
"""

import logging

from django.conf import settings
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from pipeline.models import Region
from pipeline.views import require_htmx

from .forms import EmailForm, RegionSelectionForm
from .models import Subscriber, Subscription
from .services.email import send_magic_link_email
from .services.token import validate_magic_link_token

logger = logging.getLogger(__name__)

# Session key that stores the authenticated subscriber's UUID string.
_SESSION_KEY = "subscriber_uuid"

# Template name and context for the link-expired error page.
_LINK_EXPIRED_TEMPLATE = "subscriptions/link_expired.html"
_LINK_EXPIRED_CTX = {
    "expiry_minutes": settings.MAGIC_LINK_EXPIRY_SECONDS // 60,
}


def _get_subscriber_from_session(request: HttpRequest) -> Subscriber | None:
    """
    Look up an active Subscriber from the session, or return None.

    Reads ``session['subscriber_uuid']``, attempts to fetch the matching
    active Subscriber, and returns it. Returns None when the key is absent,
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
        return None


@require_http_methods(["GET", "POST"])
def enter_email(request: HttpRequest) -> HttpResponse:
    """
    Render the email entry form and send a magic link on POST.

    GET: Display an empty EmailForm.
    POST: Validate the form; on success send a magic-link email and
    redirect to ``email_sent``. On failure re-render the form with errors.

    Args:
        request: The incoming HTTP request.

    Returns:
        Rendered form page, or redirect to email_sent on success.

    """
    if request.method == "POST":
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            logger.info("Sending magic-link email to %s", email)
            send_magic_link_email(email=email, purpose="login", request=request)
            return redirect("subscriptions:email_sent")
    else:
        form = EmailForm()

    return render(request, "subscriptions/enter_email.html", {"form": form})


@require_GET
def email_sent(request: HttpRequest) -> HttpResponse:
    """
    Render the 'check your inbox' confirmation page.

    Args:
        request: The incoming HTTP GET request.

    Returns:
        Rendered confirmation page.

    """
    return render(request, "subscriptions/email_sent.html")


@require_GET
def verify_token(request: HttpRequest) -> HttpResponse:
    """
    Validate the magic-link token from the query string.

    Reads ``?token=`` from the URL, decodes the JWT, and authenticates
    the subscriber:
      - Creates a new Subscriber if one does not exist for the email.
      - Stores ``subscriber_uuid`` in the session.
      - Updates ``last_authenticated_at``.
      - Redirects to ``pick_regions`` for first-time subscribers (no
        existing subscriptions), or ``manage_regions`` for returning ones.

    On failure (missing, expired, or malformed token) renders
    ``link_expired.html``.

    Args:
        request: The incoming HTTP GET request.

    Returns:
        Redirect to region selection, or rendered link_expired page.

    """
    token = request.GET.get("token", "")
    if not token:
        logger.debug("verify_token called without a token")
        return render(request, _LINK_EXPIRED_TEMPLATE, _LINK_EXPIRED_CTX, status=400)

    payload = validate_magic_link_token(token)
    if payload is None:
        logger.debug("verify_token received an invalid/expired token")
        return render(request, _LINK_EXPIRED_TEMPLATE, _LINK_EXPIRED_CTX, status=400)

    email: str | None = payload.get("email")
    if not email:
        logger.warning("Magic-link token missing 'email' claim")
        return render(request, _LINK_EXPIRED_TEMPLATE, _LINK_EXPIRED_CTX, status=400)
    subscriber, created = Subscriber.objects.get_or_create(
        email=email,
        defaults={"is_active": True},
    )
    if not created and not subscriber.is_active:
        subscriber.is_active = True

    subscriber.last_authenticated_at = timezone.now()
    subscriber.save(update_fields=["last_authenticated_at", "is_active"])

    request.session[_SESSION_KEY] = str(subscriber.uuid)
    logger.info("Subscriber %s authenticated via magic link (new=%s)", email, created)

    has_subscriptions = subscriber.subscriptions.exists()
    if has_subscriptions:
        return redirect("subscriptions:manage_regions")
    return redirect("subscriptions:pick_regions")


@require_http_methods(["GET", "POST"])
def pick_regions(request: HttpRequest) -> HttpResponse:
    """
    Let a new subscriber choose which regions to follow.

    Requires an active session. GET shows an empty RegionSelectionForm.
    POST saves the chosen regions as Subscription records and redirects
    to ``confirmed``.

    Redirects unauthenticated visitors to ``enter_email``.

    Args:
        request: The incoming HTTP request.

    Returns:
        Rendered region selection form, or redirect.

    """
    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return redirect("subscriptions:enter_email")

    if request.method == "POST":
        form = RegionSelectionForm(request.POST)
        if form.is_valid():
            selected_regions = form.cleaned_data["regions"]
            for region in selected_regions:
                Subscription.objects.get_or_create(subscriber=subscriber, region=region)
            logger.info(
                "Subscriber %s picked %d regions",
                subscriber.email,
                len(selected_regions),
            )
            return redirect("subscriptions:confirmed")
    else:
        form = RegionSelectionForm()

    all_regions = Region.objects.all()
    return render(
        request,
        "subscriptions/pick_regions.html",
        {"form": form, "regions": all_regions},
    )


@require_http_methods(["GET", "POST"])
def manage_regions(request: HttpRequest) -> HttpResponse:
    """
    Let a returning subscriber update their region subscriptions.

    Requires an active session. GET shows the current subscriptions
    pre-selected in the form. POST replaces all existing subscriptions
    with the new selection and redirects to ``confirmed``.

    Redirects unauthenticated visitors to ``enter_email``.

    Args:
        request: The incoming HTTP request.

    Returns:
        Rendered management form, or redirect.

    """
    subscriber = _get_subscriber_from_session(request)
    if subscriber is None:
        return redirect("subscriptions:enter_email")

    current_regions = Region.objects.filter(subscriptions__subscriber=subscriber)

    if request.method == "POST":
        form = RegionSelectionForm(request.POST)
        if form.is_valid():
            selected_regions = set(form.cleaned_data["regions"])

            # Remove deselected subscriptions.
            subscriber.subscriptions.exclude(region__in=selected_regions).delete()

            # Add newly selected subscriptions.
            for region in selected_regions:
                Subscription.objects.get_or_create(subscriber=subscriber, region=region)

            logger.info(
                "Subscriber %s updated regions: now %d",
                subscriber.email,
                len(selected_regions),
            )
            return redirect("subscriptions:confirmed")
    else:
        form = RegionSelectionForm(initial={"regions": current_regions})

    all_regions = Region.objects.all()
    return render(
        request,
        "subscriptions/manage.html",
        {
            "form": form,
            "current_regions": current_regions,
            "regions": all_regions,
        },
    )


@require_GET
def confirmed(request: HttpRequest) -> HttpResponse:
    """
    Render the subscription confirmation success page.

    Args:
        request: The incoming HTTP GET request.

    Returns:
        Rendered success page.

    """
    return render(request, "subscriptions/confirmed.html")


@require_GET
@require_htmx
def region_search_partial(request: HttpRequest) -> HttpResponse:
    """
    Return an HTMX fragment containing matching region checkboxes.

    Reads the ``?q=`` query parameter and returns a partial template
    listing regions whose region_id or name contains the search term
    (case-insensitive). Only responds to HTMX requests.

    Args:
        request: The incoming HTTP GET request (must be HTMX).

    Returns:
        Rendered checkbox list fragment.

    """
    query = request.GET.get("q", "").strip()
    if query:
        regions = Region.objects.filter(
            models.Q(region_id__icontains=query) | models.Q(name__icontains=query)
        ).order_by("region_id")
    else:
        regions = Region.objects.order_by("region_id")

    # Pass currently selected PKs so the partial can pre-check them.
    selected_pks_raw = request.GET.getlist("selected")
    try:
        selected_pks = {int(pk) for pk in selected_pks_raw if pk}
    except ValueError:
        selected_pks = set()

    return render(
        request,
        "subscriptions/partials/region_search_results.html",
        {"regions": regions, "selected_pks": selected_pks},
    )
