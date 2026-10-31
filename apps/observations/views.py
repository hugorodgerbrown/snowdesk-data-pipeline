"""
apps/observations/views.py — HTMX endpoints for the field-report feature.

Provides the HTMX-only fragment views behind the floating Report roundel on
the map page (SNOW-324; the list/delete pair added by SNOW-658, when that
roundel started opening a panel of the user's own reports rather than the
location flow directly):

- ``report_form`` (GET)   — reads optional GPS fix and location_source from
  query params, resolves the point to a MicroRegion when coords are present,
  returns the report form: three families of toggle tiles (SNOW-1289).
  No coords are required: when absent the form renders in "choose on map"
  state (MANUAL path).
- ``observation_list`` (GET) — the requesting user's own reports, newest
  first, rendered as the row list inside the map's field-observation panel
  (SNOW-658).
- ``observation_delete`` (POST) — deletes one of the requesting user's own
  reports and returns an empty body, so the row's ``hx-swap="outerHTML"``
  removes it.
- ``report_submit`` (POST) — validates lat/lon, location_source, and one
  or more observation_type values; creates one FieldObservation row per
  type, sharing a submission, a Location and an instant (SNOW-1289);
  returns the thank-you confirmation fragment.  Also accepts an optional client-supplied
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
from uuid import UUID, uuid4

import waffle
from django.contrib.auth.models import User
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.accounts.models import user_is_verified
from apps.core.coordinates import parse_coordinates, validate_accuracy_radius_km
from apps.core.decorators import require_htmx
from apps.locations.models import Location
from apps.observations.models import TYPE_FAMILY, FieldObservation
from apps.observations.services.observations import delete_observation
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


def _form_families() -> list[dict[str, object]]:
    """Return the report form's tile groups, one per family (SNOW-1289).

    Families come in ``OBSERVATION_FAMILY`` order and each lists its types
    in ``OBSERVATION_TYPE`` declaration order, so the form reads warning
    signs first, the way the families are ranked on the map.

    Returns:
        A list of ``{"value", "label", "types": [(value, label), ...]}``.

    """
    groups: list[dict[str, object]] = []
    for family_value, family_label in FieldObservation.OBSERVATION_FAMILY.choices:
        types = [
            (value, label)
            for value, label in FieldObservation.OBSERVATION_TYPE.choices
            if TYPE_FAMILY.get(value) == family_value
        ]
        groups.append({"value": family_value, "label": family_label, "types": types})
    return groups


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
    """Return the report form: one toggle tile per type, grouped by family.

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

    coords = parse_coordinates(request.GET.get("lat"), request.GET.get("lon"))
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
    gps_coords = parse_coordinates(
        request.GET.get("gps_lat"), request.GET.get("gps_lon")
    )
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
            "families": _form_families(),
            "submit_url": reverse("observations:report_submit"),
        },
    )


