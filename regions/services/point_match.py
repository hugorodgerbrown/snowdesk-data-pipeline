"""
regions/services/point_match.py — Pure-Python point-in-polygon classification.

Provides ``point_in_polygon`` (ray-casting), ``classify_match``
(region-relative subscriber classification: in_region / in_neighbour /
elsewhere / unknown), and ``region_for_point`` (global point→MicroRegion
resolver used by the GPS-gated field-report feature).

Deliberately uses no Shapely or GDAL.  Shapely is a dev-only dependency
(used lazily by ``audit_resort_regions``); promoting it to the request path
would add a non-trivial C extension to the production image for a task that
a 20-line implementation handles adequately.  See
``docs/decisions/pure-python-point-in-polygon.md`` for full rationale.

GeoJSON coordinate convention: [longitude, latitude] pairs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Match-kind constants
# ---------------------------------------------------------------------------
# Plain strings so regions/ keeps no dependency on accounts/.
# Subscription.GeoMatchKind literal values MUST match these — a unit test in
# tests/regions/services/test_point_match.py guards against drift.

IN_REGION: str = "in_region"
IN_NEIGHBOUR: str = "in_neighbour"
ELSEWHERE: str = "elsewhere"
UNKNOWN: str = "unknown"


# ---------------------------------------------------------------------------
# Ray-casting algorithm
# ---------------------------------------------------------------------------


def _ray_cast_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Return True if the point (lon, lat) is inside the given ring.

    Uses the standard ray-casting (Jordan curve) algorithm.  The ray is
    cast horizontally from the point towards +∞ along the latitude axis;
    an odd number of crossings with ring edges indicates containment.

    On-boundary behaviour is implementation-defined — not guaranteed to
    return True or False.  This is acceptable because GeoLite2 coordinates
    carry kilometre-scale accuracy and the boundary itself is sub-metre.

    Args:
        lon: Longitude of the test point (GeoJSON x-axis).
        lat: Latitude of the test point (GeoJSON y-axis).
        ring: A GeoJSON linear ring — a list of [lon, lat] pairs whose
              first and last positions are identical.

    Returns:
        True when the point is inside the ring.

    """
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        # Crossing condition: the edge straddles the horizontal ray.
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_polygon_rings(
    lon: float, lat: float, rings: list[list[list[float]]]
) -> bool:
    """Test containment against a GeoJSON Polygon ring list.

    Ring 0 is the exterior; rings 1+ are holes.  A point is inside the
    polygon when it is inside the exterior ring AND not inside any hole.

    Args:
        lon: Longitude of the test point.
        lat: Latitude of the test point.
        rings: Coordinate array for a GeoJSON Polygon geometry — a list
               of rings, each a list of [lon, lat] pairs.

    Returns:
        True when the point is inside the polygon (and not in a hole).

    """
    if not rings:
        return False
    if not _ray_cast_ring(lon, lat, rings[0]):
        return False
    # Point is inside the exterior ring — now check holes.
    for hole in rings[1:]:
        if _ray_cast_ring(lon, lat, hole):
            return False
    return True


