"""
apps/routes/views.py — HTMX endpoints + GeoJSON layer for the routes application.

Four HTMX-only fragment views backing the GPX upload half of SNOW-684:

- ``route_create`` (POST) — multipart upload of a ``.gpx``; parses it,
  stores a Route, returns the route-row partial. The uploaded file is read
  into memory, parsed and discarded — nothing is written to disk (see
  ``docs/decisions/gpx-uploads-are-parsed-not-stored.md``).
- ``route_rename`` (POST) — owner-checked rename, returns the updated
  partial.
- ``route_delete`` (POST) — owner-checked deletion.
- ``route_list`` (GET) — SNOW-686: the requesting user's own routes, for
  the map sheet's routes panel.

plus one plain-JSON endpoint:

- ``routes_geojson`` (GET) — SNOW-687: a ``LineString`` FeatureCollection of
  the requesting user's own routes, for the map's routes layer. Not
  ``@require_htmx`` — consumed by a JS ``fetch()`` call, not an HTMX swap.

All of them are authentication-gated (403 for anonymous users) and
owner-scoped via ``Route.objects.for_user()`` — another user's uuid returns
404, never 403, so a probing request can't distinguish "not yours" from
"doesn't exist" (no existence oracle). This mirrors
``apps.favourites.views``, which is the reference implementation for the
whole shape.

``route_create`` additionally applies django-ratelimit (10/m, keyed on
``user`` since these endpoints are auth-only) and returns 429 when the
limit is exceeded.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.core.decorators import require_htmx
from apps.core.freshness import apply_freshness_headers
from apps.routes.constants import ROUTE_LIST_MAP_VARIANT
from apps.routes.models import Route
from apps.routes.services.gpx import GPXParseError
from apps.routes.services.routes import RouteLimitReached, create_route, delete_route

logger = logging.getLogger(__name__)

# SNOW-686: route_list serves two row shapes. The ``variant`` query
# parameter selects a template out of this fixed map — an unknown (or
# absent) value falls back to the default, so nothing a caller sends ever
# reaches a template path. Mirrors ``apps.favourites.views``'s own pair.
_LIST_TEMPLATE_DEFAULT = "routes/partials/_route_list.html"
_LIST_TEMPLATES = {
    ROUTE_LIST_MAP_VARIANT: "routes/partials/_route_list_map.html",
}

# Must match Route.name's max_length. Checked here so an over-length
# submission is turned into a handled 400 instead of a DB DataError (500).
_NAME_MAX_LENGTH = 100

# The multipart field the upload arrives in.
_UPLOAD_FIELD = "file"

# The single response body for every parse failure. Deliberately fixed: it
# says what the user can act on without echoing any part of the parser's
# exception back to them (CodeQL py/stack-trace-exposure).
_GPX_REJECTED_MESSAGE = (
    "That file could not be read as GPX. It must be a valid .gpx file "
    "containing a track or a route."
)


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def route_create(request: HttpRequest) -> HttpResponse:
    """Ingest an uploaded GPX file and return the route-row partial.

    Validates the request in cheapest-first order — auth, rate limit,
    presence, size, then parse — so an oversized or absent file never
    reaches the XML parser.

    ``settings.ROUTE_UPLOAD_MAX_BYTES`` is enforced against the uploaded
    file's reported size and answered with 413, which is what that status
    is for; note Django's own ``DATA_UPLOAD_MAX_MEMORY_SIZE`` governs the
    request body as a whole and is a separate, larger backstop.

    When the user has reached ``settings.ROUTES_MAX_PER_USER``, renders
    ``_route_limit.html`` at HTTP 409 rather than creating a row — a cap is
    a permanent failure (only deleting a route clears it), so 409 and not
    the transient 429, matching ``favourite_create``.

    Errors:
        400 — non-HTMX request; no file supplied; unparseable GPX.
        403 — anonymous request.
        409 — the user has reached ``settings.ROUTES_MAX_PER_USER``.
        413 — the upload exceeds ``settings.ROUTE_UPLOAD_MAX_BYTES``.
        429 — rate limit exceeded (> 10 uploads/min per user).

    Args:
        request: The incoming HTMX multipart POST request.

    Returns:
        The rendered route-row or limit-reached partial, or an error
        response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before uploading another route.",
            status=429,
        )

    upload = request.FILES.get(_UPLOAD_FIELD)
    if upload is None:
        return HttpResponse("A .gpx file is required.", status=400)

    if upload.size is not None and upload.size > settings.ROUTE_UPLOAD_MAX_BYTES:
        logger.info(
            "Route upload rejected: user=%s size=%s exceeds %s bytes",
            request.user.pk,
            upload.size,
            settings.ROUTE_UPLOAD_MAX_BYTES,
        )
        return HttpResponse(
            f"That file is too large — the limit is "
            f"{settings.ROUTE_UPLOAD_MAX_BYTES} bytes.",
            status=413,
        )

    try:
        route = create_route(
            request.user, upload.read(), source_filename=upload.name or ""
        )
    except GPXParseError as exc:
        # The parser's own message is logged, never returned. It carries
        # internals a caller has no use for — expat's line/column detail,
        # and for a rejected entity payload the attacker's own system_id
        # reflected back — so the response is a fixed string and the
        # diagnosis lives in the log.
        logger.info("Route upload rejected: user=%s %s", request.user.pk, exc)
        return HttpResponse(_GPX_REJECTED_MESSAGE, status=400)
    except RouteLimitReached:
        logger.info("Route create blocked: user=%s hit the cap", request.user.pk)
        return render(request, "routes/partials/_route_limit.html", {}, status=409)

    return render(request, "routes/partials/_route.html", {"route": route})


