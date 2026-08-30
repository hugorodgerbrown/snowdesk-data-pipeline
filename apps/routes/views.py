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

plus two navigation/JSON endpoints outside the ``partials/`` prefix:

- ``routes_geojson`` (GET) — SNOW-687: a ``LineString`` FeatureCollection of
  the requesting user's own routes, for the map's routes layer. Not
  ``@require_htmx`` — consumed by a JS ``fetch()`` call, not an HTMX swap.
- ``route_share_redirect`` (GET/HEAD) — SNOW-764: follows a share link,
  records the token in the session and 302s to the map. A navigation, not
  a fragment, which is why it sits outside ``partials/``.

plus one JSON endpoint the owner's Share button calls:

- ``route_share_create`` (POST) — SNOW-764: mints a share link for one of
  the requesting user's own routes and returns its absolute URL.

Alongside them sits one full page, which shares none of the fragment rules
because it is not a fragment:

- ``my_routes`` (GET) — SNOW-713: ``/account/routes/``, the account area's
  list of the signed-in user's own routes. No ``@require_htmx``; anonymous
  redirects to sign-in rather than answering 403. Authentication is the
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

The whole sharing surface sits behind the ``route_sharing`` waffle flag.
It is read in the three share views and in the two widened branches above
— never in ``apps.public.views._routes_context``, which runs on the
homepage where a flag read costs queries on the site's most-requested page
(see ``docs/feature-flags.md``). Every gated surface is rendered by
``routes:list``, ``my_routes`` or ``/help/``, so the flag never has to be
read by ``home``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import waffle
from django.conf import settings
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseGone,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from apps.core.decorators import require_htmx
from apps.core.freshness import apply_freshness_headers
from apps.core.http import client_ip, is_speculative
from apps.routes.constants import ROUTE_LIST_MAP_VARIANT
from apps.routes.models import Route, RouteShare
from apps.routes.services.gpx import GPXParseError
from apps.routes.services.routes import RouteLimitReached, create_route, delete_route
from apps.routes.services.shares import (
    RouteShareTokenCollision,
    add_pending_token,
    claim_route_share,
    create_route_share,
    drop_pending_token,
    pending_shares,
    pending_tokens,
)

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


def _sharing_enabled(request: HttpRequest) -> bool:
    """Whether the SNOW-764 sharing surface is on for this request.

    One reader inside this module, so its four gates cannot disagree. The
    name is a LITERAL rather than a constant on purpose:
    ``tests/core/services/test_waffle_manifest_call_sites.py`` reads the
    source to check the manifest and the code name the same flags, and it
    can only see literals — a constant here would make this gate invisible
    to the one check that catches a rename going dark.

    Args:
        request: The current HTTP request.

    Returns:
        True when the ``route_sharing`` waffle flag is active.

    """
    # ``bool()`` because waffle's own annotation is ``bool | None`` — a
    # flag with ``everyone`` unset and no other rule matching answers
    # None, which is "not on" and must reach the callers as False rather
    # than as a third state each of them has to think about.
    return bool(waffle.flag_is_active(request, "route_sharing"))