def point_in_polygon(lon: float, lat: float, geometry: dict | None) -> bool:
    """Return True when (lon, lat) lies inside the GeoJSON geometry.

    Handles GeoJSON ``Polygon`` and ``MultiPolygon`` types.  Returns
    ``False`` for ``None``, missing or malformed input — never raises.

    Args:
        lon: Longitude of the test point (WGS-84).
        lat: Latitude of the test point (WGS-84).
        geometry: A GeoJSON Polygon or MultiPolygon geometry dict, or None.

    Returns:
        True when the point is inside the geometry; False otherwise.

    """
    if not geometry:
        return False

    try:
        geo_type = geometry.get("type")
        coordinates = geometry.get("coordinates")

        if not geo_type or not coordinates:
            return False

        if geo_type == "Polygon":
            return _point_in_polygon_rings(lon, lat, coordinates)

        if geo_type == "MultiPolygon":
            return any(
                _point_in_polygon_rings(lon, lat, rings) for rings in coordinates
            )

        # Unsupported geometry type (e.g. Point, LineString) — not a polygon.
        logger.debug("point_in_polygon: unsupported geometry type %s", geo_type)
        return False

    except TypeError, IndexError, KeyError, AttributeError:
        logger.debug(
            "point_in_polygon: malformed geometry — returning False",
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Region-relative classification
# ---------------------------------------------------------------------------


def classify_match(
    lon: float | None,
    lat: float | None,
    target: "MicroRegion",
) -> tuple[str, "MicroRegion | None"]:
    """Classify a geo point relative to a target MicroRegion.

    Returns a ``(kind, matched_region)`` pair in a single pass:

    - ``(UNKNOWN, None)``           — coordinates are None.
    - ``(IN_REGION, target)``       — point is inside target.boundary.
    - ``(IN_NEIGHBOUR, neighbour)`` — point is inside a neighbour's boundary.
    - ``(ELSEWHERE, None)``         — point does not fall inside target or
                                       any of its neighbours.

    ``target.neighbours.all()`` is evaluated lazily; the first neighbour
    that contains the point short-circuits the loop, so average query
    overhead is one FK lookup plus an early-exit scan of the neighbour set.

    Args:
        lon: Longitude of the subscriber's geolocation, or None.
        lat: Latitude of the subscriber's geolocation, or None.
        target: The MicroRegion the subscriber is subscribing to.

    Returns:
        Tuple of (kind_constant, matched_region_or_none).

    """
    if lon is None or lat is None:
        return UNKNOWN, None

    if point_in_polygon(lon, lat, target.boundary):
        return IN_REGION, target

    for neighbour in target.neighbours.all():
        if point_in_polygon(lon, lat, neighbour.boundary):
            return IN_NEIGHBOUR, neighbour

    return ELSEWHERE, None


# ---------------------------------------------------------------------------
# Global point→region resolver (SNOW-324 field reports)
# ---------------------------------------------------------------------------


def region_for_point(lat: float, lon: float) -> "MicroRegion | None":
    """Return the MicroRegion that contains the given (lat, lon) point.

    Performs a best-effort, full-scan match against all MicroRegions that
    have a non-null boundary geometry.  Candidates are ordered by the
    squared Euclidean distance from their ``centre`` point so that the most
    likely containing region is tested first, short-circuiting early on the
    common case where the user is somewhere in the Alps.

    Squared distance is used instead of true great-circle distance because
    the ordering only needs to be monotone in distance — the expensive trig
    is avoided for the vast majority of submissions that match on the first
    or second candidate.

    Cost: one DB query (all regions with a boundary) plus up to N
    Python-level point-in-polygon tests.  In practice the nearest-centre
    ordering means 1–2 polygon tests suffice.  Acceptable given that
    submissions are rare and rate-limited (5 per minute per IP).  A spatial
    index can be added later if volume grows.

    Args:
        lat: Latitude of the GPS fix (WGS-84).
        lon: Longitude of the GPS fix (WGS-84).

    Returns:
        The first MicroRegion whose boundary contains the point, or None
        when no match is found (e.g. the point is outside all known regions).

    """
    # Import here to avoid a module-level circular dependency:
    # regions.models → regions.services is fine at import time, but
    # regions.services.point_match is also imported by observations which
    # imports regions.models — keep it deferred.
    from regions.models import MicroRegion  # noqa: PLC0415

    candidates = list(MicroRegion.objects.exclude(boundary__isnull=True))

    # Order by squared Euclidean distance from centre (cheap proxy for proximity).
    def _sq_distance(region: "MicroRegion") -> float:
        """Return the squared distance from the region centre to (lat, lon)."""
        centre = region.centre
        if not centre:
            return float("inf")
        dlon = float(centre.get("lon", 0.0)) - lon
        dlat = float(centre.get("lat", 0.0)) - lat
        return dlon * dlon + dlat * dlat

    candidates.sort(key=_sq_distance)

    for region in candidates:
        if point_in_polygon(lon, lat, region.boundary):
            return region

    return None