@require_htmx
@require_POST
@ratelimit(key="ip", rate="5/m", block=False)
def report_submit(request: HttpRequest) -> HttpResponse:
    """Create one FieldObservation row per reported type; return the confirmation.

    Validates the POST body:
    - ``lat`` / ``lon`` must be present and parseable (400 otherwise).
    - ``location_source`` must be a value from ``LOCATION_SOURCE.values``
      (400 when missing or not recognised).
    - ``observation_type`` is one or more values from
      ``OBSERVATION_TYPE.values`` (400 when none, or when any is not
      recognised — the report is rejected whole, never half saved).
    - Rate-limited to 5 submissions per minute per IP (429 on excess).

    On success, creates one row per type — one submission, one Location,
    one ``observed_at`` — and returns ``_report_confirmation.html``.
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

    coords = parse_coordinates(request.POST.get("lat"), request.POST.get("lon"))
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

    # SNOW-1289: a report is one or more types. Every value must be known —
    # one bad value rejects the whole report rather than saving the rest,
    # which would record something the reporter never sent. A type ticked
    # twice (a replayed form) counts once; the order is kept.
    valid_values = set(FieldObservation.OBSERVATION_TYPE.values)
    observation_types = list(dict.fromkeys(request.POST.getlist("observation_type")))
    if not observation_types or any(t not in valid_values for t in observation_types):
        return HttpResponse(
            "At least one valid observation_type is required.",
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
    gps_coords = parse_coordinates(
        request.POST.get("gps_lat"), request.POST.get("gps_lon")
    )
    gps_lat: float | None = gps_coords[0] if gps_coords is not None else None
    gps_lon: float | None = gps_coords[1] if gps_coords is not None else None

    # Best-effort region resolution — no region-required rejection.
    region = region_for_point(lat, lon)

    # The Location this observation happened at (SNOW-709). Minted per
    # report rather than matched against an existing one: exact float
    # equality on a user coordinate is a false economy, and wrongly merging
    # two reports into one place is worse than an extra row. Locations are
    # *shared* by curation — an observation pinned to a curated Mont Fort —
    # not by automatic dedup.
    #
    # No elevation: an observation shows no forecast panel, and
    # resolving one would put an Open-Meteo call on the report path.
    #
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
        # One submission and one instant for every row of the report, so the
        # map and the region panel can put it back together (SNOW-1289).
        "submission": uuid4(),
        "observed_at": (
            observed_at
            if isinstance(observed_at, datetime.datetime)
            else timezone.now()
        ),
    }

    # The Location and its observation are one write (SNOW-1228): a failed
    # observation insert must not leave an orphan Location behind, which
    # Location.objects.active() would then fetch weather for. Nothing in
    # the block does I/O — the region was resolved above.
    with transaction.atomic():
        create_kwargs["location"] = Location.objects.create(latitude=lat, longitude=lon)
        for observation_type in observation_types:
            FieldObservation.objects.create(
                observation_type=observation_type, **create_kwargs
            )

    logger.info(
        "FieldObservation created: user=%s region=%s types=%s source=%s",
        request.user.pk,
        region.region_id if region else None,
        ",".join(observation_types),
        location_source,
    )

    return render(
        request,
        "observations/partials/_report_confirmation.html",
        {"region": region},
    )


def _attach_three_word_addresses(
    request: HttpRequest, observations: list[FieldObservation]
) -> None:
    """Hang each report's three word address on the instance, for the list.

    **THIS SURFACE ONLY, AND THAT IS A PRIVACY BOUNDARY, NOT AN OMISSION.**
    ``observation_list`` is scoped by ``for_user``, so a reader here is
    reading their own reports and a 3m address tells them nothing they did
    not supply. The map's community overlay is the opposite: it is public,
    it carries other people's reports, and
    ``apps.public.api.community_reports_geojson`` exists to anonymise them
    — rounding coordinates to three decimals (~80-110 m) and never sending
    the raw field. A three word address names a 3m square, so putting one
    in that payload would hand back an order of magnitude more precision
    than the coordinate it sits beside and undo the anonymisation entirely.
    The address must not reach that endpoint (SNOW-883).

    **THE STORED ADDRESS ONLY, NEVER A CONVERSION**, the same rule the trip
    list and the favourite card follow: the column is filled out of band,
    so this is a plain property read and a panel open never blocks on a
    five-second HTTP timeout.

    Read the flag ONCE for the whole list rather than per row — a
    percentage-scoped flag is not obliged to answer the same way twice, and
    a panel showing some rows an address and others none would look broken
    rather than gated.

    Args:
        request: The current request, read for the ``what3words`` flag.
        observations: The reports to annotate, mutated in place.

    """
    active = waffle.flag_is_active(request, "what3words")
    for observation in observations:
        location = observation.location
        observation.three_word_address = (  # type: ignore[attr-defined]
            location.three_word_address if active and location is not None else None
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

    ``map_focus`` is what tells the shared row it is being read over a map,
    so each row's label renders as a control that frames its report (see
    ``observations/partials/_observation.html``). It is always on: the map
    sheet is this endpoint's only surface. (SNOW-752 made it follow a
    ``?variant=map`` parameter while ``/account/observations/`` re-read the
    same list on a page with no map to fly; SNOW-803 removed that page, and
    the flag with it.)

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
    observations = list(
        FieldObservation.objects.for_user(cast(User, request.user)).select_related(
            "region", "location"
        )
    )
    _attach_three_word_addresses(request, observations)
    return render(
        request,
        "observations/partials/_observation_list.html",
        {
            "observations": observations,
            "map_focus": True,
        },
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

    The delete goes through ``delete_observation`` so the report's minted
    anonymous ``Location`` is erased with it (SNOW-1170).

    Args:
        request: The incoming HTMX POST request.
        uuid: The observation's uuid.

    Returns:
        An empty 200, or an error response.

    """
    gate = _auth_gate(request)
    if gate is not None:
        return gate

    try:
        delete_observation(cast(User, request.user), uuid)
    except FieldObservation.DoesNotExist as exc:
        raise Http404("No FieldObservation matches the given query.") from exc
    logger.info("FieldObservation deleted: user=%s uuid=%s", request.user.pk, uuid)
    return HttpResponse("")


# ---------------------------------------------------------------------------
# Full-page views
# ---------------------------------------------------------------------------
