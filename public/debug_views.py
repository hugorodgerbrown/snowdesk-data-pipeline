"""
public/debug_views.py — SNOW-45 scrubber perf spike harness.

Throwaway debug page + an experimental per-date JSON endpoint that let
us measure the cost of swapping per-date state on the /map/ choropleth
at drag speed. Lives on ``feature/SNOW-45-scrubber-perf-spike`` and is
deleted once the findings comment is posted on the Linear ticket.

The whole-season bundle endpoint that started life here was promoted
into ``public/api.py`` as ``api:season_ratings`` (SNOW-46) — the
timelapse debug button and the future scrubber both depend on it, so
it is no longer DEBUG-only.

Every view in this module is gated on ``settings.DEBUG`` — in production
they raise ``Http404`` before any query runs. Matches the inline-DEBUG
pattern used for the raw-bulletin debug panel in ``public/views.py``.

Endpoints exposed while DEBUG is on::

    GET /debug/scrubber-perf/          — harness page (MapLibre + controls)
    GET /api/debug/day-ratings/?date=YYYY-MM-DD
                                        — {region_id: rating_int} for a date
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from pipeline.models import RegionDayRating

logger = logging.getLogger(__name__)

# Compact int encoding for the choropleth. Duplicated from
# ``public/api.py`` rather than imported because that module's copy is
# the long-lived one — this whole module gets deleted with the perf
# spike and the duplication keeps that teardown clean.
_RATING_TO_INT: dict[str, int] = {
    RegionDayRating.Rating.NO_RATING: 0,
    RegionDayRating.Rating.LOW: 1,
    RegionDayRating.Rating.MODERATE: 2,
    RegionDayRating.Rating.CONSIDERABLE: 3,
    RegionDayRating.Rating.HIGH: 4,
    RegionDayRating.Rating.VERY_HIGH: 5,
}


def _require_debug() -> None:
    """Raise Http404 unless ``settings.DEBUG`` is on."""
    if not settings.DEBUG:
        raise Http404("Debug harness is only available when DEBUG=True.")


def scrubber_perf(request: HttpRequest) -> HttpResponse:
    """
    Render the scrubber perf harness page.

    Loads MapLibre + the regions GeoJSON layer and exposes measurement
    functions on ``window.__perf`` for console-driven runs. See
    ``static/js/debug/scrubber_perf.js`` for the harness itself.
    """
    _require_debug()
    return render(
        request,
        "debug/scrubber_perf.html",
        {
            "regions_url": reverse("api:regions_geojson"),
            "day_ratings_url": reverse("api:debug_day_ratings"),
            "season_ratings_url": reverse("api:season_ratings"),
            "basemap_style_url": settings.BASEMAP_STYLE_URL,
        },
    )


def day_ratings_debug(request: HttpRequest) -> JsonResponse:
    """
    Return ``{region_id: rating_int}`` for a single date.

    Query params:
        date: ISO date (YYYY-MM-DD). Defaults to today when omitted.

    The shape is deliberately minimal — the scrubber only needs the tile
    colour, so each entry is a single int on the danger scale (0–5).
    Encoded via ``_RATING_TO_INT``. Regions with no row for the date are
    simply absent.
    """
    _require_debug()
    raw_date = request.GET.get("date", "")
    if raw_date:
        try:
            target = dt.date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({"error": "invalid_date"}, status=400)
    else:
        target = dt.date.today()

    rows = RegionDayRating.objects.filter(date=target).values_list(
        "region__region_id", "max_rating"
    )
    payload = {region_id: _RATING_TO_INT[rating] for region_id, rating in rows}
    return JsonResponse(payload)
