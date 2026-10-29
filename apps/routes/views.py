"""
apps/routes/views.py — HTMX endpoints + GeoJSON layer for the routes application.

Five HTMX-only fragment views backing the GPX upload half of SNOW-684 and
the sharing half of SNOW-764:

- ``route_create`` (POST) — multipart upload of a ``.gpx``; parses it,
  stores a Route, returns the route-row partial. The uploaded file is read
  into memory, parsed and discarded — nothing is written to disk (see
  ``docs/decisions/gpx-uploads-are-parsed-not-stored.md``).
- ``route_rename`` (POST) — owner-checked rename, returns the updated
  partial.
- ``route_delete`` (POST) — owner-checked deletion.
- ``route_list`` (GET) — SNOW-686: the requesting user's own routes, for
  the map sheet's routes panel.
- ``route_share_claim`` (POST) — SNOW-764: takes a COPY of a shared route
  onto the requesting user's account and returns the new row.

plus three navigation/JSON endpoints outside the ``partials/`` prefix:

- ``routes_geojson`` (GET) — SNOW-687: a ``LineString`` FeatureCollection of
  the requesting user's own routes, for the map's routes layer. Not
  ``@require_htmx`` — consumed by a JS ``fetch()`` call, not an HTMX swap.
- ``route_share_redirect`` (GET/HEAD) — SNOW-764: follows a share link,
  records the token in the session and 302s to the map. A navigation, not
  a fragment, which is why it sits outside ``partials/``.

plus one JSON endpoint the owner's Share button calls:

- ``route_share_create`` (POST) — SNOW-764: mints a share link for one of
  the requesting user's own routes and returns its absolute URL.

SNOW-1255 adds the drawing half, behind the ``route_drawing`` waffle flag
(404 for anyone the flag does not cover — the flag sits on every endpoint
that MAKES a drawn route, and none of the surfaces that read one):

- ``route_draw_preview`` (POST) — the draft's waypoints in, the same
  Feature ``routes_geojson`` builds out, sampled on the spot so the panel
  can draw the profile and legs as the line grows. Saves nothing, needs no
  account (drawing is free; saving needs one).
- ``route_create_drawn`` (POST) — stores the waypoints as a Route.
- ``route_gpx`` (GET) — any of the owner's routes as a ``.gpx`` download.

There is no full page here. ``my_routes`` (SNOW-713) rendered
``/account/routes/`` as the account area's own list until SNOW-803 found it
to be a second rendering of the map's routes sheet; the view is gone and the
URL is a permanent redirect to ``/map/?panel=routes``
(``docs/decisions/two-documents-and-a-map.md``). Authentication remains the
only gate since SNOW-724 retired the ``routes`` rollout flag.

All the fragment endpoints are authentication-gated (403 for anonymous) and
owner-scoped via ``Route.objects.for_user()`` — another user's uuid returns
404, never 403, so a probing request can't distinguish "not yours" from
"doesn't exist" (no existence oracle). This mirrors
``apps.favourites.views``, which is the reference implementation for the
whole shape.

Every mutating endpoint here applies django-ratelimit and returns 429 when
the budget is spent: ``route_create`` at 10/m, ``route_share_create`` and
``route_share_claim`` at 20/m — all keyed on ``user``, since those three are
auth-only — and ``route_share_redirect`` at 30/h keyed on (token, IP),
because it is the one endpoint an anonymous stranger can reach. The rates
themselves are named and justified beside their constants below.

TWO ENDPOINTS ARE DELIBERATELY WIDER THAN OWNER-SCOPED (SNOW-764).
``route_list`` and ``routes_geojson`` answer for an ANONYMOUS request when
— and only when — that request's session holds pending share tokens. A
visitor who followed a share link has been handed something to look at
before they have an account to hang it on, and the two surfaces that draw
a route are the panel and the map layer; refusing both until sign-in would
mean the link lands on a map showing nothing.

Three properties keep that widening honest:

* it is keyed on the SESSION, not on a URL parameter, so nothing a caller
  can type reaches either endpoint's pending branch — the token had to
  come through ``route_share_redirect`` first;
* a pending feature carries the share TOKEN and never the route's
  ``uuid``. A non-owner must not learn an identifier the owner-scoped
  rename and delete endpoints are addressed by;
* both short-circuit on an empty session list before any query, so a
  visitor who never followed a share link pays nothing — which is what
  keeps the homepage's query count where it was.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseGone,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit
from waffle import flag_is_active

from apps.core.decorators import require_htmx
from apps.core.freshness import apply_freshness_headers
from apps.core.http import client_ip, is_top_level_navigation
from apps.routes.models import Route, RouteShare, SkiTour
from apps.routes.services.drawn import (
    DrawnRouteError,
    clean_name,
    clean_waypoints,
    draft_route,
)
from apps.routes.services.estimate import estimate_duration_s
from apps.routes.services.example import example_share
from apps.routes.services.gpx import GPXParseError
from apps.routes.services.gpx_export import build_gpx, gpx_filename
from apps.routes.services.leg_wire import wire_legs
from apps.routes.services.routes import (
    RouteLimitReached,
    create_drawn_route,
    create_route,
    delete_route,
)
from apps.routes.services.shares import (
    RouteShareTokenCollision,
    add_pending_token,
    claim_route_share,
    create_route_share,
    drop_pending_token,
    pending_shares,
    pending_tokens,
)
from apps.routes.services.ski_tours import (
    draft_tour_route,
    is_descent,
    localised,
    one_leg,
    sac_time,
    save_ski_tour,
    summits,
    tour_key,
    tour_points,
    tour_properties,
    tour_slope_samples,
)
from apps.routes.services.slope_segments import build_slope_samples
from apps.routes.services.slope_summary import summarise_record
from apps.routes.services.slope_wire import compact_slope
from apps.routes.services.terrain_detail import terrain_detail
from apps.routes.services.terrain_heights import terrain_points

logger = logging.getLogger(__name__)

# Stand-in uuid for reversing a __UUID__-templated rename URL. Mirrors
# ``apps.favourites.views._DUMMY_UUID``.
_DUMMY_UUID = UUID(int=0)


def _rename_url_template() -> str:
    """Return routes:rename with ``__UUID__`` where the uuid goes.

    Handed to the account page's template so ``static/js/account_routes.js``
    can build one row's rename URL at commit time, from the uuid riding on
    that row's own pencil — the module must not know how this project spells
    its URLs. The same trick ``apps.favourites.views._rename_url_template``
    uses for the account favourites list, and
    ``apps.public.views`` for the map panel's own copy.

    Reversed per call rather than once at import — this module is imported by
    ``apps.routes.urls``, so reversing at import time would ask the URLconf to
    resolve itself while it is still being built.

    Returns:
        The rename URL with the uuid replaced by ``__UUID__``.

    """
    return reverse("routes:rename", args=[_DUMMY_UUID]).replace(
        str(_DUMMY_UUID), "__UUID__"
    )


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

# The share-link follow's rate-limit budget, keyed on (token, IP). Mirrors
# ``apps.public.views.SHARE_CLICK_RATE`` and its reasoning: a real visitor
# re-following their own link never approaches it, while a scanner walking
# the token space cannot grow the session table without bound.
_SHARE_FOLLOW_RATE = "30/h"

# The owner's Share button's budget. Keyed on ``user`` because the endpoint
# is auth-only, matching ``route_create``'s own limiter. Each call mints a
# row, so the cap is what stops a scripted client filling the table.
_SHARE_CREATE_RATE = "20/m"

# The claim's budget. Keyed on ``user`` for the same reason the two above
# are keyed the way they are — this endpoint is auth-only, so the account
# is the bucket. Each successful call writes a Route, and the per-user cap
# bounds how many can ever land; this bounds how fast a scripted client can
# spend that cap and how hard it can hammer the locked cap re-check. Looser
# than ``route_create``'s 10/m because a claim carries no upload and no
# parse, and a recipient claiming several routes they were sent in one
# message should not be told to wait.
_SHARE_CLAIM_RATE = "20/m"

# SNOW-1255: the drawing flag. Read on the endpoints that MAKE a drawn route
# (the preview and the save) and on the download — never on a surface that
# READS one, because a drawn route that is shared or opened on a trip must
# keep working for a viewer the flag does not cover (docs/feature-flags.md,
# the ``route_sharing`` lesson).
ROUTE_DRAWING_FLAG = "route_drawing"

# The preview's budget, keyed on the client IP because drawing needs no
# account. One request per waypoint placed, debounced on the client so a
# burst of taps costs one; a person drawing quickly places a point every
# second or two, so 60/m is a generous ceiling for a human and a hard one
# for a script spending terrain-tile reads on the server's behalf.
_DRAW_PREVIEW_RATE = "60/m"

# The save's budget: the same as an upload's, because each one writes a row
# against the same per-user cap.
_DRAW_CREATE_RATE = "10/m"

# Fixed reply for waypoints that cannot make a route. The validation message
# itself is logged, never returned — the same rule ``route_create`` keeps for
# a parse error.
_DRAWN_REJECTED_MESSAGE = "Those points could not be read as a route."

# The 410 body for a share link that has stopped working. Deliberately says
# nothing about WHY: expired, revoked and route-deleted are one answer to a
# holder of the link, and distinguishing them would tell a guesser which
# tokens have ever existed.
_SHARE_GONE_BODY = (
    "<html><body><h1>410 Gone</h1>"
    "<p>This route link is no longer available.</p></body></html>"
)


def _share_follow_rate_limit_key(group: str, request: HttpRequest) -> str:
    """Return the rate-limit bucket for a share-link follow: (token, IP).

    The routes twin of ``apps.public.views._share_rate_limit_key``, and
    keyed the same way for the same reason: one visitor re-following their
    own link is unaffected, while a scanner hammering a single link is
    bounded, and a NATed office network does not share one budget across
    unrelated links.

    Args:
        group: The django-ratelimit group name (unused — one group here).
        request: The current HTTP request.

    Returns:
        An opaque bucket key.

    """
    match = request.resolver_match
    token = match.kwargs.get("token", "") if match is not None else ""
    return f"{token}|{client_ip(request)}"


def _pending_shares_for(request: HttpRequest) -> list[RouteShare]:
    """Return this request's pending shares, or nothing when there are none.

    The guard the two widened endpoints share, and the reason neither of
    them costs a visitor who has never followed a share link anything: the
    session read is a dict lookup on an object the auth middleware has
    already loaded, and the database query sits behind it. An empty list
    short-circuits before the query is ever built, which is what keeps the
    homepage's query count where it was.

    Args:
        request: The current HTTP request.

    Returns:
        The claimable shares this session is holding, oldest-followed
        first; empty when there are none.

    """
    if not pending_tokens(request.session):
        return []
    return pending_shares(request.session)


def _route_feature(route: Route, identity: dict[str, Any]) -> dict[str, Any]:
    """Build one GeoJSON LineString Feature for a route.

    Shared by the owned and the pending branches of ``routes_geojson`` so
    one route reads identically whichever side it arrives on — the popup,
    the elevation profile and the fit-to-bounds all read these names, and
    a second copy of this dict is where they would drift apart.

    ``identity`` is the ONE thing that differs: an owned feature carries
    ``uuid``, a pending one carries ``token`` and ``pending``. Passing it
    in rather than branching inside keeps the rule that a non-owner is
    never handed a uuid visible at both call sites.

    Args:
        route: The route to describe.
        identity: The identifying properties merged into ``properties``.

    Returns:
        A GeoJSON Feature dict.

    """
    # SNOW-1043: the heights are the terrain model's wherever the record
    # has them, and the device's elsewhere — see terrain_heights. The
    # profile, the legs and the totals below all read THESE points, so
    # the three cannot disagree about which series they describe.
    points = terrain_points(route.points, route.slope_samples)
    # SNOW-1053: the slope's ``seams`` index into THIS geometry, so the
    # class segments are drawn along the same coordinates as the casing.
    slope = compact_slope(route.slope_samples, coordinates=points)
    # The one rule every surface quotes ascent and descent by — the route
    # row's template reads the same property. None passes straight
    # through: "unknown", not zero.
    ascent_m, descent_m = route.climb
    return {
        "type": "Feature",
        "geometry": {
            # Stored in GeoJSON axis order already — see routes_geojson's
            # own note and Route.points' help_text.
            "type": "LineString",
            "coordinates": points,
        },
        "properties": {
            **identity,
            "name": route.name,
            "distance_m": route.distance_m,
            "ascent_m": ascent_m,
            "descent_m": descent_m,
            # int, not float: a GPX records whole seconds, and the popup
            # renders hours and minutes off this.
            "duration_s": (
                int(duration.total_seconds())
                if (duration := route.duration) is not None
                else None
            ),
            "bounds": route.bounds,
            # SNOW-1255/1258: how the route arrived, and — for a drawn one,
            # which has no recording to time — the planning estimate. Never
            # in ``duration_s``: an estimate must not read as a record.
            "source": route.source,
            **(
                {
                    "estimate_s": estimate_duration_s(
                        route.distance_m, ascent_m, descent_m
                    )
                }
                if route.source in (Route.Source.DRAWN, Route.Source.SKI_TOUR)
                else {}
            ),
            # OMITTED ENTIRELY when the route has never been sampled, not
            # passed through as a null like the two above. The key's
            # presence says whether the route has a slope record at all —
            # the map's refetch after an upload waits on exactly that —
            # and a present-but-null value would answer it wrongly.
            # Unknown-per-segment is expressed inside the value, by a null
            # angle; see compact_slope.
            **({"slope": slope} if slope is not None else {}),
            # SNOW-1018: the route cut at its transitions, for the rail
            # below the map. ``from``/``to`` are segment indices — the
            # index space of ``slope.angles`` — never coordinate indices.
            # Sent for an unsampled route too, in the indices its record
            # will have: legs are a fact about the geometry (see
            # leg_wire). The record is passed only when ``slope`` is sent:
            # a record compact_slope refused gives the client no
            # ``angles``, so the legs must index the stride walk instead.
            # Omitted rather than null when there are none: the home map's
            # ``routes-line`` filter tests the key's PRESENCE to decide
            # whether the leg layers are drawing a route (SNOW-1017), and
            # ``point_from``/``point_to`` slice those legs out of the
            # geometry.
            **(
                {"legs": legs}
                if (
                    legs := wire_legs(
                        points,
                        route.slope_samples if slope is not None else None,
                    )
                )
                else {}
            ),
            # SNOW-961: the same record in figures, and a SEPARATE key
            # from ``slope`` rather than a field inside it. That one's
            # presence answers "is this line already coloured"; this one
            # answers "what does the ground under it amount to", and a
            # reader that conflated them would be one refactor away from
            # drawing an uncoloured line because a summary was missing.
            # Carried for a pending route too — the figures describe the
            # ground, which does not depend on whose route it is.
            **(
                {"terrain": terrain}
                if (terrain := summarise_record(route.slope_samples)) is not None
                else {}
            ),
        },
    }


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def route_create(request: HttpRequest) -> HttpResponse:
    """Ingest an uploaded GPX file and return the route-row partial.

    The row is rendered with ``map_focus=True`` (SNOW-886), which is what
    stamps ``data-row-focus`` with the route's bbox. ``route_list``
    hardcodes the same thing for every row it renders, and for the same
    reason: since SNOW-803 the map sheet is the ONLY surface either
    response reaches, so a row without the attribute is a row whose name
    frames nothing. static/js/routes.js reads the bbox straight back out
    of this response to fit the camera to the track that was just
    uploaded, rather than racing a re-read of the list for it.

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

    return render(
        request, "routes/partials/_route.html", {"route": route, "map_focus": True}
    )


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

    return render(
        request, "routes/partials/_route.html", {"route": route, "map_focus": True}
    )


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

    One template, ``_route_list_map.html``. Until SNOW-803 a ``?variant=``
    parameter chose between it and an account-page sibling; that page is
    gone, the map sheet is this endpoint's only surface, and a parameter
    is ignored.

    No freshness headers and no offline-cache sidecar, both of which
    ``favourite_list`` carries. A route has no safety-critical constituent
    to go stale (it is the user's own geometry, not a danger rating), and
    no sidecar is needed because the panel's offline read (SNOW-950,
    static/js/routes_offline.js) stores this response's rendered body
    verbatim rather than a record per route. The map layer's own copy is
    SNOW-687's ``routes_geojson`` below, write-through cached by
    static/js/map_overlay_offline_cache.js.

    SNOW-764 WIDENED THE ANONYMOUS BRANCH. 403 is still the answer for a
    visitor with nothing pending, which is every visitor who has not
    followed a share link. One who HAS gets the list, holding only the
    pending rows — they have been handed a route to look at and the panel
    is where a route is read, so refusing it until sign-in would leave the
    link landing on a panel that says "sign in" about something they were
    already shown. See this module's header for the three properties that
    keep that widening from being an ownership hole.

    Pending rows render ABOVE owned ones, on both variants. They are the
    reason the panel was opened, they are the only rows carrying an action
    the user has not yet taken, and a share claimed into a list of
    twenty-four would otherwise arrive below the fold.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request holding no pending share.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_route_list_map.html``, or an error response.

    """
    pending = _pending_shares_for(request)

    if not request.user.is_authenticated and not pending:
        return HttpResponse("Authentication required.", status=403)

    routes = (
        list(Route.objects.for_user(request.user))
        if request.user.is_authenticated
        else []
    )

    drawing = flag_is_active(request, ROUTE_DRAWING_FLAG)
    return render(
        request,
        "routes/partials/_route_list_map.html",
        {
            "routes": routes,
            "pending_shares": pending,
            # SNOW-1230: the empty clause offers the example route. Only
            # asked when the clause will render, so a list with rows pays
            # no query for it.
            "example_available": (
                not routes and not pending and example_share() is not None
            ),
            # SNOW-764: whether an owned row draws its Share control. It was
            # a per-surface condition while /account/routes/ rendered the
            # same row with no handler for it; the map panel is the only
            # surface since SNOW-803, and static/js/routes.js wires Share.
            "sharing_enabled": True,
            # SNOW-1260: the row menu's Download GPX, behind the drawing
            # flag while that feature is a prototype.
            "gpx_download_enabled": drawing,
            # SNOW-1278: the empty clause names the three ways to add a
            # route the panel's foot offers while the flag is on.
            "route_drawing_enabled": drawing,
        },
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
    Feature per route, whose ``coordinates`` are the terrain track —
    ``Route.points`` (``[lon, lat, ele]`` in GeoJSON axis order, RFC 7946,
    already simplified at ingest, so no axis swap can creep in) merged with
    the slope record's 25 m boundary points when the record carries model
    heights, every point on the terrain model's height and any run the
    model does not cover rebased onto its datum (SNOW-1043,
    ``apps.routes.services.terrain_heights``). A barometric altimeter
    drifts, and the profile drawn from it drew climbs that never happened.
    The boundary points lie on the stored line, so the drawn line does
    not change, and the legs' ``point_from``/``point_to`` index this list.

    Properties per feature: ``uuid``, ``name``, ``distance_m``,
    ``ascent_m``, ``descent_m``, ``duration_s`` and ``bounds``.
    ``ascent_m`` and ``descent_m`` are summed over those same coordinates
    when the record has model heights, and passed through **as stored**
    otherwise, including ``None`` — ``Route``'s own docstring is explicit
    that null means "the source file carried no elevation data", not
    "flat", and the client omits those figures entirely rather than
    rendering a zero for an unknown.

    ``duration_s`` is DERIVED here rather than sending ``started_at`` and
    ``finished_at`` as a pair (SNOW-750). The popup renders one elapsed
    figure; shipping two ISO strings would push date parsing and a
    timezone question onto the client for a subtraction the server has
    already done. It carries the same null contract as the elevation
    figures — an untimed route sends ``None`` and shows no duration.
    ``bounds`` rides on the feature so a tap can fit the viewport to the
    route from the payload the map already holds, offline included.

    The per-point elevation the popup's profile chart is drawn from needs
    no property of its own: it is already the third ordinate of every
    coordinate in ``geometry``, which RFC 7946 allows and MapLibre
    ignores. ``static/js/elevation_profile_core.js`` reads it from there.

    SNOW-910 ADDS ``slope``, and only to a route that has been sampled —
    the property is absent rather than null otherwise, because its
    presence is what tells the map the route is already being painted by
    the slope layers. Its shape is a COMPACT form of the stored record
    (see ``compact_slope``), not the record itself: the PER-SEGMENT
    aspect and the named unknown reason stay on the server for SNOW-911
    and SNOW-839. What travels beside the angles is DERIVED rather than
    reduced — the no-fall passages (SNOW-964) and the fall-line marks,
    which are a bearing at a point every few hundred metres of steep
    ground rather than an aspect per segment.

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

    SNOW-764 ADDS PENDING FEATURES. A request whose session holds a
    pending share gets that share's route drawn too, whether or not the
    requester is signed in — the deep link lands here, and a map that
    could not draw the shared line would be a link to nothing. A pending
    feature carries the same display fields plus ``token`` and
    ``pending: true``, and DELIBERATELY NO ``uuid``: the rename and delete
    endpoints are addressed by uuid and owner-scoped, so a non-owner must
    never be handed one. ``static/js/map.js`` keys the pending line off
    ``token`` for exactly that reason.

    ``private, no-store`` was already required because the payload varied
    per user; it now varies per SESSION as well, which needs the same
    header and no additional one.

    Args:
        request: The incoming GET request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 403 error.

    """
    pending = _pending_shares_for(request)

    if not request.user.is_authenticated and not pending:
        return JsonResponse({"error": "authentication_required"}, status=403)

    routes = (
        list(Route.objects.for_user(request.user))
        if request.user.is_authenticated
        else []
    )

    newest: datetime | None = None
    features: list[dict[str, Any]] = []
    for route in routes:
        if newest is None or route.updated_at > newest:
            newest = route.updated_at
        features.append(_route_feature(route, {"uuid": str(route.uuid)}))

    for share in pending:
        # ``pending_shares`` only ever returns active shares, so the route
        # is present; the local narrows the nullable FK for mypy.
        shared = share.route
        if shared is None:
            continue
        identity: dict[str, Any] = {"token": share.token, "pending": True}
        if share.is_example:
            # SNOW-1230: the map reads these to mark the example's day and
            # to offer no Save — the example is never claimed.
            identity["example"] = True
            identity["example_date"] = (
                share.example_date.isoformat() if share.example_date else None
            )
        features.append(_route_feature(shared, identity))

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


