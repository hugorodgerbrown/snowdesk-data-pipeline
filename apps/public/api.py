"""
apps/public/api.py — JSON endpoints for bulletins, sharing and the client shell.

* ``/api/version`` and ``/api/sw-config`` — what the service worker and the
  PWA shell read to know which build they are talking to.
* ``/api/bulletins/share/`` — mint a share link for one bulletin view.
* ``/api/bulletins/<bulletin_id>/`` — a bulletin's stored render model.
* ``/api/bulletins/<bulletin_id>.caaml.json`` — the bulletin's CAAML.

The map's JSON endpoints and the in-map editor moved to ``apps/map/api.py``
and ``apps/map/editor.py`` (SNOW-1428). ``apps/public/api_urls.py`` routes
all of them under ``api:``.

Plain Django ``JsonResponse`` views — no DRF.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date

from django.conf import settings
from django.db import IntegrityError
from django.http import (
    Http404,
    HttpRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.analytics.signals import emit_server_signal
from apps.bulletins.models import (
    Bulletin,
    BulletinShare,
)
from apps.bulletins.services.selection import select_bulletin_for_date
from apps.core.sw_shell import served_cache_version
from apps.ingest.public import caaml_envelope, source_caaml
from apps.regions.models import (
    MicroRegion,
)

from .bulletin_panels import (
    resolve_region_for_bulletin,
)

# Cache lifetime for the bulletin JSON alternates: five minutes, the same
# figure the map's endpoints use (``apps/map/api.py``). Pair the decorator with
# @vary_on_headers("Accept-Encoding") to stop SessionMiddleware from appending
# Vary: Cookie and killing shared caching.
_DYNAMIC_CACHE_MAX_AGE = 300


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Community reports overlay (SNOW-419)
# ---------------------------------------------------------------------------


# ``_truncate_to_quarter_hour`` stood here — it floored ``observed_at`` to
# the preceding 15-minute mark on the way out, as part of the SNOW-419
# anonymisation contract. It is gone, and both its callers (this endpoint
# and ``views.observations_list``) now send the instant as recorded.
#
# It was protecting nothing. No surface attributes a report to anyone: the
# feed carries no user, no pk, and no name, and neither does the
# /observations page. A timestamp only helps identify a reporter when
# there is a name beside it to attach it to.
#
# What it did cost was accuracy, in one direction. Flooring never moves an
# instant forward, so every pin read up to fifteen minutes older than it
# was — most visibly on a report just filed, which the map called "5 min
# ago" while the user's own panel, holding the true instant, said "29
# seconds ago". Two ages for one report, on screen together, and the
# blunter one was wrong.
#
# Anonymity still rests on what it always rested on: coordinates rounded
# to 3 dp (~80-110 m), and no identity, GPS fix or accuracy on the wire.


# ---------------------------------------------------------------------------
# Edit-resorts mode (SNOW-74) — superuser-only (SNOW-724)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Edit-locations mode (SNOW-755) — superuser-only
# ---------------------------------------------------------------------------
#
# The same shape as the resort editor above — superuser gate, JSON bodies,
# a placed pin inside the Swiss bbox — over ``Location`` /
# ``ResortLocation`` instead of ``Resort``. Two things genuinely differ.
#
# **A location is shared, a resort is not.** Mont Fort is one row that
# Verbier, Nendaz, Veysonnaz and Thyon all reference, which is the whole
# reason the model exists. So there is a ``link`` endpoint with no
# resort-editor equivalent: it attaches an EXISTING location to another
# resort without minting a second copy of the summit.
#
# **``kind`` and ``role`` are independent.** ``Location.kind`` describes
# the place (Mont Fort is a peak whoever is looking at it);
# ``ResortLocation.role`` describes one resort's relationship to it. A
# point can be one resort's top and another's mid-station, so neither is
# ever derived from the other — every payload below carries both.


# SNOW-79 retired the ``offline_manifest_map`` endpoint. The PWA shell
# service worker now caches static assets at runtime via
# stale-while-revalidate, so there is no precache manifest for an SW to
# fetch. See ``static/js/sw.js`` and ``docs/offline-map.md``.


# ---------------------------------------------------------------------------
# Share-create endpoint (SNOW-217)
# ---------------------------------------------------------------------------

# Maximum number of token-generation retries before giving up on collision.
_SHARE_TOKEN_MAX_RETRIES = 2


def _parse_share_request(
    request: HttpRequest,
) -> tuple[JsonResponse, None, None] | tuple[None, str, date]:
    """Parse and validate the share-create request body.

    Returns either ``(error_response, None, None)`` on validation failure,
    or ``(None, region_id, target_date)`` on success.
    """
    try:
        body = json.loads(request.body or b"")
    except ValueError, json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json"}, status=400), None, None

    if not isinstance(body, dict):
        return JsonResponse({"error": "invalid_json"}, status=400), None, None

    region_id = body.get("region_id")
    date_str = body.get("date")

    if not region_id or not date_str:
        return (
            JsonResponse(
                {
                    "error": "missing_fields",
                    "detail": "region_id and date are required",
                },
                status=400,
            ),
            None,
            None,
        )

    try:
        target_date = date.fromisoformat(str(date_str))
    except ValueError, TypeError:
        return (
            JsonResponse(
                {"error": "invalid_date", "detail": "date must be YYYY-MM-DD"},
                status=400,
            ),
            None,
            None,
        )

    return None, str(region_id), target_date


def _create_share_with_retry(
    bulletin: Bulletin,
    region: MicroRegion,
    target_date: date,
) -> BulletinShare | None:
    """Create a BulletinShare with a unique token, retrying on collision.

    Returns the created share, or None if all retries exhausted.
    """
    for _attempt in range(_SHARE_TOKEN_MAX_RETRIES):
        token = secrets.token_urlsafe(8)
        try:
            return BulletinShare.objects.create(
                token=token,
                bulletin=bulletin,
                region=region,
                target_date=target_date,
            )
        except IntegrityError:
            if _attempt == _SHARE_TOKEN_MAX_RETRIES - 1:
                logger.error(
                    "share_create: token collision after %d retries",
                    _SHARE_TOKEN_MAX_RETRIES,
                )
            else:
                logger.warning("share_create: token collision, retrying")
    return None


@require_POST
@ratelimit(key="ip", rate="20/m", block=False)
def share_create(request: HttpRequest) -> JsonResponse:
    """Create a tokenised share link for a bulletin page.

    Request body (JSON)::

        {"region_id": "ch-4222", "date": "2026-04-08"}

    Response (200)::

        {"url": "https://snowdesk.app/s/<token>/"}

    Errors:
        400 — missing/malformed JSON, missing ``region_id`` or ``date``
              field, malformed date string.
        404 — unknown ``region_id`` or no bulletin covers the given date.
        429 — rate limit exceeded (> 20 requests/min per IP).

    Args:
        request: The incoming HTTP request (must be POST).

    Returns:
        A JsonResponse with ``{"url": "..."}`` on success.

    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    error_response, region_id, target_date = _parse_share_request(request)
    if error_response is not None:
        return error_response

    try:
        region = resolve_region_for_bulletin(region_id)  # type: ignore[arg-type]
    except Http404:
        return JsonResponse({"error": "region_not_found"}, status=404)

    bulletin = select_bulletin_for_date(region, target_date)  # type: ignore[arg-type]
    if bulletin is None:
        return JsonResponse({"error": "bulletin_not_found"}, status=404)

    share = _create_share_with_retry(bulletin, region, target_date)  # type: ignore[arg-type]
    if share is None:
        return JsonResponse({"error": "token_collision"}, status=500)

    url = request.build_absolute_uri(
        reverse("public:share_redirect", args=[share.token])
    )
    return JsonResponse({"url": url})


