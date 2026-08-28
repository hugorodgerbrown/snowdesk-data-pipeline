"""
apps/downloads/views.py — sync endpoints for offline download areas (SNOW-749).

Three HTMX-only fragment views plus one plain-JSON read, backing the
cross-device half of SNOW-749:

- ``area_sync`` (POST) — record one completed download against the account.
  An ``update_or_create`` on ``(user, area_id)``, so a mutation-queue replay
  updates the row it already wrote rather than duplicating it. That is the
  idempotency guarantee this endpoint relies on: it holds without
  ``IdempotencyMiddleware``, which only covers a replay carrying the same
  ``Idempotency-Key``.
- ``area_rename`` (POST) — rename a custom area.
- ``area_forget`` (POST) — drop the account row. Note the name: this does
  NOT evict the device's tiles, which the client does separately and
  locally. The manage sheet's two destructive verbs map onto exactly this
  split — the trash calls both, "free up space" calls only the client half,
  and SNOW-586's automatic budget eviction calls only the client half too,
  which is what lets an evicted area survive as a one-tap re-download.

and one plain-JSON endpoint:

- ``areas_json`` (GET) — every area on the requesting user's account, for
  static/js/downloads_sync.js. Not ``@require_htmx`` — consumed by a
  ``fetch()``, not an HTMX swap. Mirrors ``routes/routes.geojson``.

**What this module deliberately does not gate.** Reading a download that is
already on the device needs no account and never touches this app: the
tiles are in Cache Storage and the record is in IndexedDB. A session
expiring or a user signing out must leave both untouched, because the whole
point of a downloaded area is that it works with no signal — and a signal
is exactly what checking a session would need. The authentication gate here
covers *starting* a download and syncing it, nothing else.

**Two validation rules worth stating up front**, because both were got
wrong first:

- A posted bbox is **priced**, not merely range-checked. Bounding each
  ordinate to a valid lon/lat leaves ``[-179, -89, 179, 89]`` acceptable,
  and that box is 357 million tiles across the micro band. ``_clean_bbox``
  therefore prices it against the shared ``DOWNLOAD_CEILING_MB`` from
  ``apps.regions.services.basemap_tiles`` — the same constant
  ``static/js/basemap_download_core.js`` mirrors and the framing control
  already enforces, imported rather than restated so there is one ceiling
  and not three. This matters because a stored bbox is replayed on another
  device by ``openFramingAt``: a box we know to be undownloadable would be
  a row whose only purpose is to be acted on and cannot be.
- An over-long ``name``, ``region_id`` or ``basemap_key`` is **refused**,
  never truncated, in both write paths. See ``_too_long_error``.

All four views are authentication-gated (403 for anonymous) and owner-scoped
via ``DownloadArea.objects.for_user()``. An area id belonging to somebody
else returns 404, never 403, so a probing request cannot distinguish "not
yours" from "doesn't exist". This mirrors ``apps.routes.views``, which
mirrors ``apps.favourites.views`` — the reference implementation for the
shape.

``area_sync`` applies django-ratelimit (30/m, keyed on ``user``). Higher
than ``route_create``'s 10/m because a client coming back online can
legitimately drain a queue of several areas at once, and because the write
is a single small upsert rather than an XML parse.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.core.decorators import require_htmx
from apps.regions.services.basemap_tiles import (
    DOWNLOAD_CEILING_MB,
    MICRO_BAND,
    WORST_CASE_BYTES_PER_TILE,
    tile_count,
    tile_ranges,
)

from .models import DownloadArea

logger = logging.getLogger(__name__)

# The shape a client-minted area id may take: ``region-<region_id>`` or
# ``custom-<uuid>``. Anchored and character-limited because this value
# arrives from the client, is stored, and is later echoed back into the
# JSON another device reads — validating it at the door is cheaper than
# trusting every downstream reader to. Matches the ids
# ``basemap_download_core.js``'s ``areaIdForRegion`` and
# ``generateCustomAreaId`` produce.
_AREA_ID_RE = re.compile(r"^(region|custom)-[A-Za-z0-9_-]{1,88}$")

# Longest accepted ``name``, mirroring ``DownloadArea.name``'s max_length.
_NAME_MAX_LENGTH = 100

# Longest accepted ``region_id`` and ``basemap_key``, each mirroring its own
# column's ``max_length``.
_REGION_ID_MAX_LENGTH = 50
_BASEMAP_KEY_MAX_LENGTH = 50

# Bytes in a megabyte, 1024-based — matching ``basemap_tiles.py``'s own
# arithmetic and the JS twin's. Every size a user has been shown for a
# download is computed this way, so a decimal megabyte here would price the
# same box differently from the control that framed it.
_BYTES_PER_MB = 1024 * 1024


def _kind_for_area_id(area_id: str) -> str:
    """Return the ``DownloadArea.KIND`` an area id implies.

    The prefix is not decoration — it is what ``basemap_download_core.js``'s
    ``isCustomAreaId`` reads on the client to decide which of the two local
    records an id belongs to. Deriving the kind here rather than trusting a
    separate posted field keeps the two sides from ever disagreeing about
    what a given id is.

    Args:
        area_id: A client-minted area id, already matched against
            ``_AREA_ID_RE``.

    Returns:
        ``DownloadArea.KIND.REGION`` or ``DownloadArea.KIND.CUSTOM``.

    """
    if area_id.startswith("custom-"):
        return DownloadArea.KIND.CUSTOM
    return DownloadArea.KIND.REGION


def _bbox_download_mb(bbox: list[float]) -> float:
    """Return the worst-case download size of ``bbox``, in megabytes.

    The same arithmetic ``build_blob`` performs for a region, applied to a
    posted box: expand it into per-zoom tile ranges across the micro band,
    count the tiles, and price them at the worst-case bytes-per-tile figure
    SNOW-631 calibrated. Every constant is imported from
    ``apps.regions.services.basemap_tiles`` rather than restated here —
    there is one download ceiling in this system, and it is mirrored in
    ``static/js/basemap_download_core.js`` as it is, so a second one would
    be a limit no control was designed against.

    Deliberately a worst case, not an estimate of what a run would really
    fetch. It over-reads on sparse alpine terrain by three to five times,
    which is the trade that keeps a single constant that never
    under-promises — and under-promising is the direction that matters for
    a ceiling.

    Args:
        bbox: ``[west, south, east, north]`` in degrees, already validated
            as a non-empty in-range box.

    Returns:
        Megabytes (1024-based).

    """
    tiles = tile_count(tile_ranges(bbox, *MICRO_BAND))
    return tiles * WORST_CASE_BYTES_PER_TILE / _BYTES_PER_MB


def _clean_bbox(raw: str) -> list[float] | None:
    """Parse and validate a posted bbox, or return None.

    Returns None for anything that is not four finite numbers describing a
    non-empty box in ``[west, south, east, north]`` (lon, lat) order, in
    range, AND small enough to be downloadable. A rejected bbox is not an
    error the caller has to surface — a region area legitimately has none —
    so the two callers decide for themselves whether its absence matters.

    **Two separate checks, because the coordinate range is not a size
    limit.** Bounding each ordinate to valid lon/lat leaves
    ``[-179, -89, 179, 89]`` perfectly acceptable, and that box is 357
    million tiles across the micro band — roughly 17 TB at the worst-case
    rate. The size check is what actually stops it.

    The size check exists because a stored bbox is not inert data: another
    device replays it through ``openFramingAt`` in
    static/js/map_downloads_manager.js, which fits the map to it and offers
    a download. A box this server knows to be undownloadable is a row whose
    only purpose is to be acted on and cannot be, so it is refused at the
    door rather than stored for a second device to discover.

    It is deliberately the SAME ceiling the framing control already
    enforces client-side (``basemap_download_core.js``'s
    ``DOWNLOAD_CEILING_MB``, mirroring the constant imported here), so no
    box a legitimate client can frame is ever refused here. This is the
    backstop for a client that is not the one we ship — not a second,
    tighter policy.

    Args:
        raw: The posted JSON string.

    Returns:
        The four floats, or None if the value is unusable.

    """
    try:
        parsed = json.loads(raw)
    except TypeError, ValueError:
        return None
    if not isinstance(parsed, list) or len(parsed) != 4:
        return None
    try:
        west, south, east, north = (float(value) for value in parsed)
    except TypeError, ValueError:
        return None
    if not all(
        value == value and abs(value) != float("inf")
        for value in (west, south, east, north)
    ):
        return None
    if not (-180.0 <= west < east <= 180.0):
        return None
    if not (-90.0 <= south < north <= 90.0):
        return None
    bbox = [west, south, east, north]
    # Last, because it is the only check here that does real work — five
    # zoom levels of index arithmetic — and every cheaper rejection above
    # has already run.
    size_mb = _bbox_download_mb(bbox)
    if size_mb > DOWNLOAD_CEILING_MB:
        logger.info(
            "Download area bbox refused: %.0f MB exceeds the %d MB ceiling",
            size_mb,
            DOWNLOAD_CEILING_MB,
        )
        return None
    return bbox


def _too_long_error(name: str, region_id: str, basemap_key: str) -> str | None:
    """Return the 400 message for the first over-long field, or None.

    Over-long values are REFUSED, not truncated — and the same way
    ``area_rename`` refuses one. The two write paths used to disagree:
    this one silently clipped each value to its column width while the
    other 400d at the identical limit.

    Rejecting is right in both, and the argument is not consistency for its
    own sake. A clipped value is stored, echoed back to every other device
    and read as what the user meant. A truncated ``region_id`` is worse
    still: it names a region that does not exist, so the area arrives on
    another device un-downloadable — exactly the class of row
    ``_clean_bbox`` refuses an over-large box for.

    Rejecting costs nothing a legitimate client can hit, including a queued
    one that cannot be retried. The rename field carries
    ``maxlength="100"`` (includes/_ugc_panel_row.html), a region id is an
    EAWS code, and a basemap key is a ``settings.BASEMAP_STYLES`` key — so
    anything over these limits came from a client we did not ship, and
    failing it loudly is the honest answer.

    Args:
        name: The posted display name.
        region_id: The posted EAWS micro-region id.
        basemap_key: The posted basemap picker key.

    Returns:
        A message naming the offending field and its limit, or None when
        every value fits.

    """
    for value, limit, label in (
        (name, _NAME_MAX_LENGTH, "name"),
        (region_id, _REGION_ID_MAX_LENGTH, "region_id"),
        (basemap_key, _BASEMAP_KEY_MAX_LENGTH, "basemap_key"),
    ):
        if len(value) > limit:
            return f"{label} must be at most {limit} characters."
    return None


def _serialise(area: DownloadArea) -> dict[str, Any]:
    """Return one area as the client's JSON shape.

    Deliberately not a full model dump. ``bytes`` and ``band`` are absent
    because they are not stored (see ``models.py`` for why), and no internal
    primary key is exposed — ``area_id`` is the only identifier either side
    addresses a row by.

    Args:
        area: The row to serialise.

    Returns:
        A JSON-safe dict.

    """
    return {
        "area_id": area.area_id,
        "kind": area.kind,
        "region_id": area.region_id,
        "bbox": area.bbox,
        "basemap_key": area.basemap_key,
        "name": area.name,
        "created_at": area.created_at.isoformat(),
    }


@require_GET
def areas_json(request: HttpRequest) -> HttpResponse:
    """Return every download area on the requesting user's account.

    The client unions this into its own local records to work out which
    areas are on THIS device and which are only on the account. An
    anonymous request gets 403 rather than an empty list: "you are signed
    out" and "you have no areas" are different facts, and the client paints
    them differently.

    Args:
        request: The incoming GET request.

    Returns:
        ``{"areas": [...]}``, or 403 for an anonymous request.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    areas = DownloadArea.objects.for_user(request.user)
    return JsonResponse({"areas": [_serialise(area) for area in areas]})


