"""
apps/trips/views.py — the trip page and the fragments that author one.

Three full pages, which share none of the fragment rules because they are
not fragments:

- ``trip_list`` (GET) — ``/trips/``: the reader's own agenda, split
  upcoming / past. Scoped by PARTICIPATION, so a trip they joined is on it
  and one they organised is labelled rather than filed separately.
- ``trip_new`` (GET) — ``/trips/new/?route=<uuid>``: the authoring form for
  a trip planned from one of the requesting user's own routes.
- ``trip_detail`` (GET) — ``/trips/<uuid>/``: the trip itself. A REAL PAGE
  rather than a redirect into the map, so a shared link unfurls as a proper
  card in a message and the recipient sees what they were sent before being
  asked for anything.

plus three HTMX fragments under the ``partials/`` prefix, every one
``@require_htmx`` (invariant 4):

- ``trip_create`` (POST) — writes the trip and answers ``HX-Redirect`` to
  its page.
- ``trip_edit`` (POST) — updates the plan; answers ``HX-Redirect`` on
  success, and re-renders the form with its errors when the submission is
  invalid.
- ``trip_delete`` (POST) — deletes the trip and answers ``HX-Redirect``.
- ``trip_join`` / ``trip_leave`` (POST, SNOW-822) — the roster's two verbs;
  both answer the roster fragment.
- ``trip_route_save`` (POST, SNOW-824) — copies the trip's SNAPSHOT into the
  viewer's own routes, and answers the saved-state control.

plus the sharing pair (SNOW-821), neither of which is a fragment:

- ``trip_share_create`` (POST) — mints (or rotates) the trip's ONE link and
  answers JSON with its absolute URL, for the native share sheet. NOT
  ``@require_htmx``: its body goes to ``navigator.share``, not into the page.
- ``trip_share_revoke`` (POST) — nulls the token, same shape.
- ``trip_share_page`` (GET/HEAD) — ``/trips/s/<token>/``, the public page a
  recipient opens. The one endpoint here an anonymous stranger can reach, so
  it is rate-limited on the (token, IP) key ``apps.routes.views`` established
  for the routes twin: this is the token-guessing surface.

**Why the form is a page and the writes are fragments.** The "Plan a trip"
control lives on a route row inside the map's routes panel, whose body is
re-cloned from a ``<template>`` on every open — a form swapped into it would
be thrown away the moment the sheet closed, mid-typing. Planning a trip is
a five-field authoring task, which is a page. The WRITES stay fragments
because the form posts over HTMX from that page and needs to re-render
itself with errors without a round trip through a full re-render.

Ownership: ``trip_detail`` is ORGANISER-ONLY in this commit — SNOW-821 adds
the tokenised public page and SNOW-822 opens the object page to
participants. Everything else here is organiser-scoped through the service
layer's own lookups, which raise ``DoesNotExist`` for a uuid that is not
this user's; the view answers 404 and never 403, so a probing request
cannot distinguish "not yours" from "doesn't exist".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.conf import settings
from django.db.models import Count
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)
from django_ratelimit.decorators import ratelimit

from apps.core.decorators import require_htmx
from apps.core.http import client_ip
from apps.routes.models import Route
from apps.routes.services.routes import RouteLimitReached
from apps.trips.forms import TripForm
from apps.trips.models import Trip
from apps.trips.services.participants import (
    OrganiserCannotLeave,
    is_participant,
    join_trip,
    leave_trip_by_uuid,
    roster_for,
    roster_names_visible_to,
)
from apps.trips.services.routes import already_saved, save_trip_route
from apps.trips.services.shares import (
    TripShareTokenCollision,
    mint_trip_share,
    revoke_trip_share,
)
from apps.trips.services.trips import (
    TripLimitReached,
    create_trip,
    delete_trip,
    update_trip,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# Stand-in uuid for reversing a ``__UUID__``-templated share URL. Mirrors
# ``apps.routes.views._DUMMY_UUID``.
_DUMMY_UUID = UUID(int=0)


def _share_url_templates() -> dict[str, str]:
    """Return the two share URLs with ``__UUID__`` where the uuid goes.

    Handed to the trip page so ``static/js/trip_share.js`` can build the
    endpoints at click time from the uuid riding on the button — the module
    must not know how this project spells its URLs. The same trick
    ``apps.routes.views._rename_url_template`` uses.

    Reversed per call rather than once at import: this module is imported
    by ``apps.trips.urls``, so reversing at import time would ask the
    URLconf to resolve itself while it is still being built.

    Returns:
        A dict of the two templated paths, ready for the page's context.

    """
    dummy = str(_DUMMY_UUID)
    return {
        "share_url_template": reverse("trips:share_create", args=[_DUMMY_UUID]).replace(
            dummy, "__UUID__"
        ),
        "share_revoke_url_template": reverse(
            "trips:share_revoke", args=[_DUMMY_UUID]
        ).replace(dummy, "__UUID__"),
    }


# The share-page follow's budget, keyed on (token, IP). Mirrors
# ``apps.routes.views._SHARE_FOLLOW_RATE`` and its reasoning: a real
# recipient re-opening their own link never approaches it, while a scanner
# walking the token space cannot do so quickly, and a NATed office network
# does not share one budget across unrelated links.
_SHARE_PAGE_RATE = "30/h"

# The organiser's Share button's budget. Keyed on ``user`` because the
# endpoint is auth-only, matching the authoring limiter below.
_SHARE_WRITE_RATE = "20/m"

# The authoring endpoints' rate-limit budget, keyed on ``user`` because both
# are auth-only. Each create writes three rows (a Location, a Trip and the
# organiser's participant row), so the cap is what stops a scripted client
# filling the tables between two cap checks. 20/m is far above anything a
# person planning trips reaches and far below what a script needs to matter.
_TRIP_WRITE_RATE = "20/m"


def _trip_map_payload(trip: Trip) -> dict[str, Any]:
    """Build the inline GeoJSON payload ``static/js/trip_map.js`` draws.

    One ``LineString`` Feature for the track and one ``Point`` Feature for
    the meeting point, plus the snapshot's ``bounds`` for the initial
    fit. Inline in the page rather than fetched from an endpoint of its
    own: the whole payload is already loaded by the time the page renders,
    it is small, and a public share page that had to make a second
    authenticated-looking request to draw its own map would be a page that
    shows nothing to the person it was sent to.

    ``points`` is emitted verbatim — the snapshot stores ``[lon, lat,
    ele]`` in GeoJSON axis order already, so there is no transform here
    and no chance of an axis swap creeping in.

    Args:
        trip: The trip to describe.

    Returns:
        A JSON-serialisable dict with ``route``, ``meeting`` and
        ``bounds`` keys.

    """
    meeting = trip.meeting_point
    return {
        "route": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": trip.points},
            "properties": {
                "distance_m": trip.distance_m,
                # None passes straight through: "unknown", not zero.
                "ascent_m": trip.ascent_m,
                "descent_m": trip.descent_m,
            },
        },
        "meeting": {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                # GeoJSON axis order — longitude first (RFC 7946) — even
                # though every Python signature in this project is
                # latitude-first. The two conventions meet here.
                "coordinates": [meeting.longitude, meeting.latitude],
            },
            "properties": {},
        },
        "bounds": trip.bounds,
    }


def _trip_context(trip: Trip, request: HttpRequest) -> dict[str, Any]:
    """Build the context every trip surface renders from.

    One builder shared by the object page and (from SNOW-821) the public
    share page, so the two cannot disagree about what a trip is — the
    difference between them is who may see it, never what it says.

    Args:
        trip: The trip being rendered.
        request: The current request, read for the viewer's identity.

    Returns:
        The template context.

    """
    viewer = request.user
    is_organiser = viewer.is_authenticated and trip.created_by_id == viewer.pk
    return {
        "trip": trip,
        "is_organiser": is_organiser,
        # SNOW-822. The roster, and the disclosure rule's answer for this
        # viewer, resolved once here and read by both surfaces — a count
        # and a names list computed separately would eventually disagree
        # about which of them the rule applies to.
        "roster": roster_for(trip),
        "roster_names_visible": roster_names_visible_to(viewer, trip),
        "is_participant": is_participant(viewer, trip),
        "leave_url": reverse("trips:leave", args=[trip.uuid]),
        # SNOW-824. Available to ANYONE who can see the trip, participant
        # or not: saving does not join and joining does not save.
        "route_already_saved": already_saved(viewer, trip),
        # Uuid-addressed on the object page. ``trip_share_page`` overrides
        # this with the token-addressed twin, because a link-holder who has
        # not joined must never be handed the uuid.
        "save_route_url": reverse("trips:save_route", args=[trip.uuid]),
        "map_payload": _trip_map_payload(trip),
        # The site default and nothing else. A trip page has no basemap
        # picker: it is a document about one plan rather than a map to
        # explore, and the picker's persisted choice lives in the map
        # page's own localStorage where this page cannot read it.
        "basemap_url": settings.BASEMAP_STYLE_URL,
        # The organiser's edit form, bound to nothing and prefilled from
        # the trip. Built here rather than in the template because a form
        # is Python; rendered only inside the ``is_organiser`` branch.
        "edit_form": TripForm(
            initial={
                "date": trip.date,
                "start_time": trip.start_time,
                "name": trip.name,
                "description": trip.description,
                "latitude": trip.meeting_point.latitude,
                "longitude": trip.meeting_point.longitude,
            }
        )
        if is_organiser
        else None,
        # Only the organiser's page wires the share controls, so only it
        # needs the templates. The public page gets empty strings rather
        # than a missing key, so a stray attribute renders as "" rather
        # than as the string "None".
        **(
            _share_url_templates()
            if is_organiser
            else {"share_url_template": "", "share_revoke_url_template": ""}
        ),
    }


# ---------------------------------------------------------------------------
# Full pages
# ---------------------------------------------------------------------------


@require_GET
def trip_new(request: HttpRequest) -> HttpResponse:
    """Render the authoring form for a trip planned from one of my routes.

    The route is named by a ``?route=<uuid>`` parameter and looked up
    owner-scoped, so a uuid that is not this user's 404s rather than
    revealing that it exists.

    The form is prefilled with the route's first coordinate as the meeting
    point — see ``TripForm``'s docstring for why that is two number
    inputs rather than a pin drop.

    Anonymous visitors are REDIRECTED to sign-in rather than answered
    403: this is a page, and ``apps.accounts.views``'s own auth-gated
    pages set that precedent. Django's ``@login_required`` is deliberately
    not used — it points at ``settings.LOGIN_URL``, which this project
    never set, so it would send visitors to Django's own
    ``/accounts/login/`` and a 404.

    Errors:
        404 — no ``route`` parameter, or one that is not this user's.

    Args:
        request: The incoming GET request.

    Returns:
        The rendered authoring page, or a redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    raw_uuid = request.GET.get("route", "")
    try:
        route = Route.objects.for_user(request.user).get(uuid=UUID(raw_uuid))
    except (ValueError, Route.DoesNotExist) as exc:
        raise Http404("No such route.") from exc

    first = route.points[0] if route.points else [0.0, 0.0]
    form = TripForm(
        initial={
            "name": route.name,
            # Latitude first in the signature, longitude first in the
            # storage — see ``_trip_map_payload``.
            "latitude": first[1],
            "longitude": first[0],
        }
    )
    return render(
        request,
        "trips/trip_new.html",
        {"form": form, "route": route},
    )


