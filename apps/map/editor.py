"""
apps/map/editor.py — Superuser endpoints behind the in-map editor.

The resort editor (SNOW-74, ``?edit=resorts`` on /map/) and the curated
``Location`` editor (SNOW-755, ``?edit=locations``). Every view checks
``request.user.is_superuser`` (SNOW-724) and 404s for everyone else:

* ``GET  /api/edit/resorts/queue/``               — queue + catalogue payload.
* ``POST /api/edit/resorts/<slug>/save/``          — persist the placed
  coordinates and the hand-curated detail fields.
* ``POST /api/edit/resorts/create/``              — create a resort from a
  placed pin, deriving its parent region from that pin.
* ``GET  /api/edit/locations/queue/``             — the estate, each
  location's links, and the resort catalogue to link against.
* ``POST /api/edit/locations/create/``            — mint a location from a
  placed pin and link it to one resort.
* ``POST /api/edit/locations/<short_id>/save/``   — move, rename or
  re-classify an existing location.
* ``POST /api/edit/locations/<short_id>/link/``   — attach it to another
  resort, so Mont Fort stays one row that four resorts share.
* ``POST /api/edit/locations/links/<uuid:link_uuid>/unlink/`` — drop one
  link, keeping the location.

Moved out of ``apps/public/api.py`` by SNOW-1428
(docs/decisions/public-splits-by-surface.md). The URL names stay ``api:…``.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import (
    Http404,
    HttpRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.locations.models import Location, LocationQuerySet, ResortLocation
from apps.locations.services.prime import enqueue_location_prime
from apps.regions.forms import RESORT_DETAIL_FIELDS, ResortDetailsForm
from apps.regions.models import (
    MicroRegion,
    Resort,
    SubRegion,
)
from apps.regions.services.resort_edit import canton_for_region
from apps.regions.services.resort_pin import set_resort_pin

logger = logging.getLogger(__name__)

# Swiss bounding box (west, south, east, north) in decimal degrees. Used
# by ``_validate_swiss_coords`` for the SNOW-74 resort-edit endpoint;
# the SNOW-9 offline-manifest tile generators that previously also
# consumed this constant were retired in SNOW-79 (PWA shell rewrite).
_SWISS_BBOX: tuple[float, float, float, float] = (5.9, 45.8, 10.5, 47.8)


def _require_edit_map_admin(request: HttpRequest) -> None:
    """Raise Http404 unless the request user is a superuser.

    Mirrors the view-level guard ``map_view`` applies before rendering
    the editor panel: an unauthorised caller hitting the API directly
    must see the same 404 the URL conf used to give them when the
    feature was DEBUG-only. Until SNOW-724 the audience was expressed as
    an ``edit_map`` waffle flag seeded with ``superusers=True``; that was
    a permanent audience restriction wearing a rollout gate's clothes, so
    the check is now an ordinary Django one and the flag is gone.
    """
    if not request.user.is_superuser:
        raise Http404("The resort editor is superuser-only.")


def _validate_swiss_coords(lat: float, lon: float) -> str | None:
    """
    Return an error message if (lat, lon) is outside ``_SWISS_BBOX``.

    Returns ``None`` for valid coordinates. Boundary values are accepted.
    """
    west, south, east, north = _SWISS_BBOX
    if not (south <= lat <= north):
        return f"Latitude {lat} outside Swiss bbox {south}–{north}"
    if not (west <= lon <= east):
        return f"Longitude {lon} outside Swiss bbox {west}–{east}"
    return None


def _rings_from_geometry(
    geometry: dict[str, Any],
) -> list[list[list[float]]]:
    """Return all rings from a GeoJSON Polygon or MultiPolygon geometry.

    For ``Polygon`` the rings are the top-level ``coordinates`` list
    (outer ring + any holes).  For ``MultiPolygon`` the rings are the
    concatenation of each member polygon's ring list.

    Args:
        geometry: GeoJSON Polygon or MultiPolygon geometry dict.

    Returns:
        List of rings, each ring being a list of ``[lon, lat]`` vertices.

    """
    geo_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geo_type == "Polygon":
        return list(coordinates)
    if geo_type == "MultiPolygon":
        rings: list[list[list[float]]] = []
        for member in coordinates:
            rings.extend(member)
        return rings
    return []


def _point_in_polygon(lat: float, lon: float, polygon: dict[str, Any]) -> bool:
    """
    Return True if (lat, lon) lies inside a GeoJSON Polygon or MultiPolygon.

    Implements the standard ray-casting algorithm: cast a horizontal ray
    east of the point and count how many polygon edges it crosses. Odd
    crossings = inside. Looping over every ring (outer + any holes) at
    once correctly handles holes: a point inside the outer ring but
    inside a hole gets an even total and is reported as outside, which
    is the right answer.

    For ``MultiPolygon`` the rings from all member polygons are checked
    together. Because each member polygon is topologically independent
    (non-overlapping outer rings), flipping parity across member-polygon
    rings still gives the correct result: a point outside all members
    accumulates an even count; a point inside exactly one member's outer
    ring (and outside its holes) accumulates an odd count.

    Polygon coordinates are stored in GeoJSON order as ``[lon, lat]``;
    we rename to ``x, y`` here so the algorithm reads naturally. Edge
    cases: a point exactly on a horizontal edge can flip either way
    depending on tie-breaking, but we don't need pixel-perfect boundary
    behaviour — the resort save-coords path uses this to pick a
    *containing* region for an admin-placed pin, and the operator can
    always nudge the pin if it lands ambiguously.

    Args:
        lat: Latitude of the test point (WGS 84).
        lon: Longitude of the test point (WGS 84).
        polygon: GeoJSON Polygon or MultiPolygon geometry as stored in
            ``Region.boundary``.

    Returns:
        True if the point lies inside the polygon (or any member of the
        MultiPolygon).

    """
    x, y = lon, lat
    inside = False
    for ring in _rings_from_geometry(polygon):
        # Iterate edges of this ring as (i-1, i) vertex pairs.
        n = len(ring)
        if n < 3:
            continue
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            # Standard ray-cast: count an edge crossing if the test
            # point's y lies between the edge endpoints' y, AND the
            # x of the edge at that y is to the right of the test
            # point. The strict-inequality on yi/yj avoids
            # double-counting at shared vertices.
            if (yi > y) != (yj > y):
                x_at_y = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < x_at_y:
                    inside = not inside
            j = i
    return inside


def _all_coords_from_geometry(
    polygon: dict[str, Any],
) -> list[tuple[float, float]]:
    """Return every ``(lon, lat)`` coordinate pair across all rings of a geometry.

    Flattens all rings from both ``Polygon`` and ``MultiPolygon`` inputs
    via :func:`_rings_from_geometry` so the bbox computation has a single
    flat list to min/max over.

    Args:
        polygon: GeoJSON Polygon or MultiPolygon geometry dict.

    Returns:
        Flat list of ``(lon, lat)`` pairs; empty when geometry has no rings.

    """
    return [(x, y) for ring in _rings_from_geometry(polygon) for x, y in ring]


def _bbox_of_polygon(
    polygon: dict[str, Any],
) -> tuple[float, float, float, float] | None:
    """Return ``(west, south, east, north)`` of a GeoJSON Polygon or MultiPolygon.

    For ``Polygon`` only the outer ring is used (as before). For
    ``MultiPolygon`` all member polygons' outer rings are considered so
    the returned bbox encloses the full extent.

    Returns ``None`` if the geometry has no usable coordinates. Used by
    :func:`_region_for_point` as a cheap pre-filter so the full
    ray-cast only runs on regions whose bbox could plausibly contain
    the point.
    """
    coords = _all_coords_from_geometry(polygon)
    if not coords:
        return None
    lons = [x for x, _ in coords]
    lats = [y for _, y in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _region_for_point(lat: float, lon: float) -> MicroRegion | None:
    """Return the MicroRegion whose boundary polygon contains (lat, lon).

    Returns ``None`` if the point falls outside every region.

    Iterates ``MicroRegion.objects.exclude(boundary__isnull=True)`` and runs
    a bbox pre-filter followed by a full ray-cast. Used by the
    edit-resorts save endpoint to auto-correct a resort's parent-region
    FK when the saved pin lands outside the FK's polygon — some
    imported resorts have wrong region tags (e.g. Villars-sur-Ollon
    seeded as CH-1113 but actually in CH-1114), and the operator
    placing a pin is the most authoritative signal we'll get.

    The lookup is O(regions × ring vertices) Python — ~150 regions
    each with a few hundred vertices means single-digit ms per call,
    fine for an interactive admin tool.

    Args:
        lat: Latitude (WGS 84).
        lon: Longitude (WGS 84).

    Returns:
        The first matching MicroRegion, or ``None``. "First" is in the
        MicroRegion default ordering — ties (a point on a shared boundary)
        are unlikely in practice and not worth disambiguating.

    """
    for region in MicroRegion.objects.exclude(boundary__isnull=True).iterator():
        # The ``exclude(boundary__isnull=True)`` filter already drops
        # null rows; the explicit guard here is for mypy's benefit
        # (``MicroRegion.boundary`` is typed as Optional) and as defence in
        # depth against a future schema/migration that lets nulls back
        # in. ``assert`` would be the pythonic check but ruff's S101
        # rejects assertions outside test code.
        boundary = region.boundary
        if boundary is None:
            continue
        bbox = _bbox_of_polygon(boundary)
        if bbox is None:
            continue
        w, s, e, n = bbox
        if not (w <= lon <= e and s <= lat <= n):
            continue
        if _point_in_polygon(lat, lon, boundary):
            return region
    return None


@require_GET
def edit_resorts_queue(request: HttpRequest) -> JsonResponse:
    """Return the flat resort catalogue + L2 labels (flag-gated).

    Response shape::

        {
          "all_resorts": [{catalogue-entry}, ...],
          "sub_regions": {"CH-41": "Lower Valais", ...}
        }

    Catalogue order is L2 prefix → L4 region_id → name. Sorting by
    ``region__region_id`` groups entries by L2 (the L2 prefix is a
    prefix of the full region_id) so the JS can detect L2 transitions
    just by comparing the first 5 chars of consecutive rows'
    ``region_id`` and emit a section header labelled with the L2 name
    from ``sub_regions``.

    Each catalogue entry carries the fields the side panel needs to
    render a row and (on click) a full target readout: ``id``, ``slug``
    (the key the panel selects on and builds its save URL from, SNOW-798),
    ``name``, ``region_id``, ``region_name``, ``canton``, ``latitude``,
    ``longitude``, ``has_coords``, ``needs_review``, plus a ``details``
    object holding every hand-curated metadata field
    (``apps.regions.forms.RESORT_DETAIL_FIELDS``) so the panel can populate
    its edit form on selection without a per-resort round trip.

    ``sub_regions`` maps L2 prefixes (e.g. ``"CH-41"``) to a display
    label — ``name_en`` when SLF publishes one, otherwise ``name_native``.
    L1 grouping was tried first (SNOW-85 addendum 3) but L2 is a more
    useful grouping for the operator: ~25 L2 sections of ~5–10
    resorts each scans better than ~9 L1 sections of ~10–30.

    The endpoint name and URL are kept from SNOW-74 (``edit_resorts_queue``,
    ``/api/edit/resorts/queue/``) for minimal-diff reasons even though
    the SNOW-85 manual workflow no longer surfaces a "queue" of unset
    rows. Renaming the URL would require a coordinated panel-template
    + JS update for no behavioural benefit.

    Returns 404 unless the request user is a superuser (SNOW-724 —
    previously the ``edit_map`` waffle flag, seeded ``superusers=True``).
    """
    _require_edit_map_admin(request)
    all_resorts = [
        {
            "id": row["pk"],
            "slug": row["slug"],
            "name": row["name"],
            "region_id": row["region__region_id"],
            "region_name": row["region__name"],
            "canton": row["canton"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "has_coords": row["latitude"] is not None and row["longitude"] is not None,
            "needs_review": row["needs_review"],
            "details": {field: row[field] for field in RESORT_DETAIL_FIELDS},
        }
        for row in (
            Resort.objects
            # L2 (e.g. "CH-41") is a prefix of L4 (e.g. "CH-4115"), so
            # sorting on region_id alone groups rows by L2 in the right
            # order. ``name`` breaks ties within a region.
            .order_by("region__region_id", "name").values(
                "pk",
                "slug",
                "name",
                "region__region_id",
                "region__name",
                "canton",
                "latitude",
                "longitude",
                "needs_review",
                *RESORT_DETAIL_FIELDS,
            )
        )
    ]
    # Prefer the English name when SLF publishes one (some L2 entries
    # have ``name_en=""``); fall back to the locally-dominant name so
    # the section header is never blank.
    sub_regions = {
        prefix: (name_en or name_native)
        for prefix, name_en, name_native in SubRegion.objects.values_list(
            "prefix",
            "name_en",
            "name_native",
        )
    }
    return JsonResponse(
        {
            "all_resorts": all_resorts,
            "sub_regions": sub_regions,
        }
    )


def _resort_details_payload(resort: Resort) -> dict[str, Any]:
    """Return the hand-curated metadata fields of ``resort`` as a dict.

    The single shape the panel reads on both legs of the round trip — the
    catalogue's ``details`` object and the save response's — so a saved
    row can be patched into the in-memory catalogue verbatim.
    """
    return {field: getattr(resort, field) for field in RESORT_DETAIL_FIELDS}


def _bind_resort_details(
    resort: Resort,
    raw_details: Any,
) -> tuple[JsonResponse | None, list[str]]:
    """Validate and apply the incoming detail fields to ``resort`` in memory.

    An absent key means "leave this field alone": the incoming values are
    merged over the instance's current ones before binding, so the panel
    can send a partial object and a field it never renders can never be
    blanked by omission.

    Nothing is written to the database — the caller saves once, with the
    coordinate columns, so a rejected detail field cannot leave a
    half-applied row behind.

    Args:
        resort: The instance to apply the values to (mutated in place on
            success).
        raw_details: The request body's ``details`` value, unvalidated.

    Returns:
        ``(error_response, [])`` when the payload is malformed or fails
        validation, otherwise ``(None, changed_field_names)`` — the names
        to add to the caller's ``update_fields``.

    """
    if raw_details is None:
        return None, []
    if not isinstance(raw_details, dict):
        return (
            JsonResponse(
                {"error": "invalid_details", "detail": "details must be an object"},
                status=400,
            ),
            [],
        )

    data = {
        field: raw_details.get(field, getattr(resort, field))
        for field in RESORT_DETAIL_FIELDS
    }
    form = ResortDetailsForm(data, instance=resort)
    if not form.is_valid():
        return (
            JsonResponse(
                {
                    "error": "invalid_details",
                    "detail": "one or more detail fields are invalid",
                    "fields": form.errors,
                },
                status=400,
            ),
            [],
        )
    # commit=False applies the cleaned values to the instance without
    # touching the database; the caller's single save() persists them
    # alongside the coordinates.
    form.save(commit=False)
    return None, list(RESORT_DETAIL_FIELDS)


def _parse_edit_body(
    request: HttpRequest,
) -> tuple[JsonResponse | None, dict[str, Any]]:
    """Decode a JSON request body into a dict, or return the 400 to send.

    Shared by the two edit-resorts write endpoints, which take the same
    body shape modulo the create-only ``name``/``canton`` keys.

    Returns:
        ``(error_response, {})`` when the body is not a JSON object,
        otherwise ``(None, payload)``.

    """
    try:
        payload = json.loads(request.body or b"")
    except ValueError, json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json"}, status=400), {}
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_json"}, status=400), {}
    return None, payload


def _parse_edit_coords(payload: dict[str, Any]) -> JsonResponse | tuple[float, float]:
    """Validate the ``latitude``/``longitude`` pair of an edit-resorts POST.

    Both edit endpoints require a point inside the Swiss bounding box —
    the placed pin *is* the coordinate being saved, and for the create
    endpoint it is also what the parent region is derived from.

    Returns either the 400 response to send (missing, non-numeric, or
    out-of-bbox pair) or the parsed ``(lat, lon)``; the caller
    discriminates with ``isinstance``.
    """
    raw_lat = payload.get("latitude")
    raw_lon = payload.get("longitude")
    if raw_lat is None or raw_lon is None:
        return JsonResponse(
            {
                "error": "invalid_coords",
                "detail": "latitude and longitude are required",
            },
            status=400,
        )
    try:
        lat = float(raw_lat)
        lon = float(raw_lon)
    except TypeError, ValueError:
        return JsonResponse(
            {
                "error": "invalid_coords",
                "detail": "latitude and longitude must be numbers",
            },
            status=400,
        )

    bbox_error = _validate_swiss_coords(lat, lon)
    if bbox_error:
        return JsonResponse(
            {"error": "out_of_bounds", "detail": bbox_error}, status=400
        )
    return lat, lon


def _resort_save_payload(resort: Resort) -> dict[str, Any]:
    """Return the JSON body both write endpoints answer with.

    One shape for both legs so the panel can patch an edited row and
    build a catalogue entry for a created one from the same keys — the
    field set is a superset of a ``edit_resorts_queue`` catalogue entry
    (``has_coords`` included) plus the geocode provenance columns the
    endpoint stamped.
    """
    return {
        "id": resort.pk,
        "slug": resort.slug,
        "name": resort.name,
        "region_id": resort.region.region_id,
        "region_name": resort.region.name,
        "canton": resort.canton,
        "latitude": resort.latitude,
        "longitude": resort.longitude,
        "has_coords": resort.latitude is not None and resort.longitude is not None,
        "geocode_source": resort.geocode_source,
        "geocode_confidence": resort.geocode_confidence,
        "geocoded_at": resort.geocoded_at.isoformat() if resort.geocoded_at else None,
        "needs_review": resort.needs_review,
        "details": _resort_details_payload(resort),
    }


@require_POST
def edit_resort_save(request: HttpRequest, slug: str) -> JsonResponse:
    """Persist the placed coordinates and detail fields of a resort (flag-gated).

    Request body (JSON)::

        {
          "latitude": <float>,
          "longitude": <float>,
          "details": {"num_lifts": 12, "website": "https://…", …}
        }

    ``details`` is optional and may be partial — every key it omits keeps
    its stored value (see ``_bind_resort_details``); its permitted keys
    are ``apps.regions.forms.RESORT_DETAIL_FIELDS``.

    On success, sets ``geocode_source="MANUAL"``,
    ``geocode_confidence=1.0``, ``geocoded_at=now()``, and clears
    ``needs_review`` — the operator has the pin under the placement
    marker whenever they save, so every save is a manual confirmation of
    the position, not just of the detail fields. Auto-rebinds
    ``resort.region`` if the saved point lands inside a different
    region's polygon (SNOW-85). Returns the updated resort fields
    including the (possibly re-bound) ``region_id`` and ``region_name``
    and the saved ``details`` so the panel can patch its in-memory
    catalogue without a follow-up GET.

    Errors:
        404 — request user is not a superuser, or unknown ``slug``.
        400 — invalid JSON; missing or non-float lat/lon; coordinates
              outside the Swiss bounding box; a detail field that fails
              model validation (``{"error": "invalid_details", "fields":
              {…}}``, keyed by field name).
    """
    _require_edit_map_admin(request)

    body_error, payload = _parse_edit_body(request)
    if body_error is not None:
        return body_error

    coords = _parse_edit_coords(payload)
    if isinstance(coords, JsonResponse):
        return coords
    lat, lon = coords

    resort = get_object_or_404(
        Resort.objects.select_related("region"),
        slug=slug,
    )

    details_error, changed_details = _bind_resort_details(
        resort,
        payload.get("details"),
    )
    if details_error is not None:
        return details_error

    # The pin itself (latitude/longitude/location) is written by
    # set_resort_pin below, not here — SNOW-1144.
    resort.geocode_source = Resort.GeocodeSource.MANUAL
    resort.geocode_confidence = 1.0
    resort.geocoded_at = timezone.now()
    resort.needs_review = False
    update_fields = [
        "geocode_source",
        "geocode_confidence",
        "geocoded_at",
        "needs_review",
        "updated_at",
        *changed_details,
    ]

    # Auto-rebind the parent region from the clicked location. Some
    # imported resorts have wrong region tags (e.g. Villars-sur-Ollon
    # and Gryon were seeded as CH-1113 but sit in CH-1114) and the
    # operator placing a pin is the most authoritative signal we'll
    # get. If the saved point is outside every region polygon (rare,
    # would need to be in a no-coverage gap), leave the FK alone
    # rather than nulling it. We log when a rebind fires so a
    # subsequent ``dump_resorts_sheet --commit`` makes the data
    # change visible in the diff.
    containing = _region_for_point(lat, lon)
    if containing is not None and containing.pk != resort.region_id:
        logger.info(
            "edit_resort_save: rebinding %s from %s to %s",
            resort.name,
            resort.region.region_id,
            containing.region_id,
        )
        resort.region = containing
        update_fields.append("region")

    with transaction.atomic():
        resort.save(update_fields=update_fields)
        # SNOW-1144: the one writer of the pin — moves the resort's Location
        # in place so its weather stays attached, and mirrors lat/lon.
        set_resort_pin(resort, lat, lon)

    return JsonResponse(_resort_save_payload(resort))


def _parse_new_resort_identity(
    payload: dict[str, Any],
) -> JsonResponse | tuple[str, str]:
    """Validate the ``name``/``canton`` inputs of a create request.

    ``name`` is the one value the endpoint cannot get from anywhere else.
    ``canton`` is optional here and may come back empty — the caller
    fills it from the derived region via ``canton_for_region``, and
    only fails if that comes up empty too.

    Returns either the 400 response to send or the cleaned
    ``(name, canton)``; ``canton`` is upper-cased so "vs" and "VS" are
    one value.
    """
    name = str(payload.get("name") or "").strip()
    canton = str(payload.get("canton") or "").strip().upper()
    if not name:
        return JsonResponse(
            {"error": "invalid_identity", "detail": "name is required"},
            status=400,
        )
    # Mirror the model's column widths — a too-long value would otherwise
    # be silently truncated by SQLite or blow up on PostgreSQL.
    if len(name) > 255 or len(canton) > 5:
        return JsonResponse(
            {
                "error": "invalid_identity",
                "detail": "name must be ≤255 chars and canton ≤5",
            },
            status=400,
        )
    return name, canton


def _resolve_canton(region: MicroRegion, canton: str) -> JsonResponse | str:
    """Return the canton to store, or the 400 to send if there isn't one.

    An operator-supplied ``canton`` always wins; a blank one falls back to
    ``canton_for_region``. Only a region with nothing to inherit from
    has to ask the operator for it.
    """
    resolved = canton or canton_for_region(region)
    if not resolved:
        return JsonResponse(
            {
                "error": "invalid_identity",
                "detail": (
                    f"{region.region_id} has no resorts to take a canton from; "
                    "enter one."
                ),
            },
            status=400,
        )
    return resolved


@require_POST
def edit_resort_create(request: HttpRequest) -> JsonResponse:
    """Create a resort from a placed pin (flag-gated).

    The create half of the edit-resorts panel: identical to
    :func:`edit_resort_save` — same coordinate rules, same ``details``
    validation, same response shape — except that there is no row yet,
    so the panel also sends ``name``, and the parent region is
    **derived from the pin** rather than rebound against an existing FK.

    Request body (JSON)::

        {
          "name": "Verbier",
          "canton": "VS",            # optional — see below
          "latitude": <float>,
          "longitude": <float>,
          "details": {"num_lifts": 12, "website": "https://…", …}
        }

    ``name`` is the only value the endpoint cannot source from anywhere
    else. ``canton`` is optional: omitted, it is inherited from the
    resorts already in the derived region (``canton_for_region``),
    which is the right answer for all but border cases; sent, it wins,
    so the operator can correct one. A region with no resorts to inherit
    from is the one case that has to ask.

    The new row is stamped exactly as a save stamps an edited one
    (``geocode_source="MANUAL"``, ``geocode_confidence=1.0``,
    ``geocoded_at=now()``, ``needs_review=False``) — the operator placed
    the pin, so the position is manually confirmed by construction.

    A created resort lives only in this environment's database (see
    ``docs/decisions/resorts-are-editable-data.md``); run
    ``manage.py dump_resorts_sheet --commit`` to carry it to other
    worktrees and CI, and add it to ``apps/regions/data/resorts.tsv`` so the
    next ``import_resorts`` run lists it in the sheet (it is never deleted
    for being absent — SNOW-1404).

    Errors:
        404 — request user is not a superuser.
        400 — invalid JSON; missing/blank ``name``, or an omitted
              ``canton`` the derived region cannot supply
              (``invalid_identity``); missing or non-float lat/lon;
              coordinates outside the Swiss bounding box; a pin that
              falls outside every region polygon (``no_region``, since
              ``Resort.region`` is not nullable); a detail field that
              fails model validation (``invalid_details``).
        409 — a resort of that name already exists in the derived region
              (``duplicate_name``), which is nearly always a double-click
              or a re-created row rather than a genuine second resort.
    """
    _require_edit_map_admin(request)

    body_error, payload = _parse_edit_body(request)
    if body_error is not None:
        return body_error

    identity = _parse_new_resort_identity(payload)
    if isinstance(identity, JsonResponse):
        return identity
    name, canton = identity

    coords = _parse_edit_coords(payload)
    if isinstance(coords, JsonResponse):
        return coords
    lat, lon = coords

    # The parent region is the whole point of "click the map to add a
    # resort" — no region picker, the pin says which region it is in.
    # ``Resort.region`` is a non-nullable FK, so a pin in a no-coverage
    # gap is a hard failure rather than a row with a dangling parent.
    region = _region_for_point(lat, lon)
    if region is None:
        return JsonResponse(
            {
                "error": "no_region",
                "detail": (
                    f"No avalanche region contains ({lat:.5f}, {lon:.5f}); "
                    "move the pin inside a region boundary."
                ),
            },
            status=400,
        )

    # all_objects: a retired resort of the same name is restored, not
    # created again (SNOW-1404).
    existing = Resort.all_objects.filter(region=region, name__iexact=name).first()
    if existing is not None:
        retired = " (retired: restore it instead)" if existing.is_retired else ""
        return JsonResponse(
            {
                "error": "duplicate_name",
                "detail": f"{name} already exists in {region.region_id}{retired}.",
            },
            status=409,
        )

    # Canton is a curatorial column, not a derived one — but it is very
    # nearly a function of the region, so an omitted value is inherited
    # from the region's existing resorts rather than demanded of the
    # operator.
    resolved_canton = _resolve_canton(region, canton)
    if isinstance(resolved_canton, JsonResponse):
        return resolved_canton

    resort = Resort(
        name=name,
        canton=resolved_canton,
        region=region,
        geocode_source=Resort.GeocodeSource.MANUAL,
        geocode_confidence=1.0,
        geocoded_at=timezone.now(),
        needs_review=False,
    )

    details_error, _changed_details = _bind_resort_details(
        resort,
        payload.get("details"),
    )
    if details_error is not None:
        return details_error

    # One INSERT with the details already bound — a rejected detail field
    # cannot leave a half-populated new row behind. The pin follows in the
    # same transaction through its one writer (SNOW-1144), so a resort is
    # never committed without the Location its weather hangs off.
    with transaction.atomic():
        resort.save()
        set_resort_pin(resort, lat, lon)
    logger.info(
        "edit_resort_create: created %s in %s at (%s, %s)",
        resort.name,
        region.region_id,
        lat,
        lon,
    )

    return JsonResponse(_resort_save_payload(resort), status=201)


# Decimal places a placed pin is stored to. Five is ~1 m in Switzerland:
# the precision the editor reads out, and the precision
# ``apps/locations/data/locations.tsv`` is written to. Storing more than
# the sheet can carry would make ``import_locations`` report an update on
# every run — a permanent diff of trailing digits that teaches an operator
# to ignore the one signal that says whether the estate matches git.
_LOCATION_COORD_DECIMALS = 5


def _location_link_payload(link: ResortLocation) -> dict[str, Any]:
    """Return one resort-to-location link as the panel reads it.

    Args:
        link: The link, with ``resort`` and ``resort.region`` already
            selected (the queue endpoint prefetches both).

    Returns:
        The link's JSON shape — its own ``uuid``, so unlink can name it
        (SNOW-798), plus enough of the resort to render a row without a
        second request.

    """
    return {
        "id": link.pk,
        "uuid": str(link.uuid),
        "resort_id": link.resort_id,
        "resort_name": link.resort.name,
        "region_id": link.resort.region.region_id,
        "role": link.role,
        "is_primary": link.is_primary,
    }


def _location_payload(location: Location) -> dict[str, Any]:
    """Return one location and its links as the panel reads it.

    The single shape every endpoint below answers with, so the panel can
    splice a created location and patch a saved one from the same keys.
    ``elevation_m`` is included but read-only — it is resolved from the
    coordinate out-of-band, never supplied — and is shown because a
    resolved height nowhere near the expected one is how a mis-placed pin
    announces itself.

    Args:
        location: The location, with its links prefetched or selected.

    Returns:
        The location's JSON shape.

    """
    return {
        "id": location.pk,
        "uuid": str(location.uuid),
        # SNOW-798: what the panel selects on and builds save/link URLs
        # from — the same identifier the public weather surfaces use.
        "short_id": location.short_id,
        "name": location.name,
        "kind": location.kind,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "elevation_m": location.elevation_m,
        "links": [
            _location_link_payload(link) for link in location.resort_locations.all()
        ],
    }


def _locations_with_links() -> LocationQuerySet:
    """Return a Location queryset carrying each row's links in one query.

    The prefetch is not an optimisation, it is the difference between one
    query and one per row: every payload above renders a location's resort
    links inline, so a bare queryset would go back to the database once per
    location in a list meant to grow to hundreds — and again per link for
    the resort and its region.

    Returns:
        An unfiltered ``Location`` queryset with ``resort_locations``,
        their resorts and those resorts' regions prefetched.

    """
    return Location.objects.prefetch_related(
        Prefetch(
            "resort_locations",
            queryset=ResortLocation.objects.select_related(
                "resort", "resort__region"
            ).order_by("resort__name", "pk"),
        )
    )


def _reload_with_links(location: Location) -> Location:
    """Re-read one location with its links, for a write endpoint's response.

    A location fetched by pk (or just created) has no prefetch behind it,
    so building its payload straight off the instance would walk the links
    one query at a time. One re-read is cheaper and, after a create or an
    unlink, is also the only way the response reflects the write.

    Args:
        location: The instance just written.

    Returns:
        The same row, re-read with its links.

    """
    return _locations_with_links().get(pk=location.pk)


def _curated_locations() -> LocationQuerySet:
    """Return the curated estate, in the order the panel lists it.

    Returns:
        Named locations only, name-ordered, links prefetched.

    """
    return _locations_with_links().named().order_by("name", "pk")


@require_GET
def edit_locations_queue(request: HttpRequest) -> JsonResponse:
    """Return the curated location estate plus everything to link against.

    Response shape::

        {
          "locations": [{location-payload}, ...],
          "resorts":   [{"id", "name", "region_id", "region_name",
                         "latitude", "longitude"}, ...],
          "kinds":     [{"value": "PEAK", "label": "Peak"}, ...],
          "roles":     [{"value": "TOP",  "label": "Top"}, ...]
        }

    One request, because the panel needs all four to render at all: the
    estate is the list, the resorts are what a location is attached to,
    and the two vocabularies fill the selects. Serving the choices rather
    than hard-coding them in the panel keeps ``Location.KIND`` and
    ``ResortLocation.ROLE`` as the single source of both.

    Only named locations are listed. The anonymous rows minted from
    favourites and field observations live in the same table but are user
    data, not the curated estate — the same boundary
    ``import_locations`` draws.

    Returns 404 unless the request user is a superuser.
    """
    _require_edit_map_admin(request)
    return JsonResponse(
        {
            "locations": [
                _location_payload(location) for location in _curated_locations()
            ],
            "resorts": [
                {
                    "id": row["pk"],
                    "name": row["name"],
                    "region_id": row["region__region_id"],
                    "region_name": row["region__name"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                }
                for row in Resort.objects.order_by("name", "pk").values(
                    "pk",
                    "name",
                    "region__region_id",
                    "region__name",
                    "latitude",
                    "longitude",
                )
            ],
            "kinds": [
                {"value": value, "label": label}
                for value, label in Location.KIND.choices
            ],
            "roles": [
                {"value": value, "label": label}
                for value, label in ResortLocation.ROLE.choices
            ],
        }
    )


def _parse_location_identity(
    payload: dict[str, Any],
) -> JsonResponse | tuple[str, str]:
    """Validate the ``name``/``kind`` pair of a location write.

    ``name`` is required for both write endpoints, because naming a
    location is what makes it curated — a nameless row is the anonymous
    kind this editor does not manage. ``kind`` is optional and may be
    blank, matching the model and the sheet, but a value that is not a
    ``Location.KIND`` is rejected rather than silently blanked: a typo
    resolving to "" would strip a summit of its classification with no
    signal.

    Returns either the 400 response to send or the parsed
    ``(name, kind)``; the caller discriminates with ``isinstance``.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        return JsonResponse(
            {"error": "invalid_identity", "detail": "name is required"},
            status=400,
        )
    kind = str(payload.get("kind") or "").strip().upper()
    if kind and kind not in Location.KIND.values:
        return JsonResponse(
            {
                "error": "invalid_kind",
                "detail": (
                    f"{kind!r} is not a location kind "
                    f"(expected one of: {', '.join(Location.KIND.values)}, "
                    "or blank)"
                ),
            },
            status=400,
        )
    return name, kind


