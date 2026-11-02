"""
apps/regions/services/geometry.py — ring winding for served region polygons.

SNOW-1375 draws a band of each region's danger colour just inside its edge,
as a MapLibre line with a positive ``line-offset``, which for a polygon is
an inset. That reading needs every ring in one winding, and RFC 7946
§3.1.6 is the one this serves: outer rings counter-clockwise, holes
clockwise.
The stored EAWS fixtures are mixed (measured 2 Nov 2026 across the four
``eaws_*.json`` files: 746 outer rings clockwise and 50 counter-clockwise;
98 holes clockwise and 130 counter-clockwise), so ``/api/regions.geojson``
rewinds every geometry it serves through :func:`rfc7946_rewind`.

Pure functions over GeoJSON-shaped dicts. Longitude/latitude are treated as
planar for the sign of a ring's area, which is all winding needs: at the
scale of an avalanche region the sign never flips.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

Ring = list[list[float]]


def ring_area(ring: Ring) -> float:
    """Return a ring's signed area by the shoelace formula.

    Args:
        ring: A closed or open ring of ``[x, y]`` positions.

    Returns:
        Positive for a counter-clockwise ring, negative for clockwise, 0
        for a degenerate one.

    """
    total = 0.0
    count = len(ring)
    for i in range(count):
        # The modulo closes an open ring; on a closed one (last == first)
        # the wrapped term is zero, so both shapes give the same area.
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % count][0], ring[(i + 1) % count][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _wound(ring: Ring, counter_clockwise: bool) -> Ring:
    """Return ``ring`` in the asked-for direction, reversing it if needed.

    Args:
        ring: The ring to orient.
        counter_clockwise: True for an outer ring, False for a hole.

    Returns:
        The ring itself when already so wound (or degenerate), else a
        reversed copy.

    """
    area = ring_area(ring)
    if area == 0 or (area > 0) == counter_clockwise:
        return ring
    return list(reversed(ring))


def _rewind_polygon(rings: list[Ring]) -> list[Ring]:
    """Orient one polygon's rings: the first outward, the rest as holes.

    Args:
        rings: The polygon's rings, outer ring first.

    Returns:
        The rings, RFC 7946 wound.

    """
    return [_wound(ring, counter_clockwise=(i == 0)) for i, ring in enumerate(rings)]


def rfc7946_rewind(geometry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a Polygon or MultiPolygon wound to RFC 7946.

    Any other geometry, or ``None``, comes back unchanged. The input is
    never mutated, so a cached or ORM-held boundary stays as stored.

    Args:
        geometry: A GeoJSON geometry dict.

    Returns:
        A new geometry dict with outer rings counter-clockwise and holes
        clockwise, or the input as given.

    """
    if not geometry:
        return geometry
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Polygon" and isinstance(coords, list):
        return {**geometry, "coordinates": _rewind_polygon(coords)}
    if kind == "MultiPolygon" and isinstance(coords, list):
        return {**geometry, "coordinates": [_rewind_polygon(p) for p in coords]}
    return geometry