# ---------------------------------------------------------------------------
# PWA shell contract — version + kill-switch config (SNOW-369, SNOW-372)
# ---------------------------------------------------------------------------


@require_GET
def version(request: HttpRequest) -> JsonResponse:
    """Server-authoritative version + kill state for the PWA client.

    Returns the fixed shape the offline-first spec requires so the client
    can (a) detect a new build to pick up (``current``), (b) enter a forced
    Update Required state when the server says so (``update_required``),
    and (c) trigger the Mechanism-A kill switch when ``kill`` is true.

    ``update_available`` (SNOW-869) is true when the request carried an
    ``X-Client-Version`` AND that value differs from ``APP_VERSION``. It
    is *equality*, never ordering, for the same reason ``update_required``
    is membership: a git SHA has no order (see
    ``docs/decisions/blocked-builds-not-a-version-floor.md``). It fails
    CLOSED on an unidentified client — no header means false — which is
    the mirror of ``update_required``'s fail-open: a client whose build we
    cannot read is never *told* it has an update we cannot confirm.

    SNOW-952: this is not the whole soft-banner verdict. It says the
    server has redeployed since this page was served, which is a statement
    about the SERVER; whether the *device* has anything to pick up is a
    statement about its shell, answered by ``shell`` below. The client
    needs both, and this one is the cheap pre-filter that decides whether
    to ask the second question at all.

    SNOW-869 also added a ``release`` label for the banner to print;
    SNOW-1025 removed that copy and SNOW-1026 removed the field.

    ``shell`` (SNOW-952) is the third identifier, and the one the update
    banner is actually gated on: the shell cache name this build would
    serve, derived from the shell content hash
    (``apps.core.sw_shell.served_cache_version``). The controlling service
    worker reports its own ``CACHE_VERSION`` to the page, and the two
    differ exactly when the device is holding a stale offline shell.

    The distinction is the whole of SNOW-952. ``APP_VERSION`` changes on
    every deploy, including one that touches only Python, so gating the
    banner on it interrupted every user after every deploy to offer them
    a reload whose entire effect was to swap one worker for another that
    behaved identically — which is how an escape hatch for a stuck worker
    becomes a thing people learn to dismiss. ``shell`` changes only when
    a shell source does. See
    ``docs/decisions/the-update-banner-is-gated-on-the-shell-not-the-build.md``.

    ``update_available`` is a function of ``X-Client-Version``, which is
    already in ``Vary`` below, so the 60-second edge cache is unaffected.

    ``update_required`` (SNOW-609) is the whole forced-update verdict; the
    client does no version arithmetic of its own. It is true only when the
    request carried an ``X-Client-Version`` header AND that value is listed
    in ``settings.APP_BLOCKED_VERSIONS``. Two properties are load-bearing:

    * **Membership, not ordering.** ``APP_VERSION`` resolves to a git SHA,
      and SHAs have no order, so a "minimum supported version" is not
      expressible on either side of the wire. Ops names the specific builds
      that must not keep running — see
      ``docs/decisions/blocked-builds-not-a-version-floor.md``.
    * **Fail open on an unidentified client.** No header means an empty
      string, which is never a member (``comma_separated_frozenset`` in
      ``config/settings/base.py`` drops empty entries), so a client we
      cannot identify is never blocked. A blocking modal on a client whose
      build we do not know has no recovery path.

    The header itself is sent by ``static/js/pwa_client_version.js``
    (SNOW-388) on every same-origin fetch / HTMX request.

    ``today`` (SNOW-920) is the server's date as ``YYYY-MM-DD``. The map
    compares it with the day its own page was rendered on and offers a
    Reload when they differ. It is a function of the clock, not of any
    request header, and the 60-second edge cache below bounds how far past
    midnight it can lag.

    The response is cacheable at the CDN for 60 seconds because the values
    only change on deploy or on an explicit ops flip — the client itself
    fetches this on cold start and every 15 minutes while active, so a
    stale entry at the edge is bounded by the polling window. ``Vary:
    X-Client-Version`` keeps one client's verdict out of another's cache
    entry now that the body depends on a request header.
    """
    client_version = request.headers.get("X-Client-Version", "")
    # ``!= ""`` rather than ``bool(client_version)``: semgrep's
    # nan-injection rule flags any ``bool()`` over request data, since a
    # numeric cast of "nan" is a real hazard. It isn't one here — this is a
    # string emptiness test — but an explicit comparison says the same
    # thing without needing an exemption. Both operands are bool, so the
    # ``and`` yields a bool for the JSON body either way.
    update_required = (
        client_version != "" and client_version in settings.APP_BLOCKED_VERSIONS
    )
    # Equality, never ordering — see the docstring. Same ``!= ""`` idiom as
    # above, and for the same semgrep reason.
    update_available = client_version != "" and client_version != settings.APP_VERSION
    response = JsonResponse(
        {
            "current": settings.APP_VERSION,
            # SNOW-952: the shell the banner is gated on. Recomputed under
            # DEBUG and cached per process otherwise, mirroring ``serve_sw``
            # exactly — this value is compared against the one that view
            # baked into the worker, so the two must be read the same way
            # or local development shows a permanent phantom update.
            "shell": served_cache_version(),
            "update_required": update_required,
            "update_available": update_available,
            # SNOW-920: the server's day, in the form the map page renders
            # as ``#season-scrubber[data-today]`` (``map_page`` uses
            # ``date.today()`` too, so the two can never disagree on a
            # server whose zone is not UTC). A page served from the worker's
            # cache after the network budget ran out carries the day it was
            # RENDERED on; this is what it is compared with. SNOW-793 keeps
            # the browser clock out of it.
            "today": date.today().isoformat(),
            "released_at": settings.APP_RELEASED_AT,
            "kill": bool(settings.SW_KILL),
        }
    )
    response["Cache-Control"] = "public, max-age=60"
    patch_vary_headers(response, ["X-Client-Version"])
    # SNOW-381 (spec §16.2): capture the endpoint hit so the observability
    # dashboards can chart poll cadence and detect clients that stop
    # phoning home.
    emit_server_signal(
        "pwa.version.endpoint.hit",
        {"client_version": client_version},
    )
    return response


