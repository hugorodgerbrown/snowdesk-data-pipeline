"""
favourites/views.py — HTMX endpoints + GeoJSON layer for the favourites app.

Provides three HTMX-only fragment views and one plain-JSON endpoint used by
the map page's saved-pins feature (SNOW-413):

- ``favourite_create`` (POST) — validates lat/lon/name, creates a
  Favourite, returns the saved-pin partial.
- ``favourite_rename`` (POST) — owner-checked rename of an existing
  Favourite, returns the updated partial.
- ``favourite_delete`` (POST) — owner-checked deletion of a Favourite.
- ``favourites_geojson`` (GET) — a FeatureCollection of the requesting
  user's own favourites, for the map's saved-pins layer. Not
  ``@require_htmx`` — this is consumed by a JS ``fetch()`` call, not an
  HTMX swap.

All four are:
  - flag-gated on ``favourites`` (404 when inactive);
  - authentication-gated (403 for anonymous users).

``favourite_create`` additionally applies django-ratelimit (10/m, keyed on
``user`` since these endpoints are auth-only) and returns 429 when the
limit is exceeded.

Coordinate-argument convention: every function in this module takes
latitude/longitude in that order — ``(latitude, longitude)`` — matching
``favourites.services``.
"""

from __future__ import annotations

import logging
from typing import Any

import waffle
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from core.decorators import require_htmx
from favourites.models import Favourite
from favourites.services import (
    FavouriteLimitReached,
    create_favourite,
    delete_favourite,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_favourites_flag(request: HttpRequest) -> None:
    """Raise Http404 unless the ``favourites`` waffle flag is active.

    Mirrors the pattern used by ``observations.views._require_field_observations_flag``.
    Flag is seeded with ``superusers=True`` by
    ``favourites/migrations/0002_seed_favourites_flag.py``; extend / disable
    via ``/admin/waffle/flag/favourites/``.

    Args:
        request: The current HTTP request.

    Raises:
        Http404: When the flag is inactive for the current request.

    """
    from django.http import Http404  # noqa: PLC0415

    if not waffle.flag_is_active(request, "favourites"):
        raise Http404("favourites flag is inactive for this request.")


def _parse_latlon(
    lat_str: str | None, lon_str: str | None
) -> tuple[float, float] | None:
    """Parse latitude and longitude strings into floats.

    Returns None when either value is missing or unparseable.

    Args:
        lat_str: Raw latitude string from the request POST body.
        lon_str: Raw longitude string from the request POST body.

    Returns:
        ``(latitude, longitude)`` float pair, or None on failure.

    """
    if not lat_str or not lon_str:
        return None
    try:
        return float(lat_str), float(lon_str)
    except ValueError, TypeError:
        return None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def favourite_create(request: HttpRequest) -> HttpResponse:
    """Create a Favourite and return the saved-pin partial.

    Validates the POST body:
    - ``lat`` / ``lon`` must be present and parseable (400 otherwise).
    - ``name`` is optional.
    - Rate-limited to 10 creations per minute per user (429 on excess).

    When the user has reached ``settings.FAVOURITES_MAX_PER_USER``, renders
    ``_favourite_limit.html`` at HTTP 200 (so HTMX swaps the error message
    into the target) instead of creating a row.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered saved-pin or limit-reached partial, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before saving another pin.",
            status=429,
        )

    coords = _parse_latlon(request.POST.get("lat"), request.POST.get("lon"))
    if coords is None:
        return HttpResponse("Valid lat and lon are required.", status=400)

    latitude, longitude = coords
    name = request.POST.get("name", "")

    try:
        favourite = create_favourite(request.user, latitude, longitude, name=name)
    except FavouriteLimitReached:
        logger.info("Favourite create blocked: user=%s hit the cap", request.user.pk)
        return render(
            request, "favourites/partials/_favourite_limit.html", {}, status=200
        )

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
def favourite_rename(request: HttpRequest, uuid: Any) -> HttpResponse:
    """Rename an existing Favourite owned by the requesting user.

    Args:
        request: The incoming HTMX POST request. Expects a ``name`` field.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        Rendered updated saved-pin partial, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        favourite = Favourite.objects.for_user(request.user).get(uuid=uuid)
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    favourite.name = request.POST.get("name", "")
    favourite.save(update_fields=["name"])

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
def favourite_delete(request: HttpRequest, uuid: Any) -> HttpResponse:
    """Delete an existing Favourite owned by the requesting user.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        An empty 200 response on success, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        delete_favourite(request.user, uuid)
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    return HttpResponse("")


def favourites_geojson(request: HttpRequest) -> JsonResponse:
    """Return a FeatureCollection of the requesting user's own favourites.

    Each feature is a Point with GeoJSON-ordered ``coordinates: [lon, lat]``
    (RFC 7946) and properties ``uuid`` and ``name``. Not ``@require_htmx`` —
    consumed by the map's saved-pins layer via a JS ``fetch()`` call, not an
    HTMX swap.

    The response is marked ``Cache-Control: private, no-store`` — the
    inverse of the public ``resorts_geojson`` layer — since this payload is
    per-user and must never be shared across users by an intermediate
    cache.

    Args:
        request: The incoming GET request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 403/404 error.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    features: list[dict[str, Any]] = []
    for favourite in Favourite.objects.for_user(request.user).iterator():
        # GeoJSON ordering: [longitude, latitude] per RFC 7946.
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [favourite.longitude, favourite.latitude],
                },
                "properties": {
                    "uuid": str(favourite.uuid),
                    "name": favourite.name,
                },
            }
        )

    response = JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response