def _coerce_row_id(value: Any) -> int:
    """Return ``value`` as a positive row id, or 0 when it is not one.

    The panel's resort picker is a ``<select>``, whose value reaches the
    wire as a string, so an id arrives as either ``12`` or ``"12"`` and
    both are the same request. Anything else — null, a name, a float — is
    0, which no row has, so the caller's ordinary "no such resort" 400
    covers a malformed id without a second error path for it.

    Args:
        value: The raw JSON value.

    Returns:
        The parsed id, or 0.

    """
    try:
        parsed = int(str(value))
    except ValueError, TypeError:
        return 0
    return parsed if parsed > 0 else 0


def _parse_link_fields(
    payload: dict[str, Any],
) -> JsonResponse | tuple[Resort, str, bool]:
    """Validate the ``resort_id``/``role``/``is_primary`` trio of a link.

    Unlike ``kind``, ``role`` is required: a link that does not say what
    the location is to the resort carries nothing the two foreign keys do
    not already say.

    Returns either the 400 response to send or the parsed
    ``(resort, role, is_primary)``; the caller discriminates with
    ``isinstance``.
    """
    raw_resort_id = payload.get("resort_id")
    resort_id = _coerce_row_id(raw_resort_id)
    resort = Resort.objects.filter(pk=resort_id).first() if resort_id else None
    if resort is None:
        return JsonResponse(
            {
                "error": "unknown_resort",
                "detail": f"No resort with id {raw_resort_id!r}.",
            },
            status=400,
        )
    role = str(payload.get("role") or "").strip().upper()
    if role not in ResortLocation.ROLE.values:
        return JsonResponse(
            {
                "error": "invalid_role",
                "detail": (
                    f"{role!r} is not a link role "
                    f"(expected one of: {', '.join(ResortLocation.ROLE.values)})"
                ),
            },
            status=400,
        )
    return resort, role, bool(payload.get("is_primary"))