@require_GET
def sw_config(request: HttpRequest) -> JsonResponse:
    """Kill-switch and SW-URL config consumed at SW-registration time.

    The client fetches this before ``navigator.serviceWorker.register`` so
    ops can (a) swap the active SW URL — e.g. from ``/sw.js`` to
    ``/sw-kill.js`` — without a code deploy, and (b) hard-disable the SW
    for every installed client by flipping ``SW_KILL=true`` in Render env.

    Never cache this response — the whole point is that the client sees the
    latest state on every launch. ``no-store`` is stronger than ``no-cache``
    and correct here because we don't want intermediaries to hold it either.
    """
    response = JsonResponse(
        {
            "sw_url": settings.SW_URL,
            "kill": bool(settings.SW_KILL),
        }
    )
    response["Cache-Control"] = "no-store"
    # SNOW-381 (spec §16.2): capture the endpoint hit so we can spot
    # SW-registration attempts that never proceed to install/activate.
    kill = bool(settings.SW_KILL)
    sw_config_properties: dict[str, object] = {
        "sw_url": settings.SW_URL,
        "kill": kill,
        # SNOW-384: client_version — see the identical note on
        # ``version()`` above. Client sends this header via
        # static/js/pwa_client_version.js (SNOW-388).
        "client_version": request.headers.get("X-Client-Version", ""),
    }
    if kill:
        # SNOW-384: "reason" identifies what drove the kill decision.
        # ``SW_KILL`` (config/settings/base.py) is the only kill trigger
        # today, so the reason is a fixed string. If a second trigger is
        # ever added (e.g. a per-version kill list), give it its own
        # reason string here rather than overloading this one.
        sw_config_properties["reason"] = "env_kill_switch"
    emit_server_signal("pwa.sw_config.hit", sw_config_properties)
    return response


