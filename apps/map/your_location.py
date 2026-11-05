"""
apps/map/your_location.py — the ground under a locate-me fix (SNOW-1395).

A tap on the map's blue locate-me dot opens the "Your location" sheet
(``place_sheet.location_sheet_partial``), whose title is the ground under
the fix: its height, the way it faces and how steep it is. This module reads
that ground.

It reads the terrain height tileset (swissALTI3D, 5 m, Switzerland) through
``track.terrain`` — the same grid, Horn kernel and 10 m window the
terrain-class tiles are cut with, so the slope band the sheet names is the
one the map's slope layer paints there
(docs/decisions/slope-layer-is-painted-from-our-class-tiles.md). Outside that
grid, or with the origin unreachable, the height falls back to the device's
own altitude reading, and the sheet names no aspect or slope: a device
altitude says nothing about the ground around it.

Nothing here keeps the fix. The coordinate arrives on a GET, is read, and is
gone with the request.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from django.conf import settings

from track.geo import OCTANTS, octant_for
from track.terrain import sample_height, sample_slope
from track.terrain_grid import load_grid

logger = logging.getLogger(__name__)

# The width of one slope band, in degrees — the terrain-class tiles' band.
SLOPE_BAND_DEG = 5

# The first band the slope layer paints (30°), as SLOPE_PALETTE does in
# static/js/slope_classes_core.js and STEEP_BAND in bulletin_terrain_core.js.
STEEP_BAND = 6

GROUND_TERRAIN = "terrain"
GROUND_DEVICE = "device"


@dataclass(frozen=True)
class Ground:
    """What is known about the ground under one fix.

    Attributes:
        height_m: The height in whole metres, or None when neither the
            terrain model nor the device gave one.
        octant: The aspect as an octant name ("NE"), or None on level
            ground, off the terrain model, or with no terrain reading.
        slope_band: ``floor(slope / 5)``, or None with no terrain reading.
        source: ``GROUND_TERRAIN``, ``GROUND_DEVICE``, or None.

    """

    height_m: int | None
    octant: str | None
    slope_band: int | None
    source: str | None

    @property
    def octant_index(self) -> int | None:
        """Return the aspect as the tiles' octant index, N=0 … NW=7.

        Returns:
            The index, or None when there is no aspect.

        """
        return OCTANTS.index(self.octant) if self.octant else None

    @property
    def slope_class(self) -> int | None:
        """Return the slope layer's class for the band, 0 (30–35°) … 4 (50°+).

        Returns:
            The class the layer's palette colours, or None under 30° and
            with no band — ground the slope layer leaves unpainted.

        """
        if self.slope_band is None or self.slope_band < STEEP_BAND:
            return None
        return min(self.slope_band - STEEP_BAND, 4)

    @property
    def slope_label(self) -> str:
        """Return the slope band as the slope layer names it, e.g. "30–35°".

        Returns:
            The band's bounds in degrees, or "" with no band.

        """
        if self.slope_band is None:
            return ""
        low = self.slope_band * SLOPE_BAND_DEG
        return f"{low}–{low + SLOPE_BAND_DEG}°"


def read_ground(
    latitude: float, longitude: float, device_altitude: float | None = None
) -> Ground:
    """Read the ground under one fix.

    NEVER RAISES: ``track.terrain`` answers every failure with a reason,
    which here becomes the device fallback.

    Args:
        latitude: The fix's latitude in degrees.
        longitude: The fix's longitude in degrees.
        device_altitude: The device's own altitude reading in metres, or
            None when it gave none.

    Returns:
        The ground, from the terrain model where it covers the fix, else
        the device's height alone, else nothing.

    """
    grid = load_grid(settings.TERRAIN_TILE_BASE_URL)
    height = sample_height(grid, latitude, longitude)
    if height.is_known and height.height_m is not None:
        slope = sample_slope(grid, latitude, longitude)
        band = (
            int(slope.angle_deg // SLOPE_BAND_DEG)
            if slope.angle_deg is not None
            else None
        )
        return Ground(
            height_m=round(height.height_m),
            octant=octant_for(slope.aspect_deg),
            slope_band=band,
            source=GROUND_TERRAIN,
        )
    # The fix is a reader's position: it is never logged, only the reason.
    logger.debug("your location: no terrain height (%s)", height.unknown)
    if device_altitude is not None and math.isfinite(device_altitude):
        return Ground(
            height_m=round(device_altitude),
            octant=None,
            slope_band=None,
            source=GROUND_DEVICE,
        )
    return Ground(height_m=None, octant=None, slope_band=None, source=None)
