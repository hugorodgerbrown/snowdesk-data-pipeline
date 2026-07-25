"""
observations/views.py — HTMX endpoints for the field-report feature.

Provides two HTMX-only fragment views used by the floating Report button on
the map page (SNOW-324):

- ``report_form`` (GET)   — reads optional GPS fix and location_source from
  query params, resolves the point to a MicroRegion when coords are present,
  returns the one-tap problem-selection form.  No coords are required: when
  absent the form renders in "choose on map" state (MANUAL path).
- ``report_submit`` (POST) — validates lat/lon, location_source, and
  observation_type; creates a FieldObservation row; returns the thank-you
  confirmation fragment.  Also accepts an optional client-supplied
  ``observed_at`` (SNOW-420) — the tap-time instant, stamped client-side by
  ``report.js`` before the request is handed to the offline mutation queue,
  so a report submitted while offline records when the user actually
  observed the problem rather than whenever the queued mutation replays.
  Validated for shape and plausibility (see ``_parse_observed_at``); falls
  back to the model's ``timezone.now`` default when absent.

Both endpoints are:
  - authentication-gated (403 for anonymous users);
  - verification-gated (403 unless the user has a verified ``Account``,
    SNOW-430);
  - ``@require_htmx`` (400 for non-HTMX requests).

``report_submit`` additionally applies django-ratelimit (5/m per IP, block=False)
and returns 429 when the limit is exceeded.
"""

from __future__ import annotations

import datetime
import logging
from typing import cast

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.models import user_is_verified
from core.coordinates import validate_accuracy_radius_km, validate_coordinates
from core.decorators import require_htmx
from observations.models import FieldObservation
from regions.services.point_match import region_for_point

logger = logging.getLogger(__name__)

# Sentinel distinguishing "observed_at was supplied but invalid" from
# "observed_at was absent" — the latter falls back to the model's
# timezone.now default; the former is a 400.
_INVALID_OBSERVED_AT = object()

