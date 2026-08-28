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


def _clean_bbox(raw: str) -> list[float] | None:
    """Parse and validate a posted bbox, or return None.

    Returns None for anything that is not four finite numbers describing a
    non-empty, in-range box in ``[west, south, east, north]`` (lon, lat)
    order. A rejected bbox is not an error the caller has to surface — a
    region area legitimately has none — so the two callers decide for
    themselves whether its absence matters.

    The range check is what stops a hostile or buggy client storing a box
    another device would later expand into an enormous tile list: at zoom
    14 a whole-world bbox is a quarter of a billion tiles.

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
    return [west, south, east, north]


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
            usable bbox.
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
    region_id = request.POST.get("region_id", "")[:50]
    if kind == DownloadArea.KIND.CUSTOM and bbox is None:
        # Without a bbox there is nothing another device could download —
        # the row would list an area it could never act on, which is worse
        # than not listing it.
        return HttpResponse("A custom area needs a valid bbox.", status=400)
    if kind == DownloadArea.KIND.REGION and not region_id:
        return HttpResponse("A region area needs a region_id.", status=400)

    owned = DownloadArea.objects.for_user(request.user)
    if not owned.filter(area_id=area_id).exists():
        if owned.count() >= settings.DOWNLOAD_AREAS_MAX_PER_USER:
            logger.info(
                "Download area sync blocked: user=%s hit the cap", request.user.pk
            )
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
            "basemap_key": request.POST.get("basemap_key", "")[:50],
            "name": request.POST.get("name", "")[:_NAME_MAX_LENGTH],
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

    name = request.POST.get("name", "")
    if len(name) > _NAME_MAX_LENGTH:
        return HttpResponse(
            f"name must be at most {_NAME_MAX_LENGTH} characters.", status=400
        )

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
