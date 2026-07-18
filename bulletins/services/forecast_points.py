"""
bulletins/services/forecast_points.py — Snapping pins onto shared ForecastPoints.

Contains the quantisation helpers and the resolution entry point:

  quantise_lat(latitude) / quantise_lon(longitude) / quantise_elevation(elevation)
      Pure functions mapping a raw coordinate/elevation onto its grid cell
      or elevation band index. Use ``math.floor`` (not ``int()``) so
      negative coordinates quantise consistently — see
      ``docs/decisions/forecast-point-quantisation.md``.

  resolve_forecast_point(latitude, longitude)
      Given a raw pin location, fetches its elevation via
      ``bulletins.services.elevation.fetch_elevation``, then either reuses
      the nearest existing ``ForecastPoint`` within the reuse thresholds
      or creates a new one keyed on the quantised grid cell. Returns the
      resolved ``ForecastPoint``.

The reuse check runs before cell creation so that pins sitting near a
grid-cell boundary — whose quantised cell differs from a physically
nearby existing point's cell — still share a row rather than each minting
its own. Candidates are loaded from the full 3x3x3 neighbourhood of grid
cells (lat_cell +/-1, lon_cell +/-1, elevation_band +/-1) around the pin's
own cell so a genuinely close point in an adjacent cell is not missed.
"""

from __future__ import annotations

import logging
import math

from django.db import IntegrityError

from bulletins.models import ForecastPoint
from bulletins.services.elevation import fetch_elevation

logger = logging.getLogger(__name__)

# Grid cell sizes, in degrees. 0.01 degrees latitude is ~1.1 km; 0.015
# degrees longitude is chosen so the cell is roughly square at mid
# European latitudes (~46 degrees N), where cos(46 deg) ~= 0.69, so
# 0.015 * 0.69 ~= 0.0104 degrees of "effective" longitude width.
LAT_CELL_SIZE = 0.01
LON_CELL_SIZE = 0.015

# Elevation band size, in metres.
ELEVATION_BAND_SIZE = 200

# Reuse thresholds: a pin reuses the nearest existing ForecastPoint only if
# it falls within both of these.
REUSE_HORIZONTAL_THRESHOLD_M = 750
REUSE_ELEVATION_THRESHOLD_M = 150

_EARTH_RADIUS_KM = 6371.0088


def quantise_lat(latitude: float) -> int:
    """
    Map a latitude to its grid cell index.

    Args:
        latitude: Latitude in degrees.

    Returns:
        ``floor(latitude / LAT_CELL_SIZE)``.

    """
    return math.floor(latitude / LAT_CELL_SIZE)


def quantise_lon(longitude: float) -> int:
    """
    Map a longitude to its grid cell index.

    Args:
        longitude: Longitude in degrees.

    Returns:
        ``floor(longitude / LON_CELL_SIZE)``.

    """
    return math.floor(longitude / LON_CELL_SIZE)


def quantise_elevation(elevation: float) -> int:
    """
    Map an elevation to its elevation-band index.

    Args:
        elevation: Elevation in metres above sea level.

    Returns:
        ``floor(elevation / ELEVATION_BAND_SIZE)``.

    """
    return math.floor(elevation / ELEVATION_BAND_SIZE)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Return the great-circle distance in metres between two lat/lon pairs.

    Pure-Python haversine, mirroring ``mcp_server/resolvers.py::_haversine_km``
    (same earth radius constant). Kept as a local, private copy per the
    scope's touch list — see
    ``docs/decisions/forecast-point-quantisation.md`` for why a shared
    ``core`` extraction is deferred.

    Args:
        lat1: Latitude of point one, in degrees.
        lon1: Longitude of point one, in degrees.
        lat2: Latitude of point two, in degrees.
        lon2: Longitude of point two, in degrees.

    Returns:
        The great-circle distance in metres.

    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    distance_km = 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
    return distance_km * 1000


