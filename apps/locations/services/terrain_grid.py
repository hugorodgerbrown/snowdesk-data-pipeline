"""
apps/locations/services/terrain_grid.py — the terrain grid's geometry.

Contains one dataclass, one loader, the projection and the addressing:

  TerrainGrid
      The published definition of the height tileset — cell size, tile
      size, the skirt, the height encoding, the sources.

  load_grid()
      Fetches and caches ``grid.json``. Returns None when it cannot.

  project(latitude, longitude)
      WGS84 lat/lon to EPSG:3035 easting/northing, in pure Python.

  global_cell(grid, easting, northing)
      Which cell of the whole grid a projected point falls in.

  tile_of_cell(grid, cell_x, cell_y)
      Which tile owns a cell, and where in that tile it sits.

  offset_in_tile(grid, cell_x, cell_y, tile_x, tile_y)
      Where a cell sits relative to a tile that may only hold it in its
      skirt — or None when that tile does not hold it at all.

  stored_index(grid, row, column)
      The index of a cell in a decoded tile's flat 258x258 array.

**READ THE GRID, NEVER HARDCODE IT.** Every number this module works with
comes from ``grid.json``. The tileset can be rebuilt at a different cell
size, tile size or height scale, and a sampler applying yesterday's
constants to today's bytes DOES NOT FAIL — it returns heights that are
plausible and wrong, which is the worst outcome available. The constants
that are hardcoded here are the ones that cannot change without the grid
being a different grid: the EPSG:3035 projection parameters.

**There is no committed fallback copy of ``grid.json``.** A stale local
copy is precisely the silent drift above, dressed as resilience. When the
definition cannot be fetched, callers get ``UNAVAILABLE`` and say so. (The
copy under ``tests/locations/fixtures/terrain/`` is a test fixture, which
is a different thing: it is asserted against, not shipped.)

**The projection is pure Python.** Ellipsoidal Lambert Azimuthal Equal Area
after Snyder, *Map Projections — A Working Manual*, pp. 187–190, on GRS80.
No pyproj, no GDAL, no numpy: one forward transform of a point pair is a
dozen lines of trigonometry, and a compiled geospatial stack is a large
thing to add to a deployment for it. Verified against the EPSG registry's
own test point for 3035.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from apps.locations.services.terrain_sources import TerrainSource, source_from_payload

logger = logging.getLogger(__name__)

# The definition is a couple of kilobytes and is fetched once an hour, so
# it can afford to wait longer than a tile does.
REQUEST_TIMEOUT = 10  # seconds

# Matches the origin's own Cache-Control on grid.json. Longer would delay a
# rebuild's new geometry reaching us; shorter would spend a request an hour
# for a file that changes a few times a year.
GRID_CACHE_SECONDS = 3600

# Versioned key prefix, per the convention in ``apps.public.api``: the
# cached value is a pickled dataclass, so a field added to ``TerrainGrid``
# must not meet an old entry shaped like the old class.
GRID_CACHE_KEY = "terrain-grid:v1"


# --- EPSG:3035 ------------------------------------------------------------
# ETRS89-extended / LAEA Europe, on the GRS80 ellipsoid. These are the
# projection's definition rather than a configuration: a different value
# here is a different coordinate reference system, not a tuning knob.
_SEMI_MAJOR_AXIS_M = 6378137.0
_FLATTENING = 1 / 298.257222101
_ECCENTRICITY_SQUARED = _FLATTENING * (2 - _FLATTENING)
_ECCENTRICITY = math.sqrt(_ECCENTRICITY_SQUARED)
_LATITUDE_OF_ORIGIN = math.radians(52.0)
_LONGITUDE_OF_ORIGIN = math.radians(10.0)
_FALSE_EASTING_M = 4321000.0
_FALSE_NORTHING_M = 3210000.0


def _authalic_q(latitude_rad: float) -> float:
    """Return Snyder's ``q`` — the authalic area function — at a latitude.

    Snyder equation (3-12). The quantity the equal-area projection is built
    on: it maps a geodetic latitude onto the sphere of equal area.

    Args:
        latitude_rad: Geodetic latitude in radians.

    Returns:
        Snyder's ``q``, dimensionless.

    """
    sine = math.sin(latitude_rad)
    return (1 - _ECCENTRICITY_SQUARED) * (
        sine / (1 - _ECCENTRICITY_SQUARED * sine * sine)
        - (1 / (2 * _ECCENTRICITY))
        * math.log((1 - _ECCENTRICITY * sine) / (1 + _ECCENTRICITY * sine))
    )


# Precomputed because they depend only on the CRS, never on the point.
_Q_POLE = _authalic_q(math.pi / 2)
_AUTHALIC_LATITUDE_OF_ORIGIN = math.asin(_authalic_q(_LATITUDE_OF_ORIGIN) / _Q_POLE)
_AUTHALIC_RADIUS_M = _SEMI_MAJOR_AXIS_M * math.sqrt(_Q_POLE / 2)
_AXIS_RATIO = (
    _SEMI_MAJOR_AXIS_M
    * math.cos(_LATITUDE_OF_ORIGIN)
    / (
        math.sqrt(1 - _ECCENTRICITY_SQUARED * math.sin(_LATITUDE_OF_ORIGIN) ** 2)
        * _AUTHALIC_RADIUS_M
        * math.cos(_AUTHALIC_LATITUDE_OF_ORIGIN)
    )
)


@dataclass(frozen=True)
class TerrainGrid:
    """The published geometry and encoding of the terrain height tileset.

    Mirrors ``grid.json`` field for field. Frozen because every consumer
    treats it as the ground truth for a batch of samples, and a mutated
    copy would silently re-address them.

    Attributes:
        grid: The grid's identifier, e.g. ``"snowdesk-terrain-5m-3035"``.
        crs: The coordinate reference system the grid is laid out in.
        cell_size_m: The ground size of one stored cell.
        tile_cells: Cells per tile edge, excluding the skirt.
        tile_size_m: The ground size of one tile edge.
        skirt_cells: How many cells of a neighbouring tile each tile
            repeats around its edge.
        stored_cells: Cells per stored tile edge, skirt included.
        tile_bytes: The exact byte length of one tile.
        height_scale_m: Metres per stored unit.
        height_offset_m: Metres added after scaling.
        nodata: The stored value meaning "this cell has no height".
        row_order: Which way stored rows run — ``"north-to-south"``.
        column_order: Which way stored columns run — ``"west-to-east"``.
        default_analysis_window_m: The spacing a slope is measured at
            unless a caller asks for another.
        attribution: The tileset's overall credit line.
        tile_url_template: The published ``{x}/{y}`` tile URL template.
        version: The tileset version, e.g. ``"v1"``.
        sources: Who surveyed the ground, and where.

    """

    grid: str
    crs: str
    cell_size_m: float
    tile_cells: int
    tile_size_m: float
    skirt_cells: int
    stored_cells: int
    tile_bytes: int
    height_scale_m: float
    height_offset_m: float
    nodata: int
    row_order: str
    column_order: str
    default_analysis_window_m: float
    attribution: str
    tile_url_template: str
    version: str
    sources: tuple[TerrainSource, ...]


def grid_from_payload(payload: dict[str, Any]) -> TerrainGrid:
    """Build a ``TerrainGrid`` from the parsed ``grid.json`` body.

    Strict for the same reason ``source_from_payload`` is: a definition
    that has changed shape must stop the sampler, not be half-read.

    Args:
        payload: The decoded ``grid.json`` object.

    Returns:
        The parsed grid definition.

    Raises:
        KeyError: If a required field is absent.
        TypeError: If the payload is not an object, or a field is not the
            type it must be.
        ValueError: If a field cannot be coerced, or a source is unusable.

    """
    return TerrainGrid(
        grid=str(payload["grid"]),
        crs=str(payload["crs"]),
        cell_size_m=float(payload["cell_size_m"]),
        tile_cells=int(payload["tile_cells"]),
        tile_size_m=float(payload["tile_size_m"]),
        skirt_cells=int(payload["skirt_cells"]),
        stored_cells=int(payload["stored_cells"]),
        tile_bytes=int(payload["tile_bytes"]),
        height_scale_m=float(payload["height_scale_m"]),
        height_offset_m=float(payload["height_offset_m"]),
        nodata=int(payload["nodata"]),
        row_order=str(payload["row_order"]),
        column_order=str(payload["column_order"]),
        default_analysis_window_m=float(payload["default_analysis_window_m"]),
        attribution=str(payload["attribution"]),
        tile_url_template=str(payload["tile_url_template"]),
        version=str(payload["version"]),
        sources=tuple(source_from_payload(entry) for entry in payload["sources"]),
    )


def load_grid() -> TerrainGrid | None:
    """Return the tileset's published definition, fetching it if need be.

    ``GET {settings.TERRAIN_TILE_BASE_URL}/grid.json``, parsed and held in
    the Django cache for an hour. The parsed dataclass is what is cached,
    not the bytes: re-parsing a definition that has not changed is work
    nobody asked for, and the payload is small enough for the production
    ``DatabaseCache`` either way.

    NEVER RAISES. Every failure — a timeout, a refused connection, a 5xx, a
    body that is not JSON, a definition missing a field — returns None, and
    ``apps.locations.services.terrain`` turns that into ``UNAVAILABLE``.
    There is no fallback copy to fall back to, deliberately; see the module
    docstring.

    Returns:
        The grid definition, or None when it could not be obtained.

    """
    cached = cache.get(GRID_CACHE_KEY)
    if isinstance(cached, TerrainGrid):
        return cached

    url = f"{settings.TERRAIN_TILE_BASE_URL.rstrip('/')}/grid.json"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        # ``exception`` rather than ``warning``: this is the branch that
        # fires when the origin is down, and which failure it was is the
        # whole diagnosis.
        logger.exception("terrain: grid.json request failed (url=%s)", url)
        return None

    if not response.ok:
        logger.warning(
            "terrain: %s fetching grid.json (url=%s)", response.status_code, url
        )
        return None

    try:
        payload = json.loads(response.content)
        grid = grid_from_payload(payload)
    # IndexError joins the list because a structurally short array — a
    # coverage range published as ``[3131]`` — is a malformed definition
    # like any other, and this function promises never to raise for one.
    except json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError:
        logger.exception("terrain: grid.json is not a usable definition (url=%s)", url)
        return None

    cache.set(GRID_CACHE_KEY, grid, GRID_CACHE_SECONDS)
    return grid


def project(latitude: float, longitude: float) -> tuple[float, float]:
    """Project a WGS84 coordinate onto EPSG:3035.

    The forward ellipsoidal LAEA transform (Snyder pp. 187–190). Arguments
    are ``(latitude, longitude)`` — the house order — and the result is
    ``(easting, northing)``, which is the order the grid is addressed in.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Returns:
        ``(easting, northing)`` in EPSG:3035 metres.

    """
    latitude_rad = math.radians(latitude)
    longitude_rad = math.radians(longitude)

    authalic = math.asin(_authalic_q(latitude_rad) / _Q_POLE)
    delta_longitude = longitude_rad - _LONGITUDE_OF_ORIGIN

    scale = _AUTHALIC_RADIUS_M * math.sqrt(
        2
        / (
            1
            + math.sin(_AUTHALIC_LATITUDE_OF_ORIGIN) * math.sin(authalic)
            + math.cos(_AUTHALIC_LATITUDE_OF_ORIGIN)
            * math.cos(authalic)
            * math.cos(delta_longitude)
        )
    )

    easting = _FALSE_EASTING_M + scale * _AXIS_RATIO * math.cos(authalic) * math.sin(
        delta_longitude
    )
    northing = _FALSE_NORTHING_M + (scale / _AXIS_RATIO) * (
        math.cos(_AUTHALIC_LATITUDE_OF_ORIGIN) * math.sin(authalic)
        - math.sin(_AUTHALIC_LATITUDE_OF_ORIGIN)
        * math.cos(authalic)
        * math.cos(delta_longitude)
    )
    return easting, northing


@dataclass(frozen=True)
class CellAddress:
    """Where one grid cell lives: which tile owns it, and where inside.

    Attributes:
        tile_x: The owning tile's column, counting east from easting 0.
        tile_y: The owning tile's row, counting north from northing 0.
        row: The cell's row inside that tile, 0 at its northern edge.
        column: The cell's column inside that tile, 0 at its western edge.

    """

    tile_x: int
    tile_y: int
    row: int
    column: int


def global_cell(grid: TerrainGrid, easting: float, northing: float) -> tuple[int, int]:
    """Return which cell of the whole grid a projected point falls in.

    The grid is addressed globally as well as per tile because a slope
    kernel at a wide window reaches across a tile boundary, and comparing
    two cells is only meaningful in one coordinate system.

    ``cell_x`` counts east from easting 0; ``cell_y`` counts SOUTH, so that
    ``cell_y + 1`` is the cell one row further down a stored tile and the
    two indices run the way the tile's bytes do.

    Both are plain floors, which IS the published half-open rule —
    ``[south, north)`` and ``[west, east)``, so a coordinate on a boundary
    belongs to the cell north and east of it. The southward flip is what
    makes the north edge the closed one: ``floor(-N / cell)`` puts a point
    exactly on a cell's southern boundary in the cell above it. There is no
    special case here and there must not be one.

    Args:
        grid: The loaded grid definition.
        easting: EPSG:3035 easting in metres.
        northing: EPSG:3035 northing in metres.

    Returns:
        ``(cell_x, cell_y)``.

    """
    cell_x = math.floor(easting / grid.cell_size_m)
    cell_y = -1 - math.floor(northing / grid.cell_size_m)
    return cell_x, cell_y


def tile_of_cell(grid: TerrainGrid, cell_x: int, cell_y: int) -> CellAddress:
    """Return which tile owns a global cell, and where in it the cell sits.

    The inverse of the tiling: tiles hold ``grid.tile_cells`` cells a side,
    row 0 at the tile's northern edge. Python's floor division is what
    keeps this correct for the negative ``cell_y`` axis rather than
    truncating towards zero at the equator.

    Args:
        grid: The loaded grid definition.
        cell_x: Global cell column, counting east.
        cell_y: Global cell row, counting south.

    Returns:
        The owning tile and the cell's row/column inside it, both in
        ``0 .. grid.tile_cells - 1``.

    """
    tile_x, column = divmod(cell_x, grid.tile_cells)
    # ``-1 - cell_y`` is the northward cell index that ``global_cell``
    # flipped; the tile row is its whole number of tiles.
    tile_y = (-1 - cell_y) // grid.tile_cells
    row = cell_y + grid.tile_cells * (tile_y + 1)
    return CellAddress(tile_x=tile_x, tile_y=tile_y, row=row, column=column)


def offset_in_tile(
    grid: TerrainGrid,
    cell_x: int,
    cell_y: int,
    tile_x: int,
    tile_y: int,
) -> tuple[int, int] | None:
    """Return a cell's row/column in a given tile, skirt included.

    A tile stores ``grid.skirt_cells`` extra rings of its neighbours'
    cells, so it can answer for cells it does not own. The returned indices
    therefore run from ``-skirt_cells`` to ``tile_cells - 1 + skirt_cells``
    and are what ``stored_index`` expects.

    This is what makes a step-1 slope kernel one HTTP request wherever it
    lands: the eight neighbours of a cell on a tile's outermost row are in
    that tile's skirt, not in three other tiles.

    Args:
        grid: The loaded grid definition.
        cell_x: Global cell column, counting east.
        cell_y: Global cell row, counting south.
        tile_x: The tile to read the cell from.
        tile_y: The tile to read the cell from.

    Returns:
        ``(row, column)`` within that tile's stored array, or None when the
        tile does not hold the cell even in its skirt.

    """
    row = cell_y + grid.tile_cells * (tile_y + 1)
    column = cell_x - tile_x * grid.tile_cells
    limit = grid.tile_cells - 1 + grid.skirt_cells
    if -grid.skirt_cells <= row <= limit and -grid.skirt_cells <= column <= limit:
        return row, column
    return None


def stored_index(grid: TerrainGrid, row: int, column: int) -> int:
    """Return a cell's index in a decoded tile's flat stored array.

    Rows run north to south and columns west to east, so the array is
    row-major from the tile's north-west stored corner. The skirt shifts
    the owned cells inward by ``grid.skirt_cells`` in both directions.

    Args:
        grid: The loaded grid definition.
        row: Row within the tile, ``-skirt_cells`` upwards.
        column: Column within the tile, ``-skirt_cells`` upwards.

    Returns:
        The index into the ``stored_cells * stored_cells`` array.

    """
    return (row + grid.skirt_cells) * grid.stored_cells + (column + grid.skirt_cells)


def tile_url(grid: TerrainGrid, tile_x: int, tile_y: int) -> str:
    """Return the URL one tile is published at, per the loaded definition.

    **From the definition's own template, not from
    ``TERRAIN_TILE_BASE_URL``.** The template carries the version segment,
    and that segment exists for exactly one reason: tiles are served
    ``immutable, max-age=31536000``, so a rebuild that moves the geometry
    bumps the version and moves every consumer to a URL no cache has an
    answer for. Composing the URL from a pinned setting instead would
    leave a process decoding year-old bytes with the new geometry — no
    error, just wrong heights, which is the failure this module's whole
    read-the-definition design exists to prevent.

    A consequence worth knowing: a mirror pointed at by
    ``TERRAIN_TILE_BASE_URL`` must publish its own ``grid.json`` naming its
    own tiles. Serving somebody else's definition while hosting your own
    tiles is already a lie about the geometry; this just makes it one about
    the location too.

    Args:
        grid: The loaded grid definition.
        tile_x: The tile's column.
        tile_y: The tile's row.

    Returns:
        The absolute URL of that tile.

    """
    return grid.tile_url_template.replace("{x}", str(tile_x)).replace(
        "{y}", str(tile_y)
    )