@require_GET
def trip_detail(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Render one trip's own page.

    ORGANISER-ONLY in this commit: the object page answers for the account
    that created the trip and 404s for everyone else. SNOW-821 adds the
    tokenised public page and SNOW-822 opens this one to the roster.

    An anonymous visitor is redirected to sign-in on the same reasoning
    ``trip_new`` states, not 404'd: until SNOW-821 there is no public trip
    surface at all, so "sign in" is the honest answer rather than "no such
    thing".

    Errors:
        404 — the uuid is not a trip this user organised.

    Args:
        request: The incoming GET request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        The rendered trip page, or a redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    try:
        trip = (
            Trip.objects.for_user(request.user)
            .select_related("meeting_point")
            .get(uuid=uuid)
        )
    except Trip.DoesNotExist as exc:
        raise Http404("No such trip.") from exc

    return render(request, "trips/trip.html", _trip_context(trip, request))


# ---------------------------------------------------------------------------
# HTMX fragments
# ---------------------------------------------------------------------------


@require_htmx
@require_POST
@ratelimit(key="user", rate=_TRIP_WRITE_RATE, block=False)
def trip_create(request: HttpRequest) -> HttpResponse:
    """Create a trip from the posted form and redirect to its page.

    Answers ``HX-Redirect`` rather than a fragment because the next thing
    the organiser wants is the trip itself, and a redirect header is how
    HTMX navigates. An invalid submission comes back as the form with its
    errors, swapped in place, which is the whole reason this is a fragment
    endpoint and not a plain form post.

    Errors:
        400 — non-HTMX request, or an invalid submission (the form, with
              its errors, at 400 so HTMX's own error handling is not
              silently bypassed by a 200 that changed nothing).
        403 — anonymous request.
        409 — the user has reached ``settings.TRIPS_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        An empty 200 carrying ``HX-Redirect``, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    # After the auth check, not before: the limiter keys on ``user``, and an
    # anonymous request has no account to bucket.
    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before creating another trip.",
            status=429,
        )

    raw_uuid = request.POST.get("route", "")
    try:
        route_uuid = UUID(raw_uuid)
    except ValueError:
        return HttpResponse("A route is required.", status=400)

    form = TripForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "trips/partials/_trip_form.html",
            {"form": form, "route_uuid": raw_uuid},
            status=400,
        )

    try:
        trip = create_trip(
            request.user,
            route_uuid=route_uuid,
            date=form.cleaned_data["date"],
            start_time=form.cleaned_data["start_time"],
            name=form.cleaned_data["name"],
            description=form.cleaned_data["description"],
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
        )
    except Route.DoesNotExist:
        return HttpResponse("Route not found.", status=404)
    except TripLimitReached:
        logger.info("Trip create blocked: user=%s hit the cap", request.user.pk)
        return render(request, "trips/partials/_trip_limit.html", {}, status=409)

    response = HttpResponse("")
    response["HX-Redirect"] = reverse("trips:detail", args=[trip.uuid])
    return response


@require_htmx
@require_POST
@ratelimit(key="user", rate=_TRIP_WRITE_RATE, block=False)
def trip_edit(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Update the plan half of one of the organiser's own trips.

    Answers ``HX-Redirect`` back to the trip's own page on success. A
    redirect rather than a swapped-in summary because a trip page draws a
    map, an elevation profile and a roster off one context — repainting
    part of that from a fragment would leave the rest describing the old
    plan.

    Errors:
        400 — non-HTMX request, or an invalid submission.
        403 — anonymous request.
        404 — the uuid is not a trip this user organised.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        An empty 200 carrying ``HX-Redirect``, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before editing again.",
            status=429,
        )

    form = TripForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "trips/partials/_trip_form.html",
            {"form": form, "trip_uuid": str(uuid)},
            status=400,
        )

    try:
        update_trip(
            request.user,
            uuid,
            date=form.cleaned_data["date"],
            start_time=form.cleaned_data["start_time"],
            name=form.cleaned_data["name"],
            description=form.cleaned_data["description"],
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
        )
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)

    response = HttpResponse("")
    response["HX-Redirect"] = reverse("trips:detail", args=[uuid])
    return response


@require_htmx
@require_POST
def trip_delete(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Delete one of the organiser's own trips.

    Deleting removes the trip for EVERYONE on it — the participant rows
    cascade — which is what ``_trip_delete_confirm.html`` says out loud
    before the press.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request.
        404 — the uuid is not a trip this user organised.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        An empty 200 carrying ``HX-Redirect``, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        delete_trip(request.user, uuid)
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)

    response = HttpResponse("")
    # The list, not the map: a deleted trip leaves the organiser looking at
    # the other trips they still have, which is where the thing they just
    # removed used to be.
    response["HX-Redirect"] = reverse("trips:list")
    return response


# ---------------------------------------------------------------------------
# Sharing (SNOW-821)
# ---------------------------------------------------------------------------


def _share_page_rate_limit_key(group: str, request: HttpRequest) -> str:
    """Return the rate-limit bucket for a share-page read: (token, IP).

    The trips twin of ``apps.routes.views._share_follow_rate_limit_key``,
    keyed the same way for the same reason: one recipient re-opening their
    own link is unaffected, a scanner hammering one link is bounded, and a
    NATed office network does not share one budget across unrelated links.

    Args:
        group: The django-ratelimit group name (unused — one group here).
        request: The current HTTP request.

    Returns:
        An opaque bucket key.

    """
    match = request.resolver_match
    token = match.kwargs.get("token", "") if match is not None else ""
    return f"{token}|{client_ip(request)}"


@require_POST
@ratelimit(key="user", rate=_SHARE_WRITE_RATE, block=False)
def trip_share_create(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Mint (or rotate) the trip's one share link and return its URL.

    Response (200)::

        {"url": "https://snowdesk.info/trips/s/<token>/"}

    Returns JSON rather than a partial because the caller does not render
    it: ``static/js/share.js`` hands the URL straight to the native share
    sheet (or the clipboard). The same shape ``routes:share_create``
    returns, so one JS helper serves both.

    NOT ``@require_htmx``. Its response is a string handed to
    ``navigator.share``, not swapped into the page, and requiring the
    header would be asserting a contract that is not the one in force.

    **Calling this twice ROTATES the link** — see ``mint_trip_share``. It
    is the organiser's only revoke-and-reshare, and it is why a link sent
    to the wrong person is recoverable.

    Errors:
        403 — anonymous request.
        404 — the uuid is not a trip this user organised.
        429 — rate limit exceeded.
        500 — a unique token could not be minted (implausible).

    Args:
        request: The incoming POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        A JsonResponse carrying the absolute share URL.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    try:
        trip = mint_trip_share(request.user, uuid)
    except Trip.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    except TripShareTokenCollision:
        logger.error("trip_share_create: token collision, user=%s", request.user.pk)
        return JsonResponse({"error": "token_collision"}, status=500)

    url = request.build_absolute_uri(
        reverse("trips:share_page", args=[trip.share_token])
    )
    return JsonResponse({"url": url})


@require_POST
@ratelimit(key="user", rate=_SHARE_WRITE_RATE, block=False)
def trip_share_revoke(request: HttpRequest, uuid: UUID) -> JsonResponse:
    """Stop the trip being reachable by link.

    Answers ``{"revoked": true}``. JSON rather than a fragment for the same
    reason its sibling above does — the organiser's controls are wired by
    ``static/js/share.js`` and neither response goes into the page.

    Idempotent: revoking an unshared trip is a 200, not an error.

    Errors:
        403 — anonymous request.
        404 — the uuid is not a trip this user organised.
        429 — rate limit exceeded.

    Args:
        request: The incoming POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        A JsonResponse confirming the revoke.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    try:
        revoke_trip_share(request.user, uuid)
    except Trip.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    return JsonResponse({"revoked": True})


@require_http_methods(["GET", "HEAD"])
@ratelimit(key=_share_page_rate_limit_key, rate=_SHARE_PAGE_RATE, block=False)
def trip_share_page(request: HttpRequest, token: str) -> HttpResponse:
    """Render the public page behind a trip's share link.

    **A page, not a redirect.** The link is what somebody is sent in a
    message, so it has to unfurl as a card and then open as something the
    recipient can READ — the day, the meeting time, the meeting point, the
    route drawn with a marker, the figures and the organiser's note —
    before being asked for anything. A redirect into the map would show
    them a track and none of that.

    ``noindex`` is emitted (see ``includes/_page_meta.html``) rather than a
    ``robots.txt`` disallow: ``Disallow: /trips/`` would prefix-match this
    very path and block the unfurlers the page exists to serve, while an
    unguessable URL turning up in a search result would defeat the token.

    ONE ANSWER FOR EVERY DEAD LINK. Unknown, revoked and expired are all
    404, decided by ``TripQuerySet.shared()``. Distinguishing them would
    tell a guesser which tokens have ever existed.

    Errors:
        404 — the token matches no live link.
        429 — rate limit exceeded (30/hour per token+IP).

    Args:
        request: The incoming GET or HEAD request.
        token: The share token, from the URL.

    Returns:
        The rendered public trip page, or an error response.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    try:
        trip = (
            Trip.objects.shared().select_related("meeting_point").get(share_token=token)
        )
    except Trip.DoesNotExist as exc:
        raise Http404("No such trip.") from exc

    context = _trip_context(trip, request)
    context["share_token"] = token
    # The join endpoint is addressed by TOKEN, never by uuid: a
    # link-holder who is not on the roster must not be handed the
    # identifier the participant-scoped endpoints key on. The routes app's
    # own pending rows follow the same rule.
    context["join_url"] = reverse("trips:join", args=[token])
    # Save is token-addressed on this surface for the same reason Join is:
    # a link-holder must never be handed the uuid.
    context["save_route_url"] = reverse("trips:save_route_shared", args=[token])
    # And Leave is NOT offered here. It is uuid-addressed and lives on the
    # trip's own page, which every participant can reach — one exit, on the
    # surface that also carries the plan being left.
    context["leave_url"] = ""
    response = render(request, "trips/trip_share.html", context)
    # Per-recipient in the sense that matters: the page varies with who is
    # signed in (SNOW-822 shows the roster's names to participants only), so
    # it must never be held by an intermediate cache and handed to somebody
    # else. Matches ``routes:share_redirect``'s own header.
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Joining and leaving (SNOW-822)
# ---------------------------------------------------------------------------


def _roster_fragment(
    request: HttpRequest, trip: Trip, *, join_url: str = "", leave_url: str = ""
) -> HttpResponse:
    """Render the roster partial for one trip, for this viewer.

    The response of BOTH join and leave, so the count, the names and the
    control are repainted from one read after either write — a fragment
    that returned only the control would leave the count beside it stating
    the state before the press.

    Args:
        request: The current request, read for the viewer's identity.
        trip: The trip whose roster to render.
        join_url: The token-addressed join endpoint, when the surface
            offers one.
        leave_url: The uuid-addressed leave endpoint, likewise.

    Returns:
        The rendered roster fragment.

    """
    viewer = request.user
    return render(
        request,
        "trips/partials/_trip_roster.html",
        {
            "trip": trip,
            "roster": roster_for(trip),
            "roster_names_visible": roster_names_visible_to(viewer, trip),
            "is_participant": is_participant(viewer, trip),
            "is_organiser": viewer.is_authenticated and trip.created_by_id == viewer.pk,
            "join_url": join_url,
            "leave_url": leave_url,
        },
    )


@require_htmx
@require_POST
@ratelimit(key="user", rate=_TRIP_WRITE_RATE, block=False)
def trip_join(request: HttpRequest, token: str) -> HttpResponse:
    """Put the requesting account on the trip behind a live share link.

    ADDRESSED BY TOKEN, and that is the whole access rule: holding a live
    link is what lets somebody join, and the token is the only identifier
    a non-participant has been given. The uuid would be an identifier the
    participant-scoped endpoints key on, so a link-holder never sees one
    (the routes app's pending rows follow the same rule).

    Idempotent through ``join_trip``: a double-tapped Join writes one row
    and answers 200 twice, rather than surfacing the ``(trip, user)``
    uniqueness constraint on a request path.

    Answers the roster fragment, so the count, the names — which the
    caller has just earned — and the control all repaint from one read.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request. Joining needs an account to join WITH;
              the share page draws a sign-in link for that visitor rather
              than a button that posts here.
        404 — the token matches no live link.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.
        token: The share token, from the URL.

    Returns:
        The rendered roster fragment, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before trying again.",
            status=429,
        )

    try:
        trip = Trip.objects.shared().get(share_token=token)
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)

    join_trip(request.user, trip)

    return _roster_fragment(request, trip, join_url=reverse("trips:join", args=[token]))