@require_htmx
@require_POST
def route_rename(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Rename an existing Route owned by the requesting user.

    Args:
        request: The incoming HTMX POST request. Expects a ``name`` field,
            rejected with 400 if it exceeds ``Route.name``'s max_length.
        uuid: The Route's uuid, from the URL.

    Returns:
        The rendered updated route-row partial, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        route = Route.objects.for_user(request.user).get(uuid=uuid)
    except Route.DoesNotExist:
        return HttpResponse("Route not found.", status=404)

    name = request.POST.get("name", "")
    if len(name) > _NAME_MAX_LENGTH:
        return HttpResponse(
            f"name must be at most {_NAME_MAX_LENGTH} characters.", status=400
        )

    route.name = name
    # updated_at is auto_now — it must be in update_fields or the DB column
    # is left stale, since save(update_fields=...) skips every field not
    # explicitly listed (auto_now is applied in Python, not by the DB).
    route.save(update_fields=["name", "updated_at"])

    return render(request, "routes/partials/_route.html", {"route": route})


@require_htmx
@require_POST
def route_delete(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Delete an existing Route owned by the requesting user.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Route's uuid, from the URL.

    Returns:
        An empty 200 response on success, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        delete_route(request.user, uuid)
    except Route.DoesNotExist:
        return HttpResponse("Route not found.", status=404)

    return HttpResponse("")


@require_htmx
@require_GET
def route_list(request: HttpRequest) -> HttpResponse:
    """Render the requesting user's own routes list partial.

    Backs the map sheet's routes panel (SNOW-686), which lazy-loads this
    endpoint over HTMX every time it opens rather than holding rows across
    an open/close cycle — so a row deleted or renamed in one session can
    never survive into the next.

    The ``variant`` query parameter picks the row shape out of a fixed map:
    ``?variant=map`` gets ``_route_list_map.html`` (the lean
    ``includes/_ugc_panel_row.html`` row the panel wants), anything else —
    including no parameter at all — falls back to ``_route_list.html`` and
    the always-visible rename field ``_route.html`` carries. The value
    selects a template out of a dict; it is never interpolated into a
    template path.

    No freshness headers and no offline-cache sidecar, both of which
    ``favourite_list`` carries. A route has no safety-critical constituent
    to go stale (it is the user's own geometry, not a danger rating), and
    caching routes for offline reads belongs to the map layer — SNOW-687's
    ``routes_geojson`` below, write-through cached by
    static/js/map_overlay_offline_cache.js. The panel itself still says so
    via its own failed-load line.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_route_list.html`` (or ``_route_list_map.html`` for
        ``?variant=map``), or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    routes = list(Route.objects.for_user(request.user))

    return render(
        request,
        _LIST_TEMPLATES.get(request.GET.get("variant", ""), _LIST_TEMPLATE_DEFAULT),
        {"routes": routes},
    )


# ---------------------------------------------------------------------------
# routes_geojson (SNOW-687) — the map layer's data
# ---------------------------------------------------------------------------


@require_GET
# Per-user data, so ``private`` and ``no-store``: this payload must never be
# held by an intermediate cache and handed to a different visitor. Mirrors
# ``favourites_geojson``'s header and ``community_reports_geojson``'s
# decorator.
@cache_control(private=True, no_store=True)
def routes_geojson(request: HttpRequest) -> JsonResponse:
    """Return a FeatureCollection of the requesting user's own routes.

    Backs the map's routes line layer (SNOW-687). One ``LineString``
    Feature per route, whose ``coordinates`` are ``Route.points``
    **verbatim** — the model already stores ``[lon, lat, ele]`` in GeoJSON
    axis order (RFC 7946), already simplified at ingest, so there is no
    per-render transform and no chance of an axis swap creeping in between
    the two representations.

    Properties per feature: ``uuid``, ``name``, ``distance_m``,
    ``ascent_m``, ``descent_m`` and ``bounds``. ``ascent_m`` and
    ``descent_m`` are passed through **as stored**, including ``None`` —
    ``Route``'s own docstring is explicit that null means "the source file
    carried no elevation data", not "flat", and the client omits those
    figures entirely rather than rendering a zero for an unknown.
    ``bounds`` rides on the feature so a tap can fit the viewport to the
    route from the payload the map already holds, offline included.

    The per-point elevation the popup's profile chart is drawn from needs
    no property of its own: it is already the third ordinate of every
    coordinate in ``geometry``, which RFC 7946 allows and MapLibre
    ignores. ``static/js/elevation_profile_core.js`` reads it from there.

    Not ``@require_htmx`` — consumed by a JS ``fetch()`` call, not an HTMX
    swap. Owner-scoped via ``Route.objects.for_user()``, in **one** query
    however many routes the user has.

    Freshness headers follow ``apps.public.api.community_reports_geojson``
    rather than ``favourites_geojson`` (which carries none):
    ``generated_at`` is the newest route's ``updated_at``, falling back to
    "now" for a user with no routes at all, and ``unsafe_after`` is
    ``None`` — a user's own uploaded track is not safety-critical data, so
    the client's freshness state saturates at "stale" and never escalates
    to "unsafe". The default 24h ``max_age`` applies.

    Args:
        request: The incoming GET request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 403 error.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    routes = list(Route.objects.for_user(request.user))

    newest: datetime | None = None
    features: list[dict[str, Any]] = []
    for route in routes:
        if newest is None or route.updated_at > newest:
            newest = route.updated_at
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    # Stored in GeoJSON axis order already — see the note
                    # above and Route.points' own help_text.
                    "type": "LineString",
                    "coordinates": route.points,
                },
                "properties": {
                    "uuid": str(route.uuid),
                    "name": route.name,
                    "distance_m": route.distance_m,
                    # None passes straight through: "unknown", not zero.
                    "ascent_m": route.ascent_m,
                    "descent_m": route.descent_m,
                    "bounds": route.bounds,
                },
            }
        )

    response = JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
    apply_freshness_headers(
        response,
        generated_at=newest or timezone.now(),
        unsafe_after=None,
    )
    return response