@require_htmx
@require_POST
@ratelimit(key="user", rate="30/m", block=False)
def area_sync(request: HttpRequest) -> HttpResponse:
    """Record one completed download against the requesting user's account.

    Validates in cheapest-first order — auth, rate limit, area id shape,
    per-user cap, then the payload itself.

    The per-user cap is checked only for an area id the account does not
    already hold: re-syncing an area at the cap is an update, and refusing
    it would strand a user who is merely re-downloading something they
    already have.

    Errors:
        400 — non-HTMX request; malformed area id; a custom area with no
            usable bbox (absent, malformed, out of range, or larger than
            ``DOWNLOAD_CEILING_MB`` — see ``_clean_bbox``); an over-long
            ``name``, ``region_id`` or ``basemap_key``.
        403 — anonymous request.
        409 — the user has reached ``settings.DOWNLOAD_AREAS_MAX_PER_USER``.
        429 — rate limit exceeded.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        The stored area as JSON, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before syncing another area.",
            status=429,
        )

    area_id = request.POST.get("area_id", "")
    if not _AREA_ID_RE.match(area_id):
        return HttpResponse("area_id is malformed.", status=400)

    kind = _kind_for_area_id(area_id)
    bbox = _clean_bbox(request.POST.get("bbox", ""))
    region_id = request.POST.get("region_id", "")
    basemap_key = request.POST.get("basemap_key", "")
    name = request.POST.get("name", "")
    if kind == DownloadArea.KIND.CUSTOM and bbox is None:
        # Without a bbox there is nothing another device could download —
        # the row would list an area it could never act on, which is worse
        # than not listing it.
        return HttpResponse("A custom area needs a valid bbox.", status=400)
    if kind == DownloadArea.KIND.REGION and not region_id:
        return HttpResponse("A region area needs a region_id.", status=400)

    too_long = _too_long_error(name=name, region_id=region_id, basemap_key=basemap_key)
    if too_long:
        return HttpResponse(too_long, status=400)

    # One condition, short-circuited, not two nested ones: the cap is only
    # consulted for an area the account does NOT already hold, and `and`
    # says so while guaranteeing the `count()` is skipped in the common
    # re-sync case.
    owned = DownloadArea.objects.for_user(request.user)
    if (
        not owned.filter(area_id=area_id).exists()
        and owned.count() >= settings.DOWNLOAD_AREAS_MAX_PER_USER
    ):
        logger.info("Download area sync blocked: user=%s hit the cap", request.user.pk)
        return HttpResponse("You have reached the saved-area limit.", status=409)

    area, _created = DownloadArea.objects.update_or_create(
        user=request.user,
        area_id=area_id,
        defaults={
            "kind": kind,
            # A region's own bbox stays null — its tiles are computed
            # server-side from its real boundary, so a box would be a
            # second, coarser answer to a question already answered.
            "region_id": region_id if kind == DownloadArea.KIND.REGION else "",
            "bbox": bbox if kind == DownloadArea.KIND.CUSTOM else None,
            "basemap_key": basemap_key,
            "name": name,
        },
    )
    return JsonResponse(_serialise(area))


@require_htmx
@require_POST
def area_rename(request: HttpRequest, area_id: str) -> HttpResponse:
    """Rename a custom area on the requesting user's account.

    Regions are not renameable — a region's name is its own — so a rename
    aimed at one is refused rather than silently stored where nothing would
    ever read it.

    Errors:
        400 — non-HTMX request; over-long name; the area is a region.
        403 — anonymous request.
        404 — no such area on this account.

    Args:
        request: The incoming HTMX POST request. Expects a ``name`` field.
        area_id: The client-minted area id, from the URL.

    Returns:
        The updated area as JSON, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        area = DownloadArea.objects.for_user(request.user).get(area_id=area_id)
    except DownloadArea.DoesNotExist:
        return HttpResponse("Area not found.", status=404)

    if area.kind != DownloadArea.KIND.CUSTOM:
        return HttpResponse("A region area cannot be renamed.", status=400)

    # Refused, not truncated. Both write paths go through
    # ``_too_long_error`` now — they used to disagree, this one refusing
    # while ``area_sync`` silently clipped — so the limit and the message
    # have one owner and cannot drift apart again. The empty arguments are
    # the fields this path does not take.
    name = request.POST.get("name", "")
    too_long = _too_long_error(name=name, region_id="", basemap_key="")
    if too_long:
        return HttpResponse(too_long, status=400)

    area.name = name
    # updated_at is auto_now — it must be listed in update_fields or the
    # column is left stale, since save(update_fields=...) skips every field
    # not named (auto_now is applied in Python, not by the DB).
    area.save(update_fields=["name", "updated_at"])

    return JsonResponse(_serialise(area))


@require_htmx
@require_POST
def area_forget(request: HttpRequest, area_id: str) -> HttpResponse:
    """Drop one area from the requesting user's account.

    The device's own tiles and local record are NOT touched — the client
    evicts those separately, and the two halves are deliberately separate
    verbs (see this module's docstring). A repeat call on an area already
    forgotten returns 404, which the client treats as success: the row is
    gone either way, which is what was asked for.

    Errors:
        400 — non-HTMX request.
        403 — anonymous request.
        404 — no such area on this account.

    Args:
        request: The incoming HTMX POST request.
        area_id: The client-minted area id, from the URL.

    Returns:
        204 on success, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    deleted, _details = (
        DownloadArea.objects.for_user(request.user).filter(area_id=area_id).delete()
    )
    if not deleted:
        return HttpResponse("Area not found.", status=404)

    return HttpResponse(status=204)