# ---------------------------------------------------------------------------
# Per-bulletin JSON endpoints (SNOW-394)
# ---------------------------------------------------------------------------
# Two representations of a single stored bulletin, keyed by ``bulletin_id``
# (the CharField, not the auto PK). The two shapes are exposed as two
# distinct URLs — rather than one URL with ``Accept`` content negotiation —
# so each is advertised on the bulletin HTML page with its own
# ``<link rel="alternate" type="application/json">``, so generic
# fetchers (``Accept: */*``) still discover both, and so CDN caches key
# on URL alone without a ``Vary: Accept`` fragmentation.


@require_GET
@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
def bulletin_render_model(request: HttpRequest, bulletin_id: str) -> JsonResponse:
    """
    Serve the render model for one bulletin as JSON.

    The render model is Snowdesk's canonical, versioned view of the
    bulletin — the same shape the map tooltip and the season sheet
    consume. It is the shape a downstream consumer usually wants: no
    provider-specific CAAML idiosyncrasies, just the presentation-ready
    fields plus a schema version.

    Args:
        request: The incoming HTTP request (unused — the response is
            keyed on ``bulletin_id`` alone).
        bulletin_id: The public identifier of the bulletin (the
            ``Bulletin.bulletin_id`` CharField, not the auto PK).

    Returns:
        A ``JsonResponse`` with the render model as the body, or 404 if
        no bulletin exists for that id.

    """
    bulletin = get_object_or_404(Bulletin, bulletin_id=bulletin_id)
    return JsonResponse(bulletin.render_model or {})


@require_GET
@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
def bulletin_caaml(request: HttpRequest, bulletin_id: str) -> JsonResponse:
    """
    Serve the raw CAAML payload (in its GeoJSON Feature envelope) as JSON.

    Every provider's CAAML v6 JSON wrapped in a
    ``{"type": "Feature", "geometry": null, "properties": {…}}``
    envelope (see the ``geojson-feature-envelope`` ADR). Consumers who
    want just the CAAML take ``.properties``; consumers who want the
    Feature envelope take the whole payload.

    Since SNOW-1337 the CAAML is the ingest service's (``source_caaml``,
    matched on ``bulletin_id``): a ``Bulletin`` keeps none. An instance
    whose service holds no such bulletin (staging, which pulls its
    bulletins from production's feed) answers 404, as for an unknown id.

    Args:
        request: The incoming HTTP request (unused).
        bulletin_id: The public identifier of the bulletin.

    Returns:
        A ``JsonResponse`` with the payload as the body, or 404 when no
        bulletin, or no stored CAAML, exists for that id.

    Raises:
        Http404: No bulletin, or no stored CAAML, for ``bulletin_id``.

    """
    bulletin = get_object_or_404(Bulletin, bulletin_id=bulletin_id)
    caaml = source_caaml(bulletin.bulletin_id)
    if caaml is None:
        raise Http404("No CAAML is held for this bulletin.")
    return JsonResponse(caaml_envelope(caaml))
