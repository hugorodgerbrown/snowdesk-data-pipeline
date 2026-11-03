"""
apps/regions/services/resort_edit.py — rules shared by the resort write paths.

Two places create a resort from a coordinate: the map editor's
``edit_resort_create`` (``apps.public.api``) and the ops MCP server's
``create_resort`` (``apps.mcp_server.resort_tools``, SNOW-1401). Both need
the same two answers, so they are stated once here:

* ``canton_for_region`` — the canton a new resort in a region inherits when
  none is given, voted on by the resorts already there.
* ``nearby_resorts`` — the resorts whose pin is within a radius of a point,
  nearest first, which the MCP create preview lists as likely duplicates.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from apps.regions.models import MicroRegion, Resort
from track.geo import haversine_km

logger = logging.getLogger(__name__)

# How close an existing pin must be to count as a likely duplicate of a
# resort being created. Two separate resorts this close are rare (they are
# usually one domain's villages), so a hit is worth a look, not a refusal.
DUPLICATE_RADIUS_KM: float = 2.0


@dataclass(frozen=True)
class NearbyResort:
    """An existing resort near a point, and how far away its pin is."""

    resort: Resort
    distance_km: float


def canton_for_region(region: MicroRegion) -> str:
    """Return the canton the resorts already in ``region`` agree on.

    Canton is very nearly a function of the parent region — across the
    curated set, 65 of the 66 regions holding resorts have one canton
    between them, and the exception is a border resort recorded as a
    compound ("BE/VS") rather than a genuine disagreement. So a resort
    being created into a populated region can inherit the answer instead
    of asking the operator to re-type it.

    Nothing derives this from geometry: the polygons are EAWS warning
    regions, not cantonal boundaries. This is the sibling rows voting,
    which is why both callers keep an override — a border case, or a
    region whose existing rows are mis-tagged, needs the operator's
    answer to win.

    Args:
        region: The region the pin landed in.

    Returns:
        The most common non-blank canton among the region's resorts,
        ties broken alphabetically so the result is stable. ``""`` when
        the region has no resorts to read from — the caller then asks for
        the canton explicitly.

    """
    cantons = [
        canton
        for canton in region.resorts.values_list("canton", flat=True)
        if canton.strip()
    ]
    if not cantons:
        return ""
    # ``most_common`` keeps insertion order on ties; sorting first makes
    # the tie-break alphabetical and therefore reproducible.
    return Counter(sorted(cantons)).most_common(1)[0][0]


def nearby_resorts(
    latitude: float,
    longitude: float,
    radius_km: float = DUPLICATE_RADIUS_KM,
) -> list[NearbyResort]:
    """Return the resorts whose pin is within ``radius_km`` of a point.

    A full scan of the pinned resorts — a few hundred rows, one query —
    with a great-circle distance per row. Touring terrain is included: a
    book's "resort" may well be a row the sheet already holds under that
    kind.

    Args:
        latitude: The point's latitude (WGS-84).
        longitude: The point's longitude (WGS-84).
        radius_km: How far to look, in kilometres.

    Returns:
        The resorts in range, nearest first.

    """
    hits: list[NearbyResort] = []
    for resort in Resort.objects.geocoded().select_related("region", "location"):
        pin = resort.pin_coordinates
        if pin is None:
            continue
        distance = haversine_km(latitude, longitude, pin[0], pin[1])
        if distance <= radius_km:
            hits.append(NearbyResort(resort=resort, distance_km=distance))
    hits.sort(key=lambda hit: hit.distance_km)
    return hits