@require_GET
# Per-user data, as routes.geojson: never held by a shared cache.
@cache_control(private=True, no_store=True)
def route_terrain(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Return one of the requesting user's routes' terrain rows as JSON.

    SNOW-1146. Backs the route panel's terrain data, the figures behind
    the point on the profile, which every user reveals from the point
    header's terrain slope line. Until then the readout was staff only
    and read the staff terrain table (``public:route_terrain``,
    ``?format=json``), which serves any route; this serves the owner's
    own, so the staff page stays staff only. The rows are the same
    ``terrain_detail`` rows, one per segment by index, and carry no
    coordinate — distances, heights and bearings only.

    Not ``@require_htmx`` — a JS ``fetch()`` from static/js/route_rail.js,
    loaded the first time the data is revealed for a route.

    Args:
        request: The incoming GET request.
        uuid: The Route's uuid, from the URL.

    Returns:
        ``{"rows": [...]}`` (empty for a route never sampled), 403 for an
        anonymous caller, 404 for a route the caller does not own.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    try:
        route = Route.objects.for_user(request.user).get(uuid=uuid)
    except Route.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    return JsonResponse(
        {"rows": terrain_detail(route.slope_samples, route.points) or []}
    )


@require_GET
# Per-session data, as routes.geojson's pending branch: never held by a
# shared cache.
@cache_control(private=True, no_store=True)
def route_share_terrain(request: HttpRequest, token: str) -> JsonResponse:
    """Return a pending share's terrain rows as JSON (SNOW-1230).

    The pending-share twin of ``route_terrain``, so the terrain data behind
    the point card opens for a route someone was SENT, signed in or not —
    Hugo's call on SNOW-1230: "terrain data for any shared route". The
    example route is one such share.

    Gated exactly as the two widened endpoints are (this module's header):
    on the SESSION's pending tokens, never on the URL alone. The token in
    the path must already be in this browser's pending list — which only
    ``route_share_redirect`` and ``route_example`` write — and must still
    resolve to a live share. A token typed into the URL by anyone else
    answers 404, so this is not a guessing oracle. The rows carry no
    coordinate (``terrain_detail``), and the share already sends its
    recipient the full slope record they summarise.

    Args:
        request: The incoming GET request.
        token: The share token, from the URL.

    Returns:
        ``{"rows": [...]}``, or 404 for a token this session does not hold
        or a share that is no longer live.

    """
    if token not in pending_tokens(request.session):
        return JsonResponse({"error": "not_found"}, status=404)

    share = (
        RouteShare.objects.active().select_related("route").filter(token=token).first()
    )
    route = share.route if share is not None else None
    if route is None:
        return JsonResponse({"error": "not_found"}, status=404)

    return JsonResponse(
        {"rows": terrain_detail(route.slope_samples, route.points) or []}
    )


# ---------------------------------------------------------------------------
# Sharing (SNOW-764)
# ---------------------------------------------------------------------------


@require_POST
@ratelimit(key="user", rate=_SHARE_CREATE_RATE, block=False)
def route_share_create(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Mint a share link for one of the requesting user's own routes.

    Response (200)::

        {"url": "https://snowdesk.app/routes/s/<token>/"}

    Returns JSON rather than a partial because the caller does not render
    it: ``static/js/share.js`` hands the URL straight to the native share
    sheet (or the clipboard). The same shape ``apps.public.api.share_create``
    returns for a bulletin, so one JS helper serves both.

    NOT ``@require_htmx``. Every other POST in this module is a fragment
    endpoint whose response is swapped into the page; this one's response
    is a string handed to ``navigator.share``, and requiring the header
    would be asserting a contract that is not the one in force.

    Owner-scoped through ``create_route_share``, whose lookup raises
    ``Route.DoesNotExist`` for a uuid that is not this user's — answered
    404 and never 403, so a probing request cannot tell "not yours" from
    "doesn't exist".

    Errors:
        403 — anonymous request.
        404 — the uuid is not this user's route.
        429 — rate limit exceeded.
        500 — a unique token could not be minted (implausible; see
              ``create_route_share``).

    Args:
        request: The incoming POST request.
        uuid: The Route's uuid, from the URL.

    Returns:
        A JsonResponse carrying the absolute share URL.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    try:
        share = create_route_share(request.user, uuid)
    except Route.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    except RouteShareTokenCollision:
        logger.error("route_share_create: token collision, user=%s", request.user.pk)
        return JsonResponse({"error": "token_collision"}, status=500)

    url = request.build_absolute_uri(
        reverse("routes:share_redirect", args=[share.token])
    )
    return JsonResponse({"url": url})


@require_http_methods(["GET", "HEAD"])
@ratelimit(key=_share_follow_rate_limit_key, rate=_SHARE_FOLLOW_RATE, block=False)
def route_share_redirect(request: HttpRequest, token: str) -> HttpResponse:
    """Follow a share link: remember the token and 302 to the map.

    The recipient's entry point, and the only way a token reaches a
    session. It does not claim anything — the recipient may not be signed
    in, and even a signed-in one should see what they are being given
    before it lands on their account. What it does is record the intent
    and put the map in front of them with the route drawn:
    ``/?route_share=<token>``, which ``static/js/map.js`` consumes and
    strips.

    THE SESSION IS WHY THIS WORKS ACROSS SIGN-IN. An anonymous recipient
    signs in from the map, comes back, and the parameter that brought them
    is long gone from the address bar; the session carries the pending
    token through that round trip, so the Save control is still there
    when they return. See
    ``docs/decisions/route-share-pending-claim-in-session.md``.

    No 301, ever. A 301 is cached aggressively and by the browser itself,
    so a second follow of the same link would never reach this view and
    would never re-seat the token in a session that had since expired.
    ``Cache-Control: no-store`` for the same reason, and because the
    response's effect is per-session.

    ONLY A REAL NAVIGATION WRITES TO THE SESSION. Speculative requests
    (HEAD, ``Sec-Purpose: prefetch``/``prerender``) still redirect but
    write NOTHING — a browser prefetching a link in a chat window has not
    been given a route, and writing a session for one would both mis-state
    intent and let a scanner grow the session table by prefetch alone.
    Neither does a PASSIVE SUBRESOURCE load: an ``<img src="…">`` or
    ``<iframe src="…">`` on any page anywhere would otherwise plant a token
    in every visitor's session with no interaction at all. ``Sec-Fetch-Dest``
    tells the two apart and ``apps.core.http.is_top_level_navigation``
    reads it — including why an absent header is allowed through.

    Errors:
        404 — the token matches no share at all.
        410 — the share exists but is expired or its route was deleted.
        429 — rate limit exceeded (30/hour per token+IP).

    Args:
        request: The incoming GET or HEAD request.
        token: The share token, from the URL.

    Returns:
        A 302 to the map, or a 404/410/429.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    try:
        share = RouteShare.objects.select_related("route").get(token=token)
    except RouteShare.DoesNotExist as exc:
        # 404 for a token that never existed, 410 below for one that did.
        # The distinction is safe here and useful: a 410 tells the holder
        # of a real link that it has expired rather than that they
        # mistyped it, and a guesser who reaches 404 has learnt only that
        # a random string is not a token.
        raise Http404("No such route share.") from exc

    if not share.is_live:
        gone = HttpResponseGone(_SHARE_GONE_BODY, content_type="text/html")
        gone["Cache-Control"] = "no-store"
        return gone

    if is_top_level_navigation(request):
        add_pending_token(request.session, token)

    destination = f"{reverse('public:map')}?route_share={token}"
    redir = HttpResponseRedirect(destination)
    redir["Cache-Control"] = "no-store"
    return redir


@require_http_methods(["GET", "HEAD"])
def route_example(request: HttpRequest) -> HttpResponse:
    """Open the example route on the map, on its own day (SNOW-1230).

    The one address for "the example route": the map's welcome card, the
    "?" menu, the routes panel's empty clause and any link on the site or
    off it all point here, so the example can change (``set_example_route``)
    without any of them changing. It is the share link's path with the
    token looked up rather than typed: the token goes into the session as
    a followed share's does — which is what lets the panel, the profile and
    the terrain data read it — and the visitor lands on the map with the
    route framed, the date set to the example's day, and ``example=1``,
    which starts the walkthrough (static/js/map_example_guide.js).

    A fresh browser works: nothing is needed but this request. And the
    session write is as guarded as the share link's (a real navigation
    only — see ``route_share_redirect``), though here it matters less:
    the example is offered to everyone, so planting it grants nothing.

    No rate limit: there is no token to guess, and the response is a
    redirect computed from one indexed row.

    Errors:
        404 — no example route is set.

    Args:
        request: The incoming GET or HEAD request.

    Returns:
        A 302 to the map, or a 404.

    """
    share = example_share()
    if share is None:
        raise Http404("No example route is set.")

    if is_top_level_navigation(request):
        add_pending_token(request.session, share.token)

    query = f"route_share={share.token}&example=1"
    if share.example_date is not None:
        query += f"&d={share.example_date.isoformat()}"
    redir = HttpResponseRedirect(f"{reverse('public:map')}?{query}")
    redir["Cache-Control"] = "no-store"
    return redir


@require_POST
def route_share_dismiss(request: HttpRequest, token: str) -> HttpResponse:
    """Drop a pending share from this browser's session (SNOW-1230).

    Not ``@require_htmx``: a ``fetch()`` from the walkthrough, whose body is
    read by nothing. CSRF-protected like every POST.

    The example walkthrough calls this when it finishes or is exited, so
    the example stops being drawn on the map and listed in the panel —
    "dismissed", in the ticket's word — until it is opened again from the
    welcome card, the "?" menu or the panel. Nothing is deleted: the share
    is untouched and every other browser holding it keeps it.

    Answers for any token, held or not, with the same 204: dropping a token
    a session does not hold is a no-op (``drop_pending_token``), and a
    different answer would tell a caller which tokens a session holds.

    Args:
        request: The incoming POST request.
        token: The share token, from the URL.

    Returns:
        An empty 204.

    """
    # Only a held token is dropped: dropping one that is not held would
    # still write the (empty) list back, and so create a session for a
    # visitor who had none.
    if token in pending_tokens(request.session):
        drop_pending_token(request.session, token)
    return HttpResponse(status=204)


@require_htmx
@require_POST
@ratelimit(key="user", rate=_SHARE_CLAIM_RATE, block=False)
def route_share_claim(request: HttpRequest, token: str) -> HttpResponse:
    """Take a copy of a shared route onto the requesting user's account.

    Answers with ``routes/partials/_route.html`` for the NEW row — the
    same partial ``route_create`` and ``route_rename`` return — so the
    surface that posted here can swap the claimed route into its list as
    an ordinary owned row. It is owned now: the pending row it replaces
    carried a token, and this one carries a uuid, because the claimer owns
    what they claimed.

    The token is dropped from the session on success. The SHARE stays
    claimable — the link is reusable and other people may still follow it
    — what is dropped is this browser's standing intention to claim it,
    which has now been acted on. Leaving it would re-offer Save for a
    route the user already holds.

    An at-cap claim gets the same treatment an at-cap upload does: 409 and
    ``_route_limit.html``, not the transient 429. A cap is a permanent
    failure until the user deletes a route, and the copy against it is the
    same per-user cap under the same lock (see
    ``apps.routes.services.shares.claim_route_share``).

    Errors:
        400 — non-HTMX request.
        403 — anonymous request. A claim needs an account to claim ONTO;
              the map's own Save control sends an anonymous visitor to
              sign-in rather than posting here.
        404 — the share is unknown, expired, or its route has been
              deleted.
        409 — the claimer is at ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded (> 20 claims/min per user).

    Args:
        request: The incoming HTMX POST request.
        token: The share token, from the URL.

    Returns:
        The rendered route-row partial for the new copy, or an error
        response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    # After the auth check, not before: the limiter keys on ``user``, and an
    # anonymous request has no account to bucket. Same order ``route_create``
    # puts its own limiter in, and for the same reason.
    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before saving another route.",
            status=429,
        )

    try:
        route = claim_route_share(request.user, token)
    except RouteShare.DoesNotExist:
        return HttpResponse("Route not found.", status=404)
    except RouteLimitReached:
        logger.info("Route claim blocked: user=%s hit the cap", request.user.pk)
        return render(request, "routes/partials/_route_limit.html", {}, status=409)

    drop_pending_token(request.session, token)

    # ``sharing_enabled`` True because of WHERE this row lands: the only
    # surface that posts here is the map panel (static/js/routes.js), and
    # the row swapped in is an ordinary owned row on the surface whose Share
    # control is wired — the same value ``route_list`` passes.
    return render(
        request,
        "routes/partials/_route.html",
        {"route": route, "sharing_enabled": True},
    )


