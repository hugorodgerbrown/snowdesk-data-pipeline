"""
apps/regions/services/point_match.py — Pure-Python point-in-polygon matching.

Provides ``point_in_polygon`` (ray-casting) and ``region_for_point`` (global
point→MicroRegion resolver, used by the GPS-gated field-report feature and
by favourite placement).

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
    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

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
    # apps.regions.models → apps.regions.services is fine at import time, but
    # apps.regions.services.point_match is also imported by observations which
    # imports apps.regions.models — keep it deferred.
    from apps.regions.models import MicroRegion  # noqa: PLC0415

    # select_related because _sq_distance reads centroid_location for every
    # candidate — without it the pre-sort is an N+1 across every region that
    # has a boundary, which is the whole table.
    candidates = list(
        MicroRegion.objects.exclude(boundary__isnull=True).select_related(
            "centroid_location"
        )
    )

    # Order by squared Euclidean distance from centre (cheap proxy for proximity).
    def _sq_distance(region: "MicroRegion") -> float:
        """Return the squared distance from the region centre to (lat, lon)."""
        centre = region.centre_point()
        if centre is None:
            # Sorts last. Only the ordering is affected — point_in_polygon
            # below is still the answer, so a region with no centre is
            # tested late rather than missed.
            return float("inf")
        centre_lat, centre_lon = centre
        dlon = centre_lon - lon
        dlat = centre_lat - lat
        return dlon * dlon + dlat * dlat

    candidates.sort(key=_sq_distance)

    for region in candidates:
        if point_in_polygon(lon, lat, region.boundary):
            return region

    return None


def regions_for_points(
    points: list[tuple[float, float]],
) -> list["MicroRegion | None"]:
    """Return the MicroRegion containing each of many points.

    The batch form of ``region_for_point``, added for SNOW-839, which asks
    the question once per segment of a track — several hundred times for
    one tour. Calling the single-point version in a loop would re-run its
    **whole-table query** each time; this runs it once and then answers
    from memory.

    **THE LAST ANSWER IS TRIED FIRST**, which is what makes the walk
    cheap. Consecutive samples along a track are 25 m apart, so they are
    almost always in the region the one before was in — a hit there costs
    a single point-in-polygon test, and only a genuine crossing pays for
    the sorted scan. A track that stayed in one region used to cost N
    queries and N sorted scans; it now costs one query and N cheap tests.

    Args:
        points: ``(latitude, longitude)`` pairs, in any order, though a
            track's own order is what makes the cache above pay.

    Returns:
        One entry per point, in the same order: the containing
        MicroRegion, or None where the point is outside every known
        region. **None is an answer about our coverage, not about the
        ground** — a caller must not read it as "no bulletin applies".

    """
    from apps.regions.models import MicroRegion  # noqa: PLC0415

    if not points:
        return []

    candidates = list(
        MicroRegion.objects.exclude(boundary__isnull=True).select_related(
            "centroid_location"
        )
    )

    found: list[MicroRegion | None] = []
    previous: MicroRegion | None = None
    for latitude, longitude in points:
        if previous is not None and point_in_polygon(
            longitude, latitude, previous.boundary
        ):
            found.append(previous)
            continue
        # A miss falls back to the full scan, ordered nearest-centre-first
        # exactly as the single-point form does.
        match = _first_containing(candidates, latitude, longitude)
        found.append(match)
        if match is not None:
            previous = match
    return found


def _first_containing(
    candidates: list["MicroRegion"], latitude: float, longitude: float
) -> "MicroRegion | None":
    """Return the first candidate whose boundary contains a point.

    Args:
        candidates: Regions with a boundary, already loaded.
        latitude: Latitude of the point.
        longitude: Longitude of the point.

    Returns:
        The containing region, or None.

    """

    def _sq_distance(region: "MicroRegion") -> float:
        """Return the squared distance from the region centre to the point."""
        centre = region.centre_point()
        if centre is None:
            return float("inf")
        centre_lat, centre_lon = centre
        dlon = centre_lon - longitude
        dlat = centre_lat - latitude
        return dlon * dlon + dlat * dlat

    for region in sorted(candidates, key=_sq_distance):
        if point_in_polygon(longitude, latitude, region.boundary):
            return region
    return None
