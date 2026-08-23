"""
apps/observations/views.py — HTMX endpoints for the field-report feature.

Provides the HTMX-only fragment views behind the floating Report roundel on
the map page (SNOW-324; the list/delete pair added by SNOW-658, when that
roundel started opening a panel of the user's own reports rather than the
location flow directly):

- ``report_form`` (GET)   — reads optional GPS fix and location_source from
  query params, resolves the point to a MicroRegion when coords are present,
  returns the one-tap problem-selection form.  No coords are required: when
  absent the form renders in "choose on map" state (MANUAL path).
- ``observation_list`` (GET) — the requesting user's own reports, newest
  first, rendered as the row list inside the map's field-observation panel
  (SNOW-658).
- ``observation_delete`` (POST) — deletes one of the requesting user's own
  reports and returns an empty body, so the row's ``hx-swap="outerHTML"``
  removes it.
- ``report_submit`` (POST) — validates lat/lon, location_source, and
  observation_type; creates a FieldObservation row; returns the thank-you
  confirmation fragment.  Also accepts an optional client-supplied
  ``observed_at`` (SNOW-420) — the tap-time instant, stamped client-side by
  ``report.js`` before the request is handed to the offline mutation queue,
  so a report submitted while offline records when the user actually
  observed the problem rather than whenever the queued mutation replays.
  Validated for shape and plausibility (see ``_parse_observed_at``); falls
  back to the model's ``timezone.now`` default when absent.

Every endpoint above is:
  - authentication-gated (403 for anonymous users);
  - verification-gated (403 unless the user has a verified ``Account``,
    SNOW-430);
  - ``@require_htmx`` (400 for non-HTMX requests).

``report_submit`` additionally applies django-ratelimit (5/m per IP, block=False)
and returns 429 when the limit is exceeded.

Alongside them sits one full page, which shares none of those three rules
because it is not a fragment:

- ``my_observations`` (GET) — ``/account/observations/``, the account
  area's list of the signed-in user's own reports (SNOW-677). No
  ``@require_htmx``; an anonymous visitor is redirected to sign-in rather
  than answered 403, matching the account pages it sits beside.
"""

from __future__ import annotations

import datetime
import logging
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.accounts.models import user_is_verified
from apps.core.coordinates import validate_accuracy_radius_km, validate_coordinates
from apps.core.decorators import require_htmx
from apps.observations.models import FieldObservation
from apps.regions.services.point_match import region_for_point

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
    ``apps.accounts.models.user_is_verified`` — the same one that drives the
    client-side ``report_eligible`` flag in ``apps/public/views.py`` — so the gate
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


@require_htmx
@require_GET
def observation_list(request: HttpRequest) -> HttpResponse:
    """Return the requesting user's own field observations as a row list.

    Powers the map's field-observation panel (SNOW-658), which lazy-loads this
    endpoint into ``[data-report-rows]`` when the roundel is tapped.  Scoped to
    the requesting user by ``for_user`` — a report is a personal record here,
    not the anonymised community overlay (that is
    ``apps.public.api.community_reports_geojson``).

    Ordering is the model's own ``-observed_at``, so the most recent report
    leads the list.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_observation_list.html`` partial, or an error response.

    """
    gate = _auth_gate(request)
    if gate is not None:
        return gate

    # _auth_gate above guarantees an authenticated User; cast narrows for mypy
    # (the same idiom report_submit uses).
    observations = FieldObservation.objects.for_user(
        cast(User, request.user)
    ).select_related("region")
    return render(
        request,
        "observations/partials/_observation_list.html",
        {"observations": observations},
    )


@require_htmx
@require_POST
def observation_delete(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Delete one of the requesting user's own field observations.

    Returns an empty 200 so the row's own ``hx-swap="outerHTML"`` removes it
    from the list — the same shape ``apps.favourites.views.favourite_delete``
    uses, so the two lists behave identically.

    The lookup is scoped to the requesting user, so another user's uuid is a
    404 rather than a deletion: ownership is enforced by the query, not by
    trusting the id in the URL.

    Args:
        request: The incoming HTMX POST request.
        uuid: The observation's uuid.

    Returns:
        An empty 200, or an error response.

    """
    gate = _auth_gate(request)
    if gate is not None:
        return gate

    observation = get_object_or_404(
        FieldObservation, uuid=uuid, user=cast(User, request.user)
    )
    observation.delete()
    logger.info("FieldObservation deleted: user=%s uuid=%s", request.user.pk, uuid)
    return HttpResponse("")


# ---------------------------------------------------------------------------
# Full-page views
# ---------------------------------------------------------------------------


@require_GET
def my_observations(request: HttpRequest) -> HttpResponse:
    """Render the signed-in user's own field reports as a full page (SNOW-677).

    The account area's observations surface, mounted at
    ``/account/observations/``. Until this existed a user could submit a
    report from the map or the bulletin page and then had nowhere to see
    what they had submitted: ``/observations/`` is the 48-hour anonymised
    community stream, not a personal record, and the map panel's own list
    is only reachable behind a roundel on the map canvas.

    Deliberately NOT ``@require_htmx`` — this is a real page a user
    navigates to, not a fragment. It is the full-page host for the same
    partials the map panel uses (``_observation_list.html`` and, through it,
    ``_observation.html``), the relationship
    ``apps.favourites.views.favourite_detail`` has to ``_favourite_card.html``.

    Gating follows the account area rather than the map endpoints above: an
    anonymous visitor is redirected to sign-in, as ``accounts:hub`` and
    ``accounts:settings`` do, not answered 403 the way the HTMX fragments
    are — a person who followed a link to a page should be offered the way
    in, and a fragment has nowhere to render one. There is no verification
    gate either: ``_auth_gate`` keeps unverified users from *creating*
    reports, so an unverified account simply has none and sees the empty
    state.

    Ownership is enforced by the query (``for_user``), so nothing here
    depends on an id supplied by the client.

    No 48-hour cutoff. That window is a property of the community overlay,
    where it bounds what strangers can see; a user's own record has no
    reason to end.

    Timestamps render exactly as recorded. The 15-minute flooring applied
    by ``apps.public.api.community_reports_geojson`` is an anonymisation
    rule for *other people's* reports and must not be applied to the
    owner's own.

    The response carries ``Cache-Control: private, no-store`` — per-user
    content that must never land in a shared cache, mirroring
    ``favourite_detail``. That also keeps it out of the PWA shell cache;
    reading these reports offline is SNOW-661, not this ticket.

    Args:
        request: The incoming GET request.

    Returns:
        Rendered ``observations/my_observations.html``, or a redirect to
        sign-in for an anonymous visitor.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    # No ``cast(User, ...)`` here, unlike the HTMX views above: those narrow
    # via ``_auth_gate``, whose return type mypy cannot follow, while the
    # inline ``is_authenticated`` guard above narrows on its own.
    observations = FieldObservation.objects.for_user(request.user).select_related(
        "region"
    )

    response = render(
        request,
        "observations/my_observations.html",
        {"observations": observations},
    )
    response["Cache-Control"] = "private, no-store"
    return response