def _link_or_error(
    location: Location, resort: Resort, role: str, is_primary: bool
) -> JsonResponse | ResortLocation:
    """Create one link, turning the uniqueness clash into a readable 400.

    ``ResortLocation`` is unique on ``(resort, location)``, so linking a
    location to a resort it already reaches is an ``IntegrityError`` —
    which would surface as a 500 and read as a broken editor rather than
    as "you already did that". It is a routine thing for an operator to
    try, because from the panel the link they are about to add and the
    one already listed look the same until they read the row.

    Returns either the 400 response to send or the saved link; the caller
    discriminates with ``isinstance``.
    """
    try:
        # The INSERT takes its own savepoint: a caught IntegrityError
        # leaves the surrounding transaction unusable otherwise, so
        # without this the 400 below could not be built — every query
        # after it, including the one rendering the error, would fail.
        with transaction.atomic():
            return ResortLocation.objects.create(
                resort=resort,
                location=location,
                role=role,
                is_primary=is_primary,
            )
    except IntegrityError:
        return JsonResponse(
            {
                "error": "duplicate_link",
                "detail": (
                    f"{resort.name} is already linked to {location.name} — "
                    "change the existing link's role rather than adding a "
                    "second one."
                ),
            },
            status=400,
        )


@require_POST
def edit_location_create(request: HttpRequest) -> JsonResponse:
    """Mint a location from a placed pin and link it to one resort.

    Request body (JSON)::

        {
          "name": "Mont Fort",
          "kind": "PEAK",            # optional, may be blank
          "latitude": <float>,
          "longitude": <float>,
          "resort_id": <int>,
          "role": "TOP",
          "is_primary": false        # optional, defaults false
        }

    A location and its first link are created together, in one
    transaction. Curating a summit nobody's resort reaches is not a thing
    the estate is for, and a location with no links is invisible to every
    surface that reads it — so the endpoint asks for the resort up front
    rather than leaving an orphan behind on a half-finished session.

    Subsequent resorts are attached with :func:`edit_location_link`,
    which is what keeps Mont Fort one row rather than four.

    The row lives only in this environment's database until
    ``manage.py dump_locations_sheets --commit`` writes it back to
    ``apps/locations/data/``; until then the next ``import_locations``
    reconciliation would delete it.

    Errors:
        404 — request user is not a superuser.
        400 — invalid JSON; a blank ``name`` (``invalid_identity``); an
              unrecognised ``kind``; missing or non-float lat/lon;
              coordinates outside the Swiss bounding box; an unknown
              ``resort_id``; a missing or unrecognised ``role``.
    """
    _require_edit_map_admin(request)

    body_error, payload = _parse_edit_body(request)
    if body_error is not None:
        return body_error

    identity = _parse_location_identity(payload)
    if isinstance(identity, JsonResponse):
        return identity
    name, kind = identity

    coords = _parse_edit_coords(payload)
    if isinstance(coords, JsonResponse):
        return coords
    lat, lon = coords

    link_fields = _parse_link_fields(payload)
    if isinstance(link_fields, JsonResponse):
        return link_fields
    resort, role, is_primary = link_fields

    with transaction.atomic():
        location = Location.objects.create(
            name=name,
            kind=kind,
            latitude=round(lat, _LOCATION_COORD_DECIMALS),
            longitude=round(lon, _LOCATION_COORD_DECIMALS),
        )
        link = _link_or_error(location, resort, role, is_primary)
        if isinstance(link, JsonResponse):
            # Unreachable in practice — the location was created a line
            # ago, so nothing can already be linked to it — but rolling
            # back is the only correct answer if it ever is, and a bare
            # location with no link is exactly the orphan this endpoint
            # exists to avoid.
            transaction.set_rollback(True)
            return link

    # SNOW-1226: its height, then today's forecast, so the resort panel has
    # both before the next scheduled run. After the block, on commit.
    enqueue_location_prime(location)

    # By row id, not coordinates — see the note in edit_location_save.
    logger.info(
        "edit_location_create: created location id=%s (%s) for %s",
        location.pk,
        location.name,
        resort.name,
    )
    return JsonResponse(_location_payload(_reload_with_links(location)), status=201)