# ---------------------------------------------------------------------------
# Full-page views
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Drawing a route on the map (SNOW-1255 / SNOW-1257 / SNOW-1260)
# ---------------------------------------------------------------------------


def _draw_preview_rate_limit_key(group: str, request: HttpRequest) -> str:
    """Return the rate-limit bucket for a draft preview: the client IP.

    Args:
        group: The django-ratelimit group name (unused — one group here).
        request: The current HTTP request.

    Returns:
        An opaque bucket key.

    """
    return client_ip(request)


def _read_drawn_body(request: HttpRequest) -> tuple[list[list[float | None]], str]:
    """Decode and validate a drawn route's JSON body.

    Args:
        request: A POST whose body is ``{"points": [[lon, lat], …], "name"?}``.

    Returns:
        ``(points, name)``: the stored-shape points and the cleaned name.

    Raises:
        DrawnRouteError: For a body that is not JSON, not an object, or whose
            points do not make a route.

    """
    try:
        body = json.loads(request.body or b"null")
    except ValueError as exc:
        raise DrawnRouteError("the body is not JSON") from exc
    if not isinstance(body, dict):
        raise DrawnRouteError("the body is not an object")
    return clean_waypoints(body.get("points")), clean_name(body.get("name"))


@require_POST
@ratelimit(key=_draw_preview_rate_limit_key, rate=_DRAW_PREVIEW_RATE, block=False)
def route_draw_preview(request: HttpRequest) -> JsonResponse:
    """Describe a draft route exactly as the map would once it is saved.

    The live profile (SNOW-1257). The draft's waypoints are built into an
    UNSAVED ``Route``, the terrain under them is sampled on the spot by the
    same ``build_slope_samples`` walk the saved row will get, and the answer
    is the Feature ``routes_geojson`` builds for a saved route — terrain
    track, climb, legs, slope classes and, for a drawn route, the time
    estimate. So the panel draws a draft with exactly the code that will
    draw it after Save, and the two cannot disagree.

    Nothing is written. No account is needed: drawing is free, saving is
    not (decided 27 Oct 2026). The flag gates it, the IP-keyed rate limit
    bounds what a script can spend on terrain reads, and
    ``drawn.MAX_DRAWN_DISTANCE_M`` bounds what one request can cost.

    Sampled inline, unlike a saved route's queued task: the caller is
    waiting on exactly this answer, and the tiles a growing draft reads are
    mostly the ones its previous preview has just read. Outside the terrain
    model's coverage the sample comes back None and the Feature carries no
    ``slope`` and a null climb — the client says there is no terrain data
    there rather than drawing a flat profile.

    Errors:
        400 — the body is not a valid set of waypoints.
        404 — the ``route_drawing`` flag is not active for this request.
        429 — rate limit exceeded.

    Args:
        request: The incoming POST request.

    Returns:
        ``{"feature": {...}}``.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")

    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    try:
        points, name = _read_drawn_body(request)
    except DrawnRouteError as exc:
        logger.info("Route draft rejected: %s", exc)
        return JsonResponse({"error": _DRAWN_REJECTED_MESSAGE}, status=400)

    draft = draft_route(points, name=name)
    draft.slope_samples = build_slope_samples(points, "route draft")
    return JsonResponse({"feature": _route_feature(draft, {"draft": True})})


@require_POST
@ratelimit(key="user", rate=_DRAW_CREATE_RATE, block=False)
def route_create_drawn(request: HttpRequest) -> JsonResponse:
    """Store a route drawn on the map (SNOW-1255).

    JSON in, JSON out, and not under ``partials/``: the caller is a plain
    ``fetch`` that frames the new route from the ``bounds`` it gets back,
    and a JSON body is what the mutation queue can replay once saving
    offline lands (SNOW-1262).

    Response (200)::

        {"uuid": "…", "bounds": [w, s, e, n]}

    Errors:
        400 — the body is not a valid set of waypoints.
        403 — anonymous request.
        404 — the ``route_drawing`` flag is not active for this request.
        409 — the user has reached ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming POST request.

    Returns:
        A JsonResponse.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")

    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    try:
        points, name = _read_drawn_body(request)
    except DrawnRouteError as exc:
        logger.info("Drawn route rejected: user=%s %s", request.user.pk, exc)
        return JsonResponse({"error": _DRAWN_REJECTED_MESSAGE}, status=400)

    try:
        route = create_drawn_route(request.user, points, name=name)
    except RouteLimitReached:
        logger.info("Drawn route blocked: user=%s hit the cap", request.user.pk)
        return JsonResponse({"error": "route_limit_reached"}, status=409)

    return JsonResponse({"uuid": str(route.uuid), "bounds": route.bounds})