def _pending_shares_for(request: HttpRequest) -> list[RouteShare]:
    """Return this request's pending shares, or nothing when there are none.

    The guard the two widened endpoints share, and the reason neither of
    them costs a visitor who has never followed a share link anything: the
    session read is a dict lookup on an object the auth middleware has
    already loaded, and BOTH the flag read and the database query sit
    behind it. An empty list short-circuits before either.

    The order is deliberate — session first, flag second. Reversing them
    would read the flag (a cached DB row) on every routes request from
    every visitor, which is the cost ``docs/feature-flags.md`` records
    SNOW-749 deciding was not worth paying.

    Args:
        request: The current HTTP request.

    Returns:
        The claimable shares this session is holding, oldest-followed
        first; empty when there are none or when sharing is off.

    """
    if not pending_tokens(request.session):
        return []
    if not _sharing_enabled(request):
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
    return {
        "type": "Feature",
        "geometry": {
            # Stored in GeoJSON axis order already — see routes_geojson's
            # own note and Route.points' help_text.
            "type": "LineString",
            "coordinates": route.points,
        },
        "properties": {
            **identity,
            "name": route.name,
            "distance_m": route.distance_m,
            # None passes straight through: "unknown", not zero.
            "ascent_m": route.ascent_m,
            "descent_m": route.descent_m,
            # int, not float: a GPX records whole seconds, and the popup
            # renders hours and minutes off this.
            "duration_s": (
                int(duration.total_seconds())
                if (duration := route.duration) is not None
                else None
            ),
            "bounds": route.bounds,
        },
    }


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
        Rendered ``_route_list.html`` (or ``_route_list_map.html`` for
        ``?variant=map``), or an error response.

    """
    pending = _pending_shares_for(request)

    if not request.user.is_authenticated and not pending:
        return HttpResponse("Authentication required.", status=403)

    routes = (
        list(Route.objects.for_user(request.user))
        if request.user.is_authenticated
        else []
    )

    variant = request.GET.get("variant", "")

    return render(
        request,
        _LIST_TEMPLATES.get(variant, _LIST_TEMPLATE_DEFAULT),
        {
            "routes": routes,
            "pending_shares": pending,
            # SNOW-764: whether an owned row draws its Share control. Two
            # conditions, and the second is not a flag read — it is which
            # SURFACE asked. Share is wired by static/js/routes.js, which
            # owns the map panel; /account/routes/ renders the same row
            # through the default variant and has no handler for it, and a
            # control nothing listens to is worse than no control at all
            # (it is the "dead pencil" argument account_routes.js's own
            # header makes). The account page gains Share when its module
            # does — noted as a follow-up on SNOW-764.
            "sharing_enabled": (
                variant == ROUTE_LIST_MAP_VARIANT and _sharing_enabled(request)
            ),
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
    Feature per route, whose ``coordinates`` are ``Route.points``
    **verbatim** — the model already stores ``[lon, lat, ele]`` in GeoJSON
    axis order (RFC 7946), already simplified at ingest, so there is no
    per-render transform and no chance of an axis swap creeping in between
    the two representations.

    Properties per feature: ``uuid``, ``name``, ``distance_m``,
    ``ascent_m``, ``descent_m``, ``duration_s`` and ``bounds``.
    ``ascent_m`` and ``descent_m`` are passed through **as stored**,
    including ``None`` — ``Route``'s own docstring is explicit that null
    means "the source file carried no elevation data", not "flat", and the
    client omits those figures entirely rather than rendering a zero for an
    unknown.

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
        features.append(_route_feature(shared, {"token": share.token, "pending": True}))

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
        404 — sharing is off; or the uuid is not this user's route.
        429 — rate limit exceeded.
        500 — a unique token could not be minted (implausible; see
              ``create_route_share``).

    Args:
        request: The incoming POST request.
        uuid: The Route's uuid, from the URL.

    Returns:
        A JsonResponse carrying the absolute share URL.

    """
    if not _sharing_enabled(request):
        # 404 rather than 403: with the flag off the endpoint does not
        # exist as far as anyone outside the rollout is concerned, and a
        # 403 would advertise that it is about to.
        return JsonResponse({"error": "not_found"}, status=404)

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

    Speculative requests (HEAD, ``Sec-Purpose: prefetch``/``prerender``)
    still redirect but write NOTHING to the session — a browser
    prefetching a link in a chat window has not been given a route, and
    writing a session for one would both mis-state intent and let a
    scanner grow the session table by prefetch alone. The same rule, read
    off the same helper, as ``apps.public.views.share_redirect``.

    Errors:
        404 — sharing is off, or the token matches no share at all.
        410 — the share exists but is expired or its route was deleted.
        429 — rate limit exceeded (30/hour per token+IP).

    Args:
        request: The incoming GET or HEAD request.
        token: The share token, from the URL.

    Returns:
        A 302 to the map, or a 404/410/429.

    """
    if not _sharing_enabled(request):
        raise Http404("Route sharing is not enabled.")

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

    if not share.is_claimable:
        gone = HttpResponseGone(_SHARE_GONE_BODY, content_type="text/html")
        gone["Cache-Control"] = "no-store"
        return gone

    if not is_speculative(request):
        add_pending_token(request.session, token)

    destination = f"{reverse('public:home')}?route_share={token}"
    redir = HttpResponseRedirect(destination)
    redir["Cache-Control"] = "no-store"
    return redir


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
        404 — sharing is off, or the share is unknown, expired, or its
              route has been deleted.
        409 — the claimer is at ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded (> 20 claims/min per user).

    Args:
        request: The incoming HTMX POST request.
        token: The share token, from the URL.

    Returns:
        The rendered route-row partial for the new copy, or an error
        response.

    """
    if not _sharing_enabled(request):
        return HttpResponse("Route not found.", status=404)

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

    # ``sharing_enabled`` True: the only surface that posts here is the map
    # panel, and the row it swaps in is now an ordinary owned row on a
    # surface whose Share control is wired. Reaching here at all means the
    # flag is on — the guard at the top of this view saw to that.
    return render(
        request,
        "routes/partials/_route.html",
        {"route": route, "sharing_enabled": True},
    )