@require_htmx
@require_POST
def trip_leave(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Take the requesting account off a trip they are on.

    Participation-scoped by the LOOKUP (``leave_trip_by_uuid``), so a uuid
    the caller has nothing to do with is a 404 rather than a 403 — this
    endpoint cannot be used to learn which trip uuids exist.

    THE ORGANISER CANNOT LEAVE. A trip whose organiser is not on it is
    incoherent: nobody is answerable for the plan, and the roster no
    longer holds the person whose name answers "who sent me this". They
    get 409 and a message pointing at Delete, which is the real exit and
    which says out loud that it removes the trip for everyone. 409 rather
    than 403 because this is a permanent conflict with the object's own
    state, not a permissions failure the caller could ever clear.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request.
        404 — the caller is not on a trip with that uuid.
        409 — the caller organises it.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        The rendered roster fragment, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        leave_trip_by_uuid(request.user, uuid)
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)
    except OrganiserCannotLeave:
        return HttpResponse(
            "You organised this trip, so you're always on it. "
            "Delete it to remove it for everyone.",
            status=409,
        )

    # Re-read rather than reuse the instance ``leave_trip_by_uuid`` loaded:
    # the caller is no longer on the roster, so a stale read would render
    # the state before the press. This is one query, on a path that has
    # just written.
    trip = Trip.objects.get(uuid=uuid)
    return _roster_fragment(request, trip)


