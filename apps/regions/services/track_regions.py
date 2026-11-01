"""
apps/regions/services/track_regions.py — The regions a stored track crosses (SNOW-945).

A route or trip used to be placed in the one region its START sat in
(SNOW-947). A tour that climbs out of one region and descends into another
crosses two bulletins, so the trip page, the Plan page and the alerts now
read every region the track crosses, in the order it crosses them.

``resolve_track_regions`` walks the stored ``[[lon, lat, ele], …]`` track
at ``REGION_STRIDE_M`` and asks ``point_match.regions_for_points`` which
region each sample sits in — one query, then cheap point-in-polygon tests.
The walk is densified rather than run on the stored vertices because
Douglas-Peucker thinning leaves long straight legs: one can cut a region's
corner with no vertex inside it.

The answer is stored as an ordered list of ``MicroRegion.region_id`` on
``Route.crossed_regions`` and ``Trip.crossed_regions``. ``None`` there means
"not yet resolved" (a row from before ``backfill_crossed_regions``); ``[]``
means the track is outside coverage — an answer about coverage, not about
the ground. ``track_regions`` reads the stored list back as rows, resolving
live when it is ``None`` so every page is right before the backfill runs.

See docs/decisions/a-route-carries-the-regions-it-crosses.md.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from apps.regions.models import MicroRegion
from apps.regions.services.point_match import regions_for_points
from track.slope_samples import stride_coordinates

logger = logging.getLogger(__name__)

# Distance between samples along the track. Fine enough that a corner cut
# by a thinned straight leg is still sampled; coarse enough that a 20 km
# tour is ~400 cheap tests.
REGION_STRIDE_M = 50.0


def _samples(points: Sequence[Sequence[Any]]) -> list[tuple[float, float]]:
    """Return the ``(lat, lon)`` samples to test along a stored track.

    Args:
        points: The stored track as ``[[lon, lat, ele], …]``.

    Returns:
        ``(latitude, longitude)`` pairs, start first. A one-point track
        yields its only point — ``stride_coordinates`` returns nothing for
        fewer than two points, so it is special-cased here rather than
        left to the helper.

    """
    if not points:
        return []
    if len(points) == 1:
        lon, lat = points[0][0], points[0][1]
        return [(float(lat), float(lon))]
    track = [list(point) for point in points]
    return [(lat, lon) for lon, lat in stride_coordinates(track, REGION_STRIDE_M)]


def resolve_track_regions(points: Sequence[Sequence[Any]] | None) -> list[str]:
    """Return the region ids a stored track crosses, in first-entered order.

    Args:
        points: The stored track as ``[[lon, lat, ele], …]``, or None.

    Returns:
        ``MicroRegion.region_id`` values, de-duplicated in the order the
        track first enters each — A → B → A gives ``["A", "B"]``. Empty
        when the track is empty or outside every region.

    """
    samples = _samples(points or [])
    ordered: list[str] = []
    seen: set[str] = set()
    for region in regions_for_points(samples):
        if region is None or region.region_id in seen:
            continue
        seen.add(region.region_id)
        ordered.append(region.region_id)
    return ordered


def track_regions(
    stored: list[str] | None, points: Sequence[Sequence[Any]] | None
) -> list[MicroRegion]:
    """Return the regions a route or trip crosses, as rows, in route order.

    Args:
        stored: The row's ``crossed_regions``. ``None`` means not yet
            resolved: the list is worked out live from ``points`` and NOT
            written, so a page reads correctly before the backfill.
        points: The row's stored track, used only when ``stored`` is None.

    Returns:
        The MicroRegions in the stored order. An id that no longer exists
        (an EAWS boundary change since) is dropped.

    """
    ids = resolve_track_regions(points) if stored is None else list(stored)
    if not ids:
        return []
    by_id = {
        region.region_id: region
        for region in MicroRegion.objects.filter(region_id__in=ids)
    }
    return [by_id[region_id] for region_id in ids if region_id in by_id]
