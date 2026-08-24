"""
apps/weather/services/forecast_cells.py — Snapping pins onto shared ForecastCells.

Contains the quantisation helpers and the resolution entry point:

  quantise_lat(latitude) / quantise_lon(longitude) / quantise_elevation(elevation)
      Pure functions mapping a raw coordinate/elevation onto its grid cell
      or elevation band index. Use ``math.floor`` (not ``int()``) so
      negative coordinates quantise consistently — see
      ``docs/decisions/forecast-point-quantisation.md``.

  resolve_forecast_cell(latitude, longitude)
      Given a raw pin location, fetches its elevation via
      ``apps.weather.services.elevation.fetch_elevation``, then either reuses
      the nearest existing ``ForecastCell`` within the reuse thresholds
      or creates a new one keyed on the quantised grid cell. Returns the
      resolved ``ForecastCell``. **Writes.**

  find_forecast_cell(latitude, longitude, elevation)
      The read-only twin of the above, for a ``--commit``-less backfill
      pass: makes the same decision and returns the cell that would be
      used, or ``None`` where one would be created. Never writes, and takes
      the already-fetched elevation rather than looking it up again.

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

from apps.core.coordinates import validate_coordinates
from apps.core.geo import haversine_m
from apps.weather.models import ForecastCell
from apps.weather.services.elevation import fetch_elevation

logger = logging.getLogger(__name__)

# Grid cell sizes, in degrees. 0.01 degrees latitude is ~1.1 km; 0.015
# degrees longitude is chosen so the cell is roughly square at mid
# European latitudes (~46 degrees N), where cos(46 deg) ~= 0.69, so
# 0.015 * 0.69 ~= 0.0104 degrees of "effective" longitude width.
LAT_CELL_SIZE = 0.01
LON_CELL_SIZE = 0.015

# Elevation band size, in metres.
ELEVATION_BAND_SIZE = 200

# Reuse thresholds: a pin reuses the nearest existing ForecastCell only if
# it falls within both of these.
REUSE_HORIZONTAL_THRESHOLD_M = 750
REUSE_ELEVATION_THRESHOLD_M = 150


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


def _find_reusable_cell(
    latitude: float,
    longitude: float,
    elevation: float,
    lat_cell: int,
    lon_cell: int,
    elevation_band: int,
) -> ForecastCell | None:
    """
    Find the nearest existing ForecastCell this pin can reuse, if any.

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
        The nearest ForecastCell within the reuse thresholds, or ``None``
        if no candidate qualifies.

    """
    candidates = ForecastCell.objects.filter(
        lat_cell__in=(lat_cell - 1, lat_cell, lat_cell + 1),
        lon_cell__in=(lon_cell - 1, lon_cell, lon_cell + 1),
        elevation_band__in=(elevation_band - 1, elevation_band, elevation_band + 1),
    )

    best: ForecastCell | None = None
    best_distance_m = math.inf
    for candidate in candidates:
        if abs(candidate.elevation - elevation) > REUSE_ELEVATION_THRESHOLD_M:
            continue
        distance_m = haversine_m(
            latitude, longitude, candidate.latitude, candidate.longitude
        )
        if distance_m > REUSE_HORIZONTAL_THRESHOLD_M:
            continue
        if distance_m < best_distance_m:
            best = candidate
            best_distance_m = distance_m

    return best


def find_forecast_cell(
    latitude: float, longitude: float, elevation: float
) -> ForecastCell | None:
    """
    Return the ForecastCell this pin would resolve to, without creating one.

    The read-only twin of ``resolve_forecast_cell``, for the ``--commit``-less
    pass of a backfill command. It must mirror that function's decision
    exactly or a preview reports a cost the commit run does not incur, so it
    repeats **both** of its lookups in the same order: the reuse check over
    the 3x3x3 neighbourhood, then the exact grid-cell key that
    ``get_or_create`` would otherwise satisfy with a ``get``.

    Elevation is an argument rather than a lookup because every caller has
    already fetched it. ``resolve_forecast_cell`` takes the coordinates alone
    and fetches its own, which costs a second Open-Meteo call on a path that
    has one in hand.

    Args:
        latitude: The pin's latitude in degrees.
        longitude: The pin's longitude in degrees.
        elevation: The pin's elevation in metres, as already resolved by
            ``apps.weather.services.elevation.fetch_elevation``.

    Returns:
        The ForecastCell ``resolve_forecast_cell`` would return for these
        coordinates, or ``None`` when it would create a new one.

    Raises:
        InvalidCoordinatesError: If the coordinates are non-finite or out of
            range — rejected here for the same reason as in
            ``resolve_forecast_cell``, so a preview fails on bad input rather
            than silently reporting "would create".

    """
    validate_coordinates(latitude, longitude)

    lat_cell = quantise_lat(latitude)
    lon_cell = quantise_lon(longitude)
    elevation_band = quantise_elevation(elevation)

    reusable = _find_reusable_cell(
        latitude, longitude, elevation, lat_cell, lon_cell, elevation_band
    )
    if reusable is not None:
        return reusable

    # Not reusable, but the exact key may still be taken: a row in this
    # grid cell more than REUSE_HORIZONTAL_THRESHOLD_M away fails the reuse
    # check and is still what get_or_create would hand back.
    return ForecastCell.objects.filter(
        lat_cell=lat_cell, lon_cell=lon_cell, elevation_band=elevation_band
    ).first()


def resolve_forecast_cell(
    latitude: float, longitude: float, elevation: float | None = None
) -> ForecastCell:
    """
    Resolve a raw pin location to a shared ForecastCell, creating one if needed.

    Fetches the pin's elevation, then either reuses the nearest existing
    ForecastCell within 750m horizontally and 150m in elevation, or
    creates a new row keyed on the pin's quantised grid cell.

    Args:
        latitude: The pin's latitude in degrees.
        longitude: The pin's longitude in degrees.
        elevation: The pin's elevation in metres, when the caller already
            holds it. Backfill commands fetch it for the ``Location`` row
            itself and would otherwise pay a second Open-Meteo call here for
            the same answer. Omit it and this fetches its own.

    Returns:
        The resolved (existing or newly created) ForecastCell.

    Raises:
        InvalidCoordinatesError: If the coordinates are non-finite or out of
            range (defensive — callers should validate at the view layer, but
            this must never reach the Open-Meteo call or the DB). SNOW-464.
        requests.HTTPError: If the elevation lookup fails.

    """
    # Defence in depth: reject NaN/±Inf / out-of-range coords before the
    # external elevation lookup and any DB write.
    validate_coordinates(latitude, longitude)

    if elevation is None:
        elevation = fetch_elevation(latitude, longitude)

    lat_cell = quantise_lat(latitude)
    lon_cell = quantise_lon(longitude)
    elevation_band = quantise_elevation(elevation)

    reusable = _find_reusable_cell(
        latitude, longitude, elevation, lat_cell, lon_cell, elevation_band
    )
    if reusable is not None:
        # Logged by quantised cell key, never by raw coordinates. A pin's
        # latitude/longitude is a precise personal location — often a
        # user's own saved place — and this is the debug log of a path
        # every favourite goes through. The key is what identifies the row
        # anyway, so this is both safer and more useful (SNOW-718).
        logger.debug(
            "Reusing ForecastCell id=%s for cell=(%s, %s, %s)",
            reusable.pk,
            lat_cell,
            lon_cell,
            elevation_band,
        )
        return reusable

    # Race safety: if another request creates the same cell between our
    # reuse check and this call, Django's own get_or_create already catches
    # the resulting IntegrityError in a savepoint and re-fetches by the
    # lookup kwargs (which are exactly the unique key here) — no bespoke
    # handling needed on top of that.
    cell, created = ForecastCell.objects.get_or_create(
        lat_cell=lat_cell,
        lon_cell=lon_cell,
        elevation_band=elevation_band,
        defaults={
            "latitude": latitude,
            "longitude": longitude,
            "elevation": elevation,
        },
    )

    logger.debug(
        "Resolved ForecastCell id=%s created=%s for cell=(%s, %s, %s)",
        cell.pk,
        created,
        lat_cell,
        lon_cell,
        elevation_band,
    )
    return cell
