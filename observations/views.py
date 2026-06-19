"""
observations/views.py — HTMX endpoints for the field-report feature.

Provides two HTMX-only fragment views used by the floating Report button on
the map page (SNOW-324):

- ``report_form`` (GET)   — reads GPS fix from query params, resolves the
  point to a MicroRegion, returns the observation-type toggle form.
- ``report_submit`` (POST) — validates the form, creates a FieldObservation
  row, returns the thank-you confirmation fragment.

Both endpoints are:
  - flag-gated on ``field_observations`` (404 when inactive);
  - subscriber-gated (403 for anonymous / non-subscriber users);
  - ``@require_htmx`` (400 for non-HTMX requests).

``report_submit`` additionally applies django-ratelimit (5/m per IP, block=False)
and returns 429 when the limit is exceeded.
"""

from __future__ import annotations

import logging

import waffle
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from core.decorators import require_htmx
from observations.models import FieldObservation
from regions.services.point_match import region_for_point
from subscriptions.models import Subscriber

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_field_observations_flag(request: HttpRequest) -> None:
    """Raise Http404 unless the ``field_observations`` waffle flag is active.

    Mirrors the pattern used by ``public.api._require_edit_map_flag``.
    Flag is seeded with ``superusers=True`` by
    ``observations/migrations/0002_seed_field_observations_flag.py``;
    extend / disable via ``/admin/waffle/flag/field_observations/``.

    Args:
        request: The current HTTP request.

    Raises:
        Http404: When the flag is inactive for the current request.

    """
    from django.http import Http404  # noqa: PLC0415

    if not waffle.flag_is_active(request, "field_observations"):
        raise Http404("field_observations flag is inactive for this request.")


def _get_subscriber(request: HttpRequest) -> Subscriber | None:
    """Return the authenticated Subscriber profile from request.user, or None.

    Returns None for anonymous users and for authenticated staff users who
    have no Subscriber profile (e.g. superusers created via createsuperuser).

    Args:
        request: The current HTTP request.

    Returns:
        The Subscriber instance, or None.

    """
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.subscriber  # type: ignore[union-attr]
    except Subscriber.DoesNotExist:
        return None


def _parse_gps(lat_str: str | None, lon_str: str | None) -> tuple[float, float] | None:
    """Parse latitude and longitude strings into floats.

    Returns None when either value is missing or unparseable.

    Args:
        lat_str: Raw latitude string from the request (query param or POST body).
        lon_str: Raw longitude string from the request.

    Returns:
        ``(lat, lon)`` float pair, or None on failure.

    """
    if not lat_str or not lon_str:
        return None
    try:
        return float(lat_str), float(lon_str)
    except ValueError, TypeError:
        return None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@require_htmx
def report_form(request: HttpRequest) -> HttpResponse:
    """Return the observation-type toggle form for a GPS-gated report.

    Reads ``lat``, ``lon``, and ``accuracy`` query parameters (set by
    ``report.js`` after a successful geolocation call).  Returns 400 if
    either ``lat`` or ``lon`` is missing or unparseable — this is the GPS
    gate: the form must never appear without a valid fix.

    Resolves the GPS point to a MicroRegion (best-effort) via
    ``region_for_point`` and passes it to the template so the banner can
    show "We think you're in {region}" (or a fallback message when
    resolution fails).

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_report_form.html`` partial, or an error response.

    """
    _require_field_observations_flag(request)

    subscriber = _get_subscriber(request)
    if subscriber is None:
        return HttpResponse("Authentication required.", status=403)

    coords = _parse_gps(request.GET.get("lat"), request.GET.get("lon"))
    if coords is None:
        return HttpResponse(
            "Valid lat and lon query parameters are required (GPS gate).",
            status=400,
        )

    lat, lon = coords
    accuracy_m = request.GET.get("accuracy")

    region = region_for_point(lon, lat)

    return render(
        request,
        "observations/partials/_report_form.html",
        {
            "region": region,
            "lat": lat,
            "lon": lon,
            "accuracy_m": accuracy_m,
            "observation_types": FieldObservation.OBSERVATION_TYPE.choices,
            "submit_url": reverse("observations:report_submit"),
        },
    )


@require_htmx
@require_POST
@ratelimit(key="ip", rate="5/m", block=False)
def report_submit(request: HttpRequest) -> HttpResponse:
    """Create a FieldObservation row and return the confirmation fragment.

    Validates the POST body:
    - ``lat`` / ``lon`` must be present and parseable (GPS gate; 400 otherwise).
    - ``observation_types`` must be a subset of ``OBSERVATION_TYPE.values``
      (silently drops unknown values — no hard error, to tolerate stale JS).
    - Rate-limited to 5 submissions per minute per IP (429 on excess).

    On success, creates the row and returns ``_report_confirmation.html``.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered confirmation partial, or an error response.

    """
    _require_field_observations_flag(request)

    subscriber = _get_subscriber(request)
    if subscriber is None:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before submitting again.", status=429
        )

    coords = _parse_gps(request.POST.get("lat"), request.POST.get("lon"))
    if coords is None:
        return HttpResponse(
            "Valid lat and lon are required (GPS gate).",
            status=400,
        )

    lat, lon = coords

    # Parse optional accuracy (metres → kilometres).
    accuracy_radius_km: float | None = None
    accuracy_m_str = request.POST.get("accuracy_m")
    if accuracy_m_str:
        try:
            accuracy_radius_km = float(accuracy_m_str) / 1000.0
        except ValueError, TypeError:
            accuracy_radius_km = None

    # Validate and filter observation types.
    valid_values = set(FieldObservation.OBSERVATION_TYPE.values)
    raw_types = request.POST.getlist("observation_types")
    observation_types = [t for t in raw_types if t in valid_values]

    # Best-effort region resolution.
    region = region_for_point(lon, lat)

    FieldObservation.objects.create(
        subscriber=subscriber,
        region=region,
        latitude=lat,
        longitude=lon,
        accuracy_radius_km=accuracy_radius_km,
        observation_types=observation_types,
    )

    logger.info(
        "FieldObservation created: subscriber=%s region=%s types=%s",
        subscriber.pk,
        region.region_id if region else None,
        observation_types,
    )

    return render(
        request,
        "observations/partials/_report_confirmation.html",
        {"region": region},
    )
