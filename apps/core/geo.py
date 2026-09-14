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


def destination(
    latitude: float, longitude: float, bearing_deg: float, distance_m: float
) -> tuple[float, float]:
    """Return the point ``distance_m`` away on ``bearing_deg``.

    The inverse of ``haversine_m``, on the same sphere and with the same
    accuracy note: every caller works at the scale of a hillside, where a
    spherical earth is far below the precision of the answer being asked
    for. Added for SNOW-911, which probes the ground a short way uphill of
    a track and needs the coordinate of "80 m that way".

    Bearings are compass bearings — 0 is north, 90 is east — because that
    is what an ASPECT is (``apps/locations/services/terrain.py``), and the
    one caller derives its direction from one.

    Args:
        latitude: Latitude of the origin, in degrees.
        longitude: Longitude of the origin, in degrees.
        bearing_deg: Compass bearing to travel on, in degrees.
        distance_m: How far to travel, in metres.

    Returns:
        The destination as ``(latitude, longitude)`` in degrees, longitude
        normalised to [-180, 180].

    """
    angular = distance_m / EARTH_RADIUS_M
    lat_1 = math.radians(latitude)
    lon_1 = math.radians(longitude)
    bearing = math.radians(bearing_deg)

    sin_lat_2 = math.sin(lat_1) * math.cos(angular) + math.cos(lat_1) * math.sin(
        angular
    ) * math.cos(bearing)
    lat_2 = math.asin(max(-1.0, min(1.0, sin_lat_2)))
    lon_2 = lon_1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat_1),
        math.cos(angular) - math.sin(lat_1) * sin_lat_2,
    )
    # Normalised rather than left to wrap: a probe a few metres east of the
    # antimeridian is not a case this project has, but a longitude of 181
    # would be silently rejected by the grid projection rather than
    # answering for the ground it means.
    return math.degrees(lat_2), (math.degrees(lon_2) + 540) % 360 - 180


def initial_bearing_deg(
    latitude_1: float, longitude_1: float, latitude_2: float, longitude_2: float
) -> float | None:
    """Return the compass bearing from the first point towards the second.

    The missing companion to ``destination``: that one walks a bearing,
    this one measures it. Added for SNOW-964, which needs to know what a
    track is DOING with the ground under it — the angle between the
    direction of travel and the fall line — and the direction of travel
    is the bearing of one segment's chord.

    The INITIAL bearing, which on a great circle changes as it is
    followed. Over a segment of a ski track — tens of metres — the
    difference between the initial and the final bearing is far below
    the tenth of a degree anything here is rounded to.

    Compass bearings, like ``destination``'s and like an aspect's: 0 is
    north, 90 is east.

    Args:
        latitude_1: Latitude of the origin, in degrees.
        longitude_1: Longitude of the origin, in degrees.
        latitude_2: Latitude of the target, in degrees.
        longitude_2: Longitude of the target, in degrees.

    Returns:
        The bearing in ``[0, 360)``, or **None for coincident points**.
        ``atan2(0, 0)`` is ``0.0``, so the arithmetic would happily
        answer "due north" for a chord that has no direction at all —
        the guess ``apps.routes.services.bulletin_join``'s ``octant_for``
        refuses to make for the same reason. Not a theoretical case: a
        stored coordinate is rounded to six decimal places, so a track
        that doubles back on itself can produce two identical boundaries.

    """
    if latitude_1 == latitude_2 and longitude_1 == longitude_2:
        return None

    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)
    delta_lambda = math.radians(longitude_2 - longitude_1)
    y = math.sin(delta_lambda) * math.cos(phi_2)
    x = math.cos(phi_1) * math.sin(phi_2) - math.sin(phi_1) * math.cos(
        phi_2
    ) * math.cos(delta_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
