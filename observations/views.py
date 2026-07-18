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
  confirmation fragment.

Both endpoints are:
  - flag-gated on ``field_observations`` (404 when inactive);
  - authentication-gated (403 for anonymous users);
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
    _require_field_observations_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

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
    _require_field_observations_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

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

    # Parse optional accuracy (metres → kilometres).
    accuracy_radius_km: float | None = None
    accuracy_m_str = request.POST.get("accuracy_m")
    if accuracy_m_str:
        try:
            accuracy_radius_km = float(accuracy_m_str) / 1000.0
        except ValueError, TypeError:
            accuracy_radius_km = None

    # Parse optional raw GPS fix coords (null on MANUAL path).
    gps_coords = _parse_gps(request.POST.get("gps_lat"), request.POST.get("gps_lon"))
    gps_lat: float | None = gps_coords[0] if gps_coords is not None else None
    gps_lon: float | None = gps_coords[1] if gps_coords is not None else None

    # Best-effort region resolution — no region-required rejection.
    region = region_for_point(lat, lon)

    FieldObservation.objects.create(
        user=request.user,
        region=region,
        latitude=lat,
        longitude=lon,
        accuracy_radius_km=accuracy_radius_km,
        gps_latitude=gps_lat,
        gps_longitude=gps_lon,
        location_source=location_source,
        observation_type=observation_type,
    )

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