# The most tours one "N routes pass here" list names. A tap near a hut
# reaches six; the cap bounds what a hand-written query can ask for.
_SKI_TOUR_LIST_MAX = 50


def _ski_tour_or_404(uuid: UUID) -> SkiTour:
    """Return the tour swisstopo keys ``uuid``, or raise 404."""
    try:
        return SkiTour.objects.get(source_uuid=uuid)
    except SkiTour.DoesNotExist as exc:
        raise Http404("Not found.") from exc


@require_GET
@ratelimit(key=_draw_preview_rate_limit_key, rate=_DRAW_PREVIEW_RATE, block=False)
def ski_tour_feature(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Describe one SAC tour as the route panel draws a route (SNOW-1278).

    The tour's line built into an UNSAVED Route and described by the
    Feature ``routes_geojson`` builds for a saved route — so the panel shows
    a tour's profile, legs and estimate with the code that will show them
    once it is saved — plus what SAC says of it (``tour_properties``). The
    Feature carries no ``uuid`` and no ``token``: nothing about it is the
    reader's, so the panel offers none of a route's own actions.

    The terrain is the tour's own record (``tour_slope_samples``). A tour
    never sampled answers at once without it, flagged
    ``terrain_pending``, and the panel asks again with ``?sample=1``, which
    walks the grid (seconds, for a long tour) and keeps the record.

    Errors:
        404 — no such tour, or the ``route_drawing`` flag is off.
        429 — rate limit exceeded.

    Args:
        request: The incoming GET request.
        uuid: The tour's swisstopo uuid (``SkiTour.source_uuid``).

    Returns:
        ``{"feature": {...}}``.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)
    tour = _ski_tour_or_404(uuid)
    sample = request.GET.get("sample") == "1"
    draft = draft_tour_route(tour)
    draft.slope_samples = tour_slope_samples(tour, sample=sample)
    identity: dict[str, Any] = {"tour": True, **tour_properties(tour)}
    if draft.slope_samples is None and not sample:
        identity["terrain_pending"] = True
    feature = _route_feature(draft, identity)
    # SNOW-1304: a tour is one leg, a way up or a way down, drawn whole in
    # that direction's colour, as its list row and its highlight are.
    legs = one_leg(feature["properties"].get("legs"), tour)
    if legs:
        feature["properties"]["legs"] = legs
    return JsonResponse({"feature": feature})