@require_POST
def edit_location_save(request: HttpRequest, short_id: str) -> JsonResponse:
    """Move, rename or re-classify an existing location.

    Request body (JSON)::

        {"name": "Mont Fort", "kind": "PEAK",
         "latitude": <float>, "longitude": <float>}

    Editing the location edits it for **every** resort linked to it —
    that is what sharing one row means, and it is the point: a summit
    re-pinned once is re-pinned for all four resorts that reach it. The
    links themselves are untouched here; each resort's ``role`` is its
    own and is changed by unlinking and re-linking.

    **Moving the pin** clears ``elevation_m``. It is resolved from the
    *old* coordinate, so leaving it would leave the location claiming a
    height from where it used to be — and it is a stale elevation that
    makes a mis-pinned summit look plausible. The save then enqueues
    ``enqueue_location_prime`` (SNOW-1226), which resolves the new height
    and re-fetches today's forecast for the new spot.

    **Not** moving it leaves all three columns exactly as they were, and
    that distinction is load-bearing rather than an optimisation: a
    rename is not a re-placement, and treating it as one would both
    discard a resolved height and rewrite a coordinate the admin may have
    set at full precision (see the comparison below).

    Scoped to ``Location.objects.named()``. The anonymous rows minted
    from favourites and observations live in the same table but are user
    data, and this editor curates the estate the sheets own — giving one
    a name here would silently move it across that boundary.

    Errors:
        404 — request user is not a superuser, or no such *curated*
              location.
        400 — invalid JSON; a blank ``name``; an unrecognised ``kind``;
              missing, non-float or out-of-bbox coordinates.
    """
    _require_edit_map_admin(request)
    location = get_object_or_404(Location.objects.named(), short_id=short_id)

    body_error, payload = _parse_edit_body(request)
    if body_error is not None:
        return body_error

    identity = _parse_location_identity(payload)
    if isinstance(identity, JsonResponse):
        return identity
    name, kind = identity

    coords = _parse_edit_coords(payload)
    if isinstance(coords, JsonResponse):
        return coords
    lat, lon = coords

    lat = round(lat, _LOCATION_COORD_DECIMALS)
    lon = round(lon, _LOCATION_COORD_DECIMALS)
    # Compare like with like. ``latitude`` is a plain FloatField and the
    # admin — a live write path this editor does not own — imposes no
    # rounding, so a stored coordinate may carry more decimals than the
    # panel can send back. Comparing the rounded incoming value against
    # the raw stored one made ``moved`` true for every such row, and a
    # save that only renamed it then wiped an elevation the pin had not
    # moved away from.
    moved = (lat, lon) != (
        round(location.latitude, _LOCATION_COORD_DECIMALS),
        round(location.longitude, _LOCATION_COORD_DECIMALS),
    )

    location.name = name
    location.kind = kind
    update_fields = ["name", "kind", "updated_at"]
    if moved:
        # The coordinate is written only when it actually changed. A
        # rename must not rewrite it: doing so would silently truncate a
        # full-precision coordinate set in the admin to the five decimals
        # the sheet carries, which is a data edit nobody asked for.
        # SNOW-845: Location.move_to clears the height and the three word
        # address, both derived from the old coordinate. ``moved`` is this
        # editor's own rounded comparison, passed so the rule is applied on
        # exactly the condition above.
        update_fields += location.move_to(lat, lon, moved=True)
    location.save(update_fields=update_fields)
    # SNOW-1226: a moved pin has just lost its height and holds a forecast
    # for where it used to be; an unmoved one only fills what is missing.
    enqueue_location_prime(location, moved=moved)

    # Logged by row id, never by raw coordinates (SNOW-718). A coordinate
    # is a precise location
    # and the id identifies the row anyway, so this is both safer and more
    # useful; whether the pin moved is the fact worth having in the log.
    logger.info(
        "edit_location_save: saved location id=%s (%s), moved=%s",
        location.pk,
        location.name,
        moved,
    )
    return JsonResponse(_location_payload(_reload_with_links(location)))


