"""
apps/core/geo.py — Shared great-circle distance on a spherical earth.

Companion to ``apps/core/coordinates.py``, which centralises WGS-84
*validation*; this module does the same for *distance*. Four apps needed the
same haversine and each grew its own copy — weather's forecast-point reuse
search, the MCP server's region proximity scan, the observations proximity
filter, and the GPX track-length sum. Every copy carried a comment
acknowledging the others, which is a duplication that has stopped being
deliberate (SNOW-708).

Pure Python, no Shapely and no PostGIS: the observations and MCP callers run
on the request path, where ``docs/decisions/pure-python-point-in-polygon.md``
keeps that dependency out.

Coordinate arguments are ordered ``(latitude, longitude)`` — latitude first —
matching the project convention (SNOW-426). ``apps/routes/services/gpx.py``
holds ``(lon, lat)`` tuples because that is GeoJSON's order, so it swaps at
the call site rather than this module offering a second argument order.

Accuracy: a sphere, not an ellipsoid, so ~0.5% at continental distances. Every
caller here works at the scale of a mountain range or smaller — a 750 m
forecast-cell reuse threshold, a 25 km observation radius, a GPX leg between
consecutive trackpoints — where the error is far below the precision of the
coordinates being compared.
"""

from __future__ import annotations

import math

# Mean earth radius (IUGG), in kilometres and in metres. The same physical
# value expressed twice so that each unit costs one multiplication rather
# than a multiplication plus a conversion, which is what the four copies this
# module replaces all did.
EARTH_RADIUS_KM = 6371.0088
EARTH_RADIUS_M = 6_371_008.8


def _central_angle(
    latitude_1: float, longitude_1: float, latitude_2: float, longitude_2: float
) -> float:
    """
    Return the angle subtended at the earth's centre by two points, in radians.

    The unit-free half of the haversine: multiply by a radius to get a
    distance. Split out so that the kilometre and metre entry points share one
    formula instead of restating it, and so neither pays a unit conversion.

    Args:
        latitude_1: Latitude of the first point, in degrees.
        longitude_1: Longitude of the first point, in degrees.
        latitude_2: Latitude of the second point, in degrees.
        longitude_2: Longitude of the second point, in degrees.

    Returns:
        The central angle in radians, in ``[0, pi]``.

    """
    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)
    delta_phi = math.radians(latitude_2 - latitude_1)
    delta_lambda = math.radians(longitude_2 - longitude_1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * math.asin(math.sqrt(a))


def haversine_km(
    latitude_1: float, longitude_1: float, latitude_2: float, longitude_2: float
) -> float:
    """
    Return the great-circle distance between two WGS-84 points, in kilometres.

    Args:
        latitude_1: Latitude of the first point, in degrees.
        longitude_1: Longitude of the first point, in degrees.
        latitude_2: Latitude of the second point, in degrees.
        longitude_2: Longitude of the second point, in degrees.

    Returns:
        The great-circle distance in kilometres; ``0.0`` for identical points.

    """
    return EARTH_RADIUS_KM * _central_angle(
        latitude_1, longitude_1, latitude_2, longitude_2
    )


def haversine_m(
    latitude_1: float, longitude_1: float, latitude_2: float, longitude_2: float
) -> float:
    """
    Return the great-circle distance between two WGS-84 points, in metres.

    Args:
        latitude_1: Latitude of the first point, in degrees.
        longitude_1: Longitude of the first point, in degrees.
        latitude_2: Latitude of the second point, in degrees.
        longitude_2: Longitude of the second point, in degrees.

    Returns:
        The great-circle distance in metres; ``0.0`` for identical points.

    """
    return EARTH_RADIUS_M * _central_angle(
        latitude_1, longitude_1, latitude_2, longitude_2
    )