# ---------------------------------------------------------------------------
# Saving the route (SNOW-824)
# ---------------------------------------------------------------------------


def _save_route_fragment(
    request: HttpRequest, *, saved: bool, save_url: str
) -> HttpResponse:
    """Render the Save-route control in one of its two states.

    Args:
        request: The current request.
        saved: Whether the viewer already holds a matching route.
        save_url: The endpoint the control posts to.

    Returns:
        The rendered control fragment.

    """
    return render(
        request,
        "trips/partials/_trip_save_route.html",
        {"route_already_saved": saved, "save_route_url": save_url},
    )


def _save_route(
    request: HttpRequest, user: "User", trip: Trip, save_url: str
) -> HttpResponse:
    """Copy ``trip``'s route onto the requester's account and answer.

    The shared body of the two entry points below, which differ only in
    how they resolve the trip — by share token for a link-holder, by uuid
    for a participant — and therefore in which URL the returned control
    posts to next.

    Answers the saved-state control rather than a route row: the route row
    belongs to the routes panel, and swapping one into a trip page would
    put a rename pencil and a trash can for a route on a page about a
    trip.

    Args:
        request: The incoming HTMX POST request.
        user: The authenticated saver. Passed separately from ``request``
            because each caller has already narrowed it past its own
            anonymous check, and that narrowing does not survive the call.
        trip: The trip whose snapshot is being copied.
        save_url: The endpoint the returned control posts to.

    Returns:
        The rendered control, or an error response.

    """
    if already_saved(user, trip):
        # Idempotent, on the same reasoning ``join_trip`` is: a
        # double-tapped Save must not spend a slot of the user's cap on a
        # second copy of one track. The check is a geometry match — see
        # ``already_saved`` for why that is the honest question here.
        return _save_route_fragment(request, saved=True, save_url=save_url)

    try:
        save_trip_route(user, trip)
    except RouteLimitReached:
        logger.info("Trip route save blocked: user=%s hit the cap", user.pk)
        return render(request, "routes/partials/_route_limit.html", {}, status=409)

    return _save_route_fragment(request, saved=True, save_url=save_url)