def _find_reusable_point(
    latitude: float,
    longitude: float,
    elevation: float,
    lat_cell: int,
    lon_cell: int,
    elevation_band: int,
) -> ForecastPoint | None:
    """
    Find the nearest existing ForecastPoint this pin can reuse, if any.

    Loads candidates from the 3x3x3 neighbourhood of grid cells around
    the pin's own cell, then returns the nearest one within both the
    horizontal and elevation reuse thresholds.

    Args:
        latitude: The pin's latitude in degrees.
        longitude: The pin's longitude in degrees.
        elevation: The pin's elevation in metres.
        lat_cell: The pin's quantised latitude cell.
        lon_cell: The pin's quantised longitude cell.
        elevation_band: The pin's quantised elevation band.

    Returns:
        The nearest ForecastPoint within the reuse thresholds, or ``None``
        if no candidate qualifies.

    """
    candidates = ForecastPoint.objects.filter(
        lat_cell__in=(lat_cell - 1, lat_cell, lat_cell + 1),
        lon_cell__in=(lon_cell - 1, lon_cell, lon_cell + 1),
        elevation_band__in=(elevation_band - 1, elevation_band, elevation_band + 1),
    )

    best: ForecastPoint | None = None
    best_distance_m = math.inf
    for candidate in candidates:
        if abs(candidate.elevation - elevation) > REUSE_ELEVATION_THRESHOLD_M:
            continue
        distance_m = _haversine_m(
            latitude, longitude, candidate.latitude, candidate.longitude
        )
        if distance_m > REUSE_HORIZONTAL_THRESHOLD_M:
            continue
        if distance_m < best_distance_m:
            best = candidate
            best_distance_m = distance_m

    return best


def resolve_forecast_point(latitude: float, longitude: float) -> ForecastPoint:
    """
    Resolve a raw pin location to a shared ForecastPoint, creating one if needed.

    Fetches the pin's elevation, then either reuses the nearest existing
    ForecastPoint within 750m horizontally and 150m in elevation, or
    creates a new row keyed on the pin's quantised grid cell.

    Args:
        latitude: The pin's latitude in degrees.
        longitude: The pin's longitude in degrees.

    Returns:
        The resolved (existing or newly created) ForecastPoint.

    Raises:
        requests.HTTPError: If the elevation lookup fails.

    """
    elevation = fetch_elevation(latitude, longitude)

    lat_cell = quantise_lat(latitude)
    lon_cell = quantise_lon(longitude)
    elevation_band = quantise_elevation(elevation)

    reusable = _find_reusable_point(
        latitude, longitude, elevation, lat_cell, lon_cell, elevation_band
    )
    if reusable is not None:
        logger.debug(
            "Reusing ForecastPoint id=%s for latitude=%s longitude=%s elevation=%s",
            reusable.pk,
            latitude,
            longitude,
            elevation,
        )
        return reusable

    try:
        point, created = ForecastPoint.objects.get_or_create(
            lat_cell=lat_cell,
            lon_cell=lon_cell,
            elevation_band=elevation_band,
            defaults={
                "latitude": latitude,
                "longitude": longitude,
                "elevation": elevation,
            },
        )
    except IntegrityError:
        # Race: another request created the same cell between our reuse
        # check and get_or_create. Re-fetch the winner's row.
        logger.debug(
            "IntegrityError creating ForecastPoint for cell=(%s, %s, %s); "
            "re-fetching the winner's row",
            lat_cell,
            lon_cell,
            elevation_band,
        )
        point = ForecastPoint.objects.get(
            lat_cell=lat_cell,
            lon_cell=lon_cell,
            elevation_band=elevation_band,
        )
        created = False

    logger.debug(
        "Resolved ForecastPoint id=%s created=%s for latitude=%s longitude=%s "
        "elevation=%s",
        point.pk,
        created,
        latitude,
        longitude,
        elevation,
    )
    return point