# ---------------------------------------------------------------------------
# Full-page views
# ---------------------------------------------------------------------------


@require_GET
def my_routes(request: HttpRequest) -> HttpResponse:
    """Render the signed-in user's own saved routes as a full page (SNOW-713).

    The account area's routes surface, mounted at ``/account/routes/``. Until
    this existed a user could upload a GPX and then reach it only from the
    map: SNOW-686 gave routes a roundel and a panel over the map canvas, and
    nothing listed what was saved.

    The routes twin of ``apps.observations.views.my_observations``, and
    deliberately the same shape — a full-page host for
    ``routes/partials/_route_list.html``, whose rows have been the shared UGC
    row since SNOW-711. No second listing is authored here.

    Deliberately NOT ``@require_htmx``, unlike every other GET in this
    module: this is a page a user navigates to, and applying that decorator
    by habit would make it unreachable by the only means anyone reaches it.

    Gating follows the account area rather than the fragment endpoints
    above. An anonymous visitor is redirected to sign-in, as ``accounts:hub``
    and ``accounts:settings`` do, not answered 403 — a page can render the
    way in; a fragment cannot.

    Authentication is the only gate. Until SNOW-724 the page also sat
    behind the superusers-only ``routes`` waffle flag and answered 404 for
    everyone else; the feature reached general availability, so the flag
    and its 404 branch are both gone.

    Ownership is enforced by the query (``for_user``), so nothing here
    depends on an id supplied by the client.

    The response carries ``Cache-Control: private, no-store`` — per-user
    content that must never land in a shared cache, mirroring
    ``routes_geojson`` below. That also keeps it out of the PWA shell cache:
    caching routes for offline reads belongs with the map layer, as
    ``_route_list_map.html`` already records.

    Args:
        request: The incoming GET request.

    Returns:
        Rendered ``routes/my_routes.html``, or a redirect to sign-in for an
        anonymous visitor.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    routes = list(Route.objects.for_user(request.user))

    response = render(
        request,
        "routes/my_routes.html",
        {
            "routes": routes,
            # The ``__UUID__``-templated rename endpoint, resolved here
            # rather than built in JS: static/js/account_routes.js must not
            # know how this project spells its URLs. Same contract as the
            # map panel's ``data-route-rename-url-template`` and the
            # favourites list's ``rename_url_template``.
            "rename_url_template": _rename_url_template(),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response