@require_POST
def edit_location_link(request: HttpRequest, short_id: str) -> JsonResponse:
    """Attach an existing location to another resort.

    Request body (JSON)::

        {"resort_id": <int>, "role": "TOP", "is_primary": false}

    The endpoint the estate is worth having for. Mont Fort is one row
    that four resorts reference; without this the operator's only way to
    give Nendaz its top station would be to place a second pin on the
    same summit, and the duplication the model was built to remove would
    come back through the editor that was meant to curate it.

    No coordinate is sent, and none is accepted: the location already
    knows where it is, and this endpoint says only that another resort
    reaches it. ``role`` is per-link, so the same point can be one
    resort's top and another's mid-station.

    Scoped to ``Location.objects.named()``, and that scope is an
    invariant rather than a convenience: ``dump_locations_sheets`` emits
    only curated locations, so a link hung on an anonymous row is dropped
    from the links sheet and cannot survive a round trip. Refusing to
    create one here is how this editor keeps that from happening — see
    the note in ``dump_locations_sheets``'s module docstring.

    Errors:
        404 — request user is not a superuser, or no such *curated*
              location.
        400 — invalid JSON; an unknown ``resort_id``; a missing or
              unrecognised ``role``; a resort already linked to this
              location (``duplicate_link``).
    """
    _require_edit_map_admin(request)
    location = get_object_or_404(Location.objects.named(), short_id=short_id)

    body_error, payload = _parse_edit_body(request)
    if body_error is not None:
        return body_error

    link_fields = _parse_link_fields(payload)
    if isinstance(link_fields, JsonResponse):
        return link_fields
    resort, role, is_primary = link_fields

    link = _link_or_error(location, resort, role, is_primary)
    if isinstance(link, JsonResponse):
        return link
    # SNOW-1226: a link can be what makes a location active, so it may
    # have no forecast yet.
    enqueue_location_prime(location)

    logger.info(
        "edit_location_link: linked %s to %s as %s",
        location.name,
        resort.name,
        role,
    )
    return JsonResponse(_location_payload(_reload_with_links(location)), status=201)


@require_POST
def edit_location_unlink(request: HttpRequest, link_uuid: uuid.UUID) -> JsonResponse:
    """Remove one resort's link to a location, keeping the location.

    The inverse of :func:`edit_location_link`, and deliberately not a
    delete: Mont Fort outlives Nendaz's interest in it, and the model
    says so — ``ResortLocation.location`` is ``PROTECT``, so the row
    could not be removed here even if this endpoint tried.

    A location whose last link goes is left in place rather than swept
    up. It is still a curated place, it is still in the sheet, and
    deleting the operator's work as a side effect of correcting one
    resort's links is not a thing a staff tool should do quietly.

    Errors:
        404 — request user is not a superuser, or no such link.
    """
    _require_edit_map_admin(request)
    link = get_object_or_404(
        ResortLocation.objects.select_related("resort", "location"), uuid=link_uuid
    )
    location = link.location
    logger.info(
        "edit_location_unlink: unlinked %s from %s",
        location.name,
        link.resort.name,
    )
    link.delete()
    return JsonResponse(_location_payload(_reload_with_links(location)))