@require_GET
# Never held, as route_terrain: a tour opened before its first sampling
# answers no rows, and a cached empty answer would outlive the sampling
# the panel's ?sample=1 refill triggers moments later.
@cache_control(private=True, no_store=True)
def ski_tour_terrain(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Return one SAC tour's terrain rows as JSON (SNOW-1278).

    The tour twin of ``route_terrain``, so the terrain data behind the point
    card opens for a tour before it is saved, as it does for a route. A
    tour is reference data, so there is no owner to check. The rows come
    from the tour's held record only (``tour_slope_samples`` without
    ``sample``): the panel's ``?sample=1`` refill builds that record, and
    a tour never sampled answers no rows rather than walking the grid here.

    Errors:
        404 — no such tour, or the ``route_drawing`` flag is off.

    Args:
        request: The incoming GET request.
        uuid: The tour's swisstopo uuid (``SkiTour.source_uuid``).

    Returns:
        ``{"rows": [...]}``, empty for a tour never sampled.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")
    tour = _ski_tour_or_404(uuid)
    samples = tour_slope_samples(tour, sample=False)
    return JsonResponse({"rows": terrain_detail(samples, tour_points(tour)) or []})


@require_POST
@ratelimit(key="user", rate=_DRAW_CREATE_RATE, block=False)
def ski_tour_save(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Copy one SAC tour onto the requesting user's routes (SNOW-1278).

    Response (200)::

        {"uuid": "…", "bounds": [w, s, e, n]}

    Errors:
        403 — anonymous request.
        404 — no such tour, or the ``route_drawing`` flag is off.
        409 — the user has reached ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming POST request.
        uuid: The tour's swisstopo uuid.

    Returns:
        A JsonResponse.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)
    tour = _ski_tour_or_404(uuid)
    try:
        route = save_ski_tour(request.user, tour)
    except RouteLimitReached:
        logger.info("Ski tour save blocked: user=%s hit the cap", request.user.pk)
        return JsonResponse({"error": "route_limit_reached"}, status=409)
    return JsonResponse({"uuid": str(route.uuid), "bounds": route.bounds})