@require_htmx
@require_POST
@ratelimit(key="user", rate=_TRIP_WRITE_RATE, block=False)
def trip_route_save_shared(request: HttpRequest, token: str) -> HttpResponse:
    """Save the route of a trip reached by its share link.

    Addressed by TOKEN, on the rule Join follows: holding a live link is
    what lets somebody act on a trip they are not on, and the uuid keys
    the participant-scoped endpoints.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request. A saved route needs an account to sit on;
              the page draws a sign-in link for that visitor.
        404 — the token matches no live link.
        409 — the viewer is at ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.
        token: The share token, from the URL.

    Returns:
        The rendered saved-state control, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before trying again.",
            status=429,
        )

    try:
        trip = Trip.objects.shared().get(share_token=token)
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)

    return _save_route(
        request,
        request.user,
        trip,
        reverse("trips:save_route_shared", args=[token]),
    )


@require_htmx
@require_POST
@ratelimit(key="user", rate=_TRIP_WRITE_RATE, block=False)
def trip_route_save(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Save the route of a trip the requester is on.

    Participation-scoped by the lookup, so an unrelated uuid is a 404 and
    never a 403 — this endpoint cannot be used to learn which trip uuids
    exist.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request.
        404 — the caller is not on a trip with that uuid.
        409 — the viewer is at ``settings.ROUTES_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Trip's uuid, from the URL.

    Returns:
        The rendered saved-state control, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before trying again.",
            status=429,
        )

    try:
        trip = Trip.objects.for_user(request.user).get(uuid=uuid)
    except Trip.DoesNotExist:
        return HttpResponse("Trip not found.", status=404)

    return _save_route(
        request, request.user, trip, reverse("trips:save_route", args=[uuid])
    )


# ---------------------------------------------------------------------------
# The list (SNOW-823)
# ---------------------------------------------------------------------------


@require_GET
def trip_list(request: HttpRequest) -> HttpResponse:
    """Render the reader's own trips, split upcoming and past.

    **Scoped by PARTICIPATION**, through ``Trip.objects.for_user`` — a trip
    they joined belongs on their agenda exactly as much as one they
    organised. Organising is a LABEL on the row rather than a section of
    its own: splitting by authorship would file two trips on the same
    morning under different headings, and the reader's question is "what
    am I doing", not "what did I write".

    **Split against the trip's own date, and a trip dated TODAY counts as
    upcoming** — the day it exists for has not finished, and a group still
    meeting this afternoon must not find their trip filed under "past" over
    breakfast. Upcoming reads soonest-first (an agenda reads forwards) and
    past most-recent-first (a history reads backwards).

    Unpaginated and unindexed, both deliberately. A person organises a
    handful of trips a season and the cap is 25 organised each; a page that
    long needs neither.

    Anonymous visitors are redirected to sign-in rather than answered 403,
    matching this module's other pages — see ``trip_new`` for why
    ``@login_required`` is not used.

    Args:
        request: The incoming GET request.

    Returns:
        The rendered list page, or a redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    # ``timezone.localdate()`` and not ``date.today()``: the split is
    # against the site's own current day, which is what a reader means by
    # "today" — and the only clock either half of the split may use, or a
    # trip could fall into both or neither.
    today = timezone.localdate()
    # ``participant_count`` is annotated rather than read as
    # ``trip.participants.count`` in the row partial: the template renders
    # one row per trip, so the property form is one COUNT query per row and
    # the page's query count grows with the reader's own agenda. One
    # aggregate over the join answers every row.
    trips = Trip.objects.for_user(request.user).annotate(
        participant_count=Count("participants")
    )

    return render(
        request,
        "trips/trip_list.html",
        {
            "upcoming_trips": list(trips.upcoming(today)),
            "past_trips": list(trips.past(today)),
        },
    )