# Plausibility window for a client-supplied observed_at (SNOW-420). Generous
# enough to absorb clock skew and a genuinely offline device replaying a
# queued mutation well after the tap, tight enough to reject an obviously
# wrong client clock.
_OBSERVED_AT_FUTURE_TOLERANCE = datetime.timedelta(minutes=5)
_OBSERVED_AT_MAX_AGE = datetime.timedelta(days=30)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auth_gate(request: HttpRequest) -> HttpResponse | None:
    """Return a 403 response when the user may not submit field reports.

    Field reports require an authenticated user (403 for anonymous) whose
    email has been verified (403 for an unverified / account-less user,
    SNOW-430).  The verification check is the shared
    ``accounts.models.user_is_verified`` — the same one that drives the
    client-side ``report_eligible`` flag in ``public/views.py`` — so the gate
    and the flag cannot drift (SNOW-477).  Returns ``None`` when the user
    passes both checks.

    Args:
        request: The current HTTP request.

    Returns:
        An ``HttpResponse`` (403) to short-circuit the view, or ``None``.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)
    if not user_is_verified(request.user):
        return HttpResponse("Email verification required.", status=403)
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
        lat, lon = float(lat_str), float(lon_str)
        # Rejects NaN/±Inf and out-of-range values (InvalidCoordinatesError is
        # a ValueError, so the except below catches it). SNOW-464.
        validate_coordinates(lat, lon)
    except ValueError, TypeError:
        return None
    return lat, lon


def _parse_observed_at(raw: str | None) -> datetime.datetime | None | object:
    """Parse the client-supplied ``observed_at`` field (SNOW-420).

    ``report.js`` stamps this with ``new Date().toISOString()`` at tap time,
    before handing the request to ``window.pwaMutationQueue`` — so it may
    reach the server well after the fact, on replay after a reconnect.

    Args:
        raw: The raw ``observed_at`` POST value, or None/empty when absent.

    Returns:
        None when ``raw`` is absent — the caller falls back to the model's
        ``timezone.now`` default. The parsed, timezone-aware ``datetime``
        when ``raw`` is present, well-formed, and within the accepted
        plausibility window. ``_INVALID_OBSERVED_AT`` when ``raw`` is
        present but unparseable, or outside that window (more than
        ``_OBSERVED_AT_FUTURE_TOLERANCE`` in the future, or older than
        ``_OBSERVED_AT_MAX_AGE``).

    """
    if not raw:
        return None

    parsed = parse_datetime(raw)
    if parsed is None:
        return _INVALID_OBSERVED_AT

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)

    now = timezone.now()
    if parsed > now + _OBSERVED_AT_FUTURE_TOLERANCE:
        return _INVALID_OBSERVED_AT
    if parsed < now - _OBSERVED_AT_MAX_AGE:
        return _INVALID_OBSERVED_AT

    return parsed


def _parse_accuracy_radius_km(accuracy_m_str: str | None) -> float | None:
    """Convert an optional ``accuracy_m`` POST value (metres) to kilometres.

    Args:
        accuracy_m_str: Raw ``accuracy_m`` string from the request, or None.

    Returns:
        The value divided by 1000, or None when absent or unparseable.

    """
    if not accuracy_m_str:
        return None
    try:
        accuracy_km = float(accuracy_m_str) / 1000.0
        # Rejects NaN/±Inf and negative accuracy (SNOW-464).
        validate_accuracy_radius_km(accuracy_km)
    except ValueError, TypeError:
        return None
    return accuracy_km


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@require_htmx
def report_form(request: HttpRequest) -> HttpResponse:
    """Return the one-tap problem-selection form for a field report.

    Reads ``lat``, ``lon``, ``accuracy``, ``location_source``, ``gps_lat``,
    and ``gps_lon`` query parameters (set by ``report.js``).  All are optional:
    when ``lat``/``lon`` are absent the form renders in MANUAL "choose on map"
    state — the GPS gate has been removed so users who denied location
    can still report.

    When coords are present, resolves the point to a MicroRegion (best-effort)
    via ``region_for_point`` and passes it to the template for the region hint.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_report_form.html`` partial.

    """
    gate = _auth_gate(request)
    if gate is not None:
        return gate

    coords = _parse_gps(request.GET.get("lat"), request.GET.get("lon"))
    has_coords = coords is not None

    lat: float | None = None
    lon: float | None = None
    region = None

    if coords is not None:
        lat, lon = coords
        region = region_for_point(lat, lon)

    accuracy_m = request.GET.get("accuracy")
    location_source = request.GET.get(
        "location_source", FieldObservation.LOCATION_SOURCE.GPS
    )

    # Parse raw GPS fix coords (present on GPS and GPS_REFINED paths).
    gps_coords = _parse_gps(request.GET.get("gps_lat"), request.GET.get("gps_lon"))
    gps_lat: float | None = gps_coords[0] if gps_coords is not None else None
    gps_lon: float | None = gps_coords[1] if gps_coords is not None else None

    return render(
        request,
        "observations/partials/_report_form.html",
        {
            "region": region,
            "lat": lat,
            "lon": lon,
            "accuracy_m": accuracy_m,
            "has_coords": has_coords,
            "location_source": location_source,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "problems": FieldObservation.OBSERVATION_TYPE.choices,
            "submit_url": reverse("observations:report_submit"),
        },
    )


@require_htmx
@require_POST
@ratelimit(key="ip", rate="5/m", block=False)
def report_submit(request: HttpRequest) -> HttpResponse:
    """Create a FieldObservation row and return the confirmation fragment.

    Validates the POST body:
    - ``lat`` / ``lon`` must be present and parseable (400 otherwise).
    - ``location_source`` must be a value from ``LOCATION_SOURCE.values``
      (400 when missing or not recognised).
    - ``observation_type`` must be a value from ``OBSERVATION_TYPE.values``
      (400 when missing or not recognised).
    - Rate-limited to 5 submissions per minute per IP (429 on excess).

    On success, creates the row and returns ``_report_confirmation.html``.
    Region is best-effort — a pin outside every known boundary is accepted
    with ``region=None``; there is no region-required rejection.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered confirmation partial, or an error response.

    """
    gate = _auth_gate(request)
    if gate is not None:
        return gate

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before submitting again.", status=429
        )

    coords = _parse_gps(request.POST.get("lat"), request.POST.get("lon"))
    if coords is None:
        return HttpResponse(
            "Valid lat and lon are required.",
            status=400,
        )

    lat, lon = coords

    # Validate location_source.
    valid_sources = set(FieldObservation.LOCATION_SOURCE.values)
    location_source = request.POST.get("location_source")
    if not location_source or location_source not in valid_sources:
        return HttpResponse(
            "A valid location_source is required (GPS, GPS_REFINED, or MANUAL).",
            status=400,
        )

    # Validate the single observation type.
    valid_values = set(FieldObservation.OBSERVATION_TYPE.values)
    observation_type = request.POST.get("observation_type")
    if not observation_type or observation_type not in valid_values:
        return HttpResponse(
            "A valid observation_type is required.",
            status=400,
        )

    # Optional client-supplied observed_at (SNOW-420) — the tap-time instant
    # stamped by report.js before the request enters the offline mutation
    # queue. None means absent (fall back to the model default below);
    # _INVALID_OBSERVED_AT means present but unparseable or implausible.
    observed_at = _parse_observed_at(request.POST.get("observed_at"))
    if observed_at is _INVALID_OBSERVED_AT:
        return HttpResponse("A valid observed_at is required.", status=400)

    # Parse optional accuracy (metres → kilometres).
    accuracy_radius_km = _parse_accuracy_radius_km(request.POST.get("accuracy_m"))

    # Parse optional raw GPS fix coords (null on MANUAL path).
    gps_coords = _parse_gps(request.POST.get("gps_lat"), request.POST.get("gps_lon"))
    gps_lat: float | None = gps_coords[0] if gps_coords is not None else None
    gps_lon: float | None = gps_coords[1] if gps_coords is not None else None

    # Best-effort region resolution — no region-required rejection.
    region = region_for_point(lat, lon)

    # _auth_gate above guarantees an authenticated User; cast narrows for mypy.
    create_kwargs: dict[str, object] = {
        "user": cast(User, request.user),
        "region": region,
        "latitude": lat,
        "longitude": lon,
        "accuracy_radius_km": accuracy_radius_km,
        "gps_latitude": gps_lat,
        "gps_longitude": gps_lon,
        "location_source": location_source,
        "observation_type": observation_type,
    }
    # Only set observed_at when the client supplied a valid instant — when
    # absent, omitting the key lets the model's timezone.now default fire.
    if isinstance(observed_at, datetime.datetime):
        create_kwargs["observed_at"] = observed_at

    FieldObservation.objects.create(**create_kwargs)

    logger.info(
        "FieldObservation created: user=%s region=%s type=%s source=%s",
        request.user.pk,
        region.region_id if region else None,
        observation_type,
        location_source,
    )

    return render(
        request,
        "observations/partials/_report_confirmation.html",
        {"region": region},
    )