def _tour_rows(tours: list[SkiTour]) -> list[dict[str, Any]]:
    """Return what each row of a tour list shows, by summit and then name."""
    rows = [
        {
            "uuid": str(tour.source_uuid),
            "name": localised(tour.names, tour.name),
            "target_name": tour.target_name,
            "target_altitude_m": tour.target_altitude_m,
            "grade": localised(tour.grades, tour.grade) or tour.grade,
            "time": sac_time(tour.ascent_time_label),
            "down": is_descent(tour),
        }
        for tour in tours
    ]
    rows.sort(key=lambda row: (row["target_name"], row["name"]))
    return rows


@require_GET
@require_htmx
def ski_tour_list(request: HttpRequest) -> HttpResponse:
    """Render the sheet that lists tours: those under a tap, or to a summit.

    SNOW-1278. A tap never picks a tour on its own: more than one tour in
    reach opens this list. ``?tours=<uuid>,<uuid>`` lists those tours
    ("6 routes pass here"), each row named by its summit; ``?summit=<key>``
    lists every tour to one summit or hut, in two groups, the ways up and
    the ways down, each row named by SAC's name for it.

    Errors:
        400 — neither parameter, or a malformed uuid.
        404 — the ``route_drawing`` flag is off, or the summit is unknown.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        The rendered sheet body.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")
    summit_key_param = request.GET.get("summit", "")
    if summit_key_param:
        summit = summits().get(summit_key_param)
        if summit is None:
            raise Http404("Not found.")
        tours = [
            tour
            for tour in SkiTour.objects.filter(
                target_name=summit.name, target_altitude_m=summit.altitude_m
            )
            if tour_key(tour) == summit.key
        ]
        rows = _tour_rows(tours)
        context: dict[str, Any] = {
            "summit": summit,
            "count": len(rows),
            "up_rows": [row for row in rows if not row["down"]],
            "down_rows": [row for row in rows if row["down"]],
        }
    else:
        try:
            uuids = [
                UUID(value)
                for value in request.GET.get("tours", "").split(",")[
                    :_SKI_TOUR_LIST_MAX
                ]
                if value
            ]
        except ValueError:
            return HttpResponseBadRequest("Bad tour id.")
        if not uuids:
            return HttpResponseBadRequest("No tours.")
        context = {
            "rows": _tour_rows(list(SkiTour.objects.filter(source_uuid__in=uuids)))
        }
    return render(request, "routes/partials/_ski_tour_list.html", context)


@require_GET
@cache_control(private=True, no_store=True)
def route_gpx(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Download one of the requesting user's routes as a ``.gpx`` (SNOW-1260).

    The admin's ``download_as_gpx`` for the owner: the same
    ``gpx_export.build_gpx`` reconstitution, which states in its own
    ``<desc>`` how it differs from the file that went in (or, for a drawn
    route, that there never was one). Owner-scoped — another user's uuid
    answers 404, never 403. A route the caller holds only as a followed,
    unclaimed share has no uuid on their side and so no download.

    Errors:
        403 — anonymous request.
        404 — the flag is not active, or the uuid is not this user's route.

    Args:
        request: The incoming GET request.
        uuid: The Route's uuid, from the URL.

    Returns:
        A GPX attachment.

    """
    if not flag_is_active(request, ROUTE_DRAWING_FLAG):
        raise Http404("Not found.")

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        route = Route.objects.for_user(request.user).get(uuid=uuid)
    except Route.DoesNotExist as exc:
        raise Http404("Not found.") from exc

    response = HttpResponse(
        build_gpx(route).encode("utf-8"), content_type="application/gpx+xml"
    )
    response["Content-Disposition"] = f'attachment; filename="{gpx_filename(route)}"'
    logger.info("Route downloaded as GPX: uuid=%s user=%s", route.uuid, request.user.pk)
    return response
