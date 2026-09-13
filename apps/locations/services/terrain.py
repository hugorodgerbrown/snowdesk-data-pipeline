"""
apps/locations/services/terrain.py — how high, and how steep, the ground is.

Contains one enum, two result types and two functions:

  TerrainUnknown
      The three named reasons an answer is not available.

  TerrainHeight / TerrainSlope
      What a sample returns: a figure and its provenance, or a reason.

  sample_height(latitude, longitude)
      The ground's height above sea level at one coordinate.

  sample_slope(latitude, longitude, window_m=None)
      The ground's gradient and the direction it faces, at one coordinate.

Reads the terrain height tileset SNOW-908 published — a 5 m Int16 grid over
Switzerland in EPSG:3035 — through
``apps.locations.services.terrain_grid``. Distinct from
``apps.locations.services.elevation``, which asks Open-Meteo for one
point's height over the whole world and can never answer a gradient, and
from ``SLOPE_TILE_URL``, which is a picture of steepness that MapLibre
paints and no Python reads back.

**AN UNKNOWN IS A REASON, NEVER A NULL.** Roughly half the grid's rectangle
is ground no source covers, and the origin answers ``204 No Content``
there. Every caller — SNOW-910 colouring a route by steepness, SNOW-911
marking its cruxes, SNOW-839 scoring it against the bulletin — depends on
"we do not know" being impossible to confuse with "gentle". A bare ``None``
is the shape that gets mistaken for zero, coerced with ``or 0``, and
painted green. So nothing here returns one: a result carries either a
figure and the source it came from, or one of three named reasons. See
docs/decisions/terrain-unknown-is-a-reason-not-a-null.md.

**Nothing here raises for a data problem** — an unreachable origin, a
malformed tile and a hole in the coverage are all results. ``ValueError``
for a nonsensical ``window_m`` is the one exception, and it is a
programming error rather than a data one.
"""

from __future__ import annotations

import array
import functools
import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import requests
from django.conf import settings

from apps.locations.services.terrain_grid import (
    TerrainGrid,
    global_cell,
    load_grid,
    offset_in_tile,
    project,
    stored_index,
    tile_of_cell,
)
from apps.locations.services.terrain_sources import TerrainSource, select_source

logger = logging.getLogger(__name__)

# Shorter than the grid definition's 10 seconds would be tempting, but a
# tile is 133 KB against grid.json's 2 KB and a slope sample may need up to
# four of them. what3words uses five because a trip page can render without
# an address; a route with no steepness on it is a blank feature, so this
# one is allowed to wait a little longer.
REQUEST_TIMEOUT = 10  # seconds

# Tiles are served ``immutable, max-age=31536000``, so a process-local
# cache can hold one for as long as the process lives. 64 tiles is about
# 8.5 MB and covers a 10 km square of ground — comfortably a whole route,
# which is the access pattern every caller has.
TILE_CACHE_SIZE = 64


class TerrainUnknown(StrEnum):
    """Why a terrain sample has no figure.

    Three distinct reasons, kept distinct because they call for different
    responses. ``OUTSIDE_COVERAGE`` is permanent and expected — asking
    again will not help. ``NO_DATA`` is a hole inside a surveyed area, most
    often water. ``UNAVAILABLE`` is ours, is transient, and is the one a
    caller may reasonably retry or alert on.
    """

    OUTSIDE_COVERAGE = "outside_coverage"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TerrainHeight:
    """The ground's height at one point, or the reason there is none.

    Exactly one of ``height_m`` and ``unknown`` is set; ``__post_init__``
    enforces it, so a caller may branch on either and never on both.

    Attributes:
        height_m: Metres above sea level, or None when unknown.
        unknown: Why there is no height, or None when there is one.
        source: Who surveyed the ground. Set whenever a source claimed the
            point, which includes a ``NO_DATA`` hole inside its rectangle.

    """

    height_m: float | None
    unknown: TerrainUnknown | None
    source: TerrainSource | None

    def __post_init__(self) -> None:
        """Reject a result that is neither an answer nor a reason.

        Raises:
            ValueError: If both or neither of ``height_m`` and ``unknown``
                are set.

        """
        if (self.height_m is None) == (self.unknown is None):
            raise ValueError(
                "a TerrainHeight carries exactly one of height_m and unknown"
            )

    @property
    def is_known(self) -> bool:
        """Whether this sample carries a height.

        Returns:
            True when ``height_m`` is set.

        """
        return self.height_m is not None


@dataclass(frozen=True)
class TerrainSlope:
    """The ground's gradient at one point, or the reason there is none.

    Exactly one of ``angle_deg`` and ``unknown`` is set. ``aspect_deg`` is
    None only where the ground is exactly level and therefore faces
    nowhere — a bearing invented for a flat cell would be read as a real
    one.

    Attributes:
        angle_deg: Steepness in degrees from horizontal, 0 to 90, or None
            when unknown.
        aspect_deg: The compass bearing the slope faces — the direction of
            steepest descent, degrees clockwise from north — or None when
            unknown or exactly level.
        window_m: The spacing the gradient was measured across. 0.0 when
            the grid could not be read and no window was requested, since
            there is then no default to report.
        unknown: Why there is no gradient, or None when there is one.
        source: Who surveyed the ground, where one claimed the point.

    """

    angle_deg: float | None
    aspect_deg: float | None
    window_m: float
    unknown: TerrainUnknown | None
    source: TerrainSource | None

    def __post_init__(self) -> None:
        """Reject a result that is neither an answer nor a reason.

        Raises:
            ValueError: If both or neither of ``angle_deg`` and ``unknown``
                are set, or if an aspect accompanies no angle, or if a
                sloping result carries no aspect.

        """
        if (self.angle_deg is None) == (self.unknown is None):
            raise ValueError(
                "a TerrainSlope carries exactly one of angle_deg and unknown"
            )
        if self.angle_deg is None and self.aspect_deg is not None:
            raise ValueError("an unknown TerrainSlope cannot carry an aspect")
        if self.angle_deg and self.aspect_deg is None:
            raise ValueError("a sloping TerrainSlope must carry an aspect")

    @property
    def is_known(self) -> bool:
        """Whether this sample carries a gradient.

        Returns:
            True when ``angle_deg`` is set.

        """
        return self.angle_deg is not None


class _TerrainUnavailable(Exception):
    """Raised inside this module when the origin could not be read.

    Deliberately an exception rather than a return value, and that is what
    keeps the tile cache honest: ``functools.lru_cache`` memoises a return
    and never an exception, so a ``204`` is remembered for the life of the
    process while a timeout is retried on the next sample. An outage that
    cached itself would outlive the outage.
    """


def sample_height(latitude: float, longitude: float) -> TerrainHeight:
    """Return the ground's height above sea level at one coordinate.

    Reads the single grid cell the coordinate falls in — no interpolation
    between neighbours, because the answer's precision is the grid's 5 m
    cell and smoothing it would only disguise that.

    NEVER RAISES. See the module docstring: every failure is a named
    reason on the result.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Returns:
        The height and its source, or the reason there is neither.

    """
    grid = load_grid()
    if grid is None:
        return TerrainHeight(
            height_m=None, unknown=TerrainUnknown.UNAVAILABLE, source=None
        )

    easting, northing = project(latitude, longitude)
    source = select_source(grid.sources, easting, northing)
    if source is None:
        # No request is made: no source claims the rectangle, so there is
        # no tile to ask for.
        return TerrainHeight(
            height_m=None, unknown=TerrainUnknown.OUTSIDE_COVERAGE, source=None
        )

    cell = global_cell(grid, easting, northing)
    try:
        values = _read_cells(grid, [cell])
    except _TerrainUnavailable:
        return TerrainHeight(
            height_m=None, unknown=TerrainUnknown.UNAVAILABLE, source=source
        )

    if values is None:
        return TerrainHeight(
            height_m=None, unknown=TerrainUnknown.OUTSIDE_COVERAGE, source=source
        )
    if values[0] == grid.nodata:
        return TerrainHeight(
            height_m=None, unknown=TerrainUnknown.NO_DATA, source=source
        )

    return TerrainHeight(
        height_m=_to_metres(grid, values[0]), unknown=None, source=source
    )


def sample_slope(
    latitude: float,
    longitude: float,
    window_m: float | None = None,
) -> TerrainSlope:
    """Return the ground's gradient and facing at one coordinate.

    Horn's 3x3 method — the same one GDAL's ``gdaldem`` and swisstopo's own
    slope products use — over the eight cells around the one the coordinate
    falls in.

    **``window_m`` IS THE SPACING BETWEEN THE SAMPLED CELLS, not the width
    of the patch.** At the default 10 m the kernel reads cells 10 m apart
    and therefore spans 30 m of ground. That is deliberate and was decided
    rather than inherited: 10 m spacing is what SLF and swisstopo compute
    the 30 / 35 / 40 degree classes at, so our angles agree with the slope
    overlay painted beside them on the same map. Reading it as a patch
    width would give a 3.3 m spacing, a noticeably rougher answer, and two
    numbers on one screen that disagree about the same hillside.

    It must be a positive whole multiple of the grid's cell size. Anything
    else raises rather than rounding: silently answering a 12 m request at
    10 m would make the parameter a suggestion.

    NEVER RAISES for a data problem. See the module docstring.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.
        window_m: Spacing between the sampled cells, in metres. Defaults to
            None, which uses the grid's ``default_analysis_window_m``.

    Returns:
        The gradient, its facing and its source, or the reason there is
        none.

    Raises:
        ValueError: If ``window_m`` is not a positive whole multiple of the
            grid's cell size. Not raised when the grid itself is
            unreachable — the cell size is what the window is checked
            against, so with no grid there is nothing to check it against
            and the call returns ``UNAVAILABLE`` instead.

    """
    grid = load_grid()
    if grid is None:
        # The window cannot be validated without the grid's cell size, so
        # a bad one goes unreported here. The alternative is guessing at
        # the cell size to raise on, which is the hardcoding this whole
        # module refuses.
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=window_m or 0.0,
            unknown=TerrainUnknown.UNAVAILABLE,
            source=None,
        )

    window, step = _resolve_window(grid, window_m)

    easting, northing = project(latitude, longitude)
    source = select_source(grid.sources, easting, northing)
    if source is None:
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=window,
            unknown=TerrainUnknown.OUTSIDE_COVERAGE,
            source=None,
        )

    centre_x, centre_y = global_cell(grid, easting, northing)
    # The centre is FIRST so its tile is the one fetched first, which is
    # what lets the skirt answer for the other eight at step 1.
    cells = [(centre_x, centre_y)]
    cells += [
        (centre_x + column * step, centre_y + row * step)
        for row in (-1, 0, 1)
        for column in (-1, 0, 1)
    ]

    try:
        values = _read_cells(grid, cells)
    except _TerrainUnavailable:
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=window,
            unknown=TerrainUnknown.UNAVAILABLE,
            source=source,
        )

    if values is None:
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=window,
            unknown=TerrainUnknown.OUTSIDE_COVERAGE,
            source=source,
        )

    # ONE HOLE VOIDS THE WHOLE KERNEL. A gradient computed from eight
    # heights and a guess is a fabrication, and it would be a plausible
    # one — which is exactly the failure this module exists to prevent.
    kernel = values[1:]
    if any(value == grid.nodata for value in kernel):
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=window,
            unknown=TerrainUnknown.NO_DATA,
            source=source,
        )

    heights = [_to_metres(grid, value) for value in kernel]
    angle, aspect = _horn(heights, step * grid.cell_size_m)
    return TerrainSlope(
        angle_deg=angle,
        aspect_deg=aspect,
        window_m=window,
        unknown=None,
        source=source,
    )


def _resolve_window(grid: TerrainGrid, window_m: float | None) -> tuple[float, int]:
    """Return the analysis window and its step, in cells.

    Args:
        grid: The loaded grid definition.
        window_m: The requested spacing, or None for the grid's default.

    Returns:
        ``(window_m, step_cells)``.

    Raises:
        ValueError: If the window is not a positive whole multiple of the
            grid's cell size.

    """
    window = grid.default_analysis_window_m if window_m is None else float(window_m)
    if window <= 0:
        raise ValueError(f"window_m must be positive, got {window}")

    ratio = window / grid.cell_size_m
    step = round(ratio)
    # ``isclose`` rather than ``==`` because a window arrives as a float
    # and 15 / 5 is not always exactly 3 once it has been through JSON.
    if step < 1 or not math.isclose(ratio, step, rel_tol=1e-9):
        raise ValueError(
            f"window_m must be a whole multiple of the grid's "
            f"{grid.cell_size_m:g} m cell size, got {window:g}"
        )
    return window, step


def _horn(heights: Sequence[float], spacing_m: float) -> tuple[float, float | None]:
    """Return the slope angle and aspect of a 3x3 kernel by Horn's method.

    ``heights`` is row-major from the kernel's north-west corner, so index
    0 is north-west, 4 is the centre and 8 is south-east.

    The aspect is a COMPASS BEARING: the direction of steepest descent,
    degrees clockwise from north. Derived rather than transcribed, because
    the ``atan2`` form of it is easy to get 90 degrees or a mirror out and
    the result stays in range either way. The downhill vector is
    ``(-dz/dE, -dz/dN)`` and a bearing is ``atan2(east, north)``, which
    gives ``atan2(-dz/dE, dz/drow)`` once ``dz/dN = -dz/drow``.

    Args:
        heights: The nine cell heights in metres, row-major from the
            north-west, rows running north to south.
        spacing_m: The ground distance between adjacent kernel cells.

    Returns:
        ``(angle_deg, aspect_deg)``, where the aspect is None on exactly
        level ground.

    """
    north_west, north, north_east, west, _, east, south_west, south, south_east = (
        heights
    )

    # Eastward gradient: the east column against the west, centre-weighted.
    east_gradient = (
        (north_east + 2 * east + south_east) - (north_west + 2 * west + south_west)
    ) / (8 * spacing_m)
    # Southward gradient: per row of the kernel, so downhill in +row.
    south_gradient = (
        (south_west + 2 * south + south_east) - (north_west + 2 * north + north_east)
    ) / (8 * spacing_m)

    angle = math.degrees(math.atan(math.hypot(east_gradient, south_gradient)))
    if not east_gradient and not south_gradient:
        return 0.0, None
    aspect = math.degrees(math.atan2(-east_gradient, south_gradient)) % 360
    return angle, aspect


def _to_metres(grid: TerrainGrid, value: int) -> float:
    """Convert one stored cell value to metres above sea level.

    Args:
        grid: The loaded grid definition.
        value: The stored Int16, which must not be the nodata sentinel.

    Returns:
        Metres above sea level.

    """
    return value * grid.height_scale_m + grid.height_offset_m


def _read_cells(
    grid: TerrainGrid, cells: Sequence[tuple[int, int]]
) -> list[int] | None:
    """Read the stored values of a set of global cells.

    THE FIRST CELL IS THE ANCHOR, and its tile is fetched before any other
    is considered. That ordering is the whole point of the skirt: a step-1
    kernel on a tile's outermost row has neighbours the anchor tile already
    holds, so resolving them against fetched tiles first costs one request
    where resolving them by ownership would cost four.

    Args:
        grid: The loaded grid definition.
        cells: ``(cell_x, cell_y)`` pairs, anchor first.

    Returns:
        The raw stored values in the order asked for — nodata sentinels
        included, since only the caller knows whether a hole voids the
        answer — or None when any cell falls in a tile the origin does not
        publish.

    Raises:
        _TerrainUnavailable: If the origin could not be read.

    """
    tiles: dict[tuple[int, int], array.array[int] | None] = {}

    anchor = tile_of_cell(grid, *cells[0])
    _load_tile(grid, tiles, anchor.tile_x, anchor.tile_y)

    values: list[int] = []
    for cell_x, cell_y in cells:
        value = _read_cell(grid, tiles, cell_x, cell_y)
        if value is None:
            return None
        values.append(value)
    return values


def _read_cell(
    grid: TerrainGrid,
    tiles: dict[tuple[int, int], array.array[int] | None],
    cell_x: int,
    cell_y: int,
) -> int | None:
    """Read one global cell, from an already-loaded tile where possible.

    Args:
        grid: The loaded grid definition.
        tiles: Tiles decoded so far, keyed ``(tile_x, tile_y)``; a None
            value records a tile the origin does not publish. Mutated.
        cell_x: Global cell column, counting east.
        cell_y: Global cell row, counting south.

    Returns:
        The raw stored value, or None when the cell is in no published
        tile.

    Raises:
        _TerrainUnavailable: If the origin could not be read.

    """
    for (tile_x, tile_y), decoded in tiles.items():
        if decoded is None:
            continue
        offset = offset_in_tile(grid, cell_x, cell_y, tile_x, tile_y)
        if offset is not None:
            return decoded[stored_index(grid, *offset)]

    owner = tile_of_cell(grid, cell_x, cell_y)
    decoded = _load_tile(grid, tiles, owner.tile_x, owner.tile_y)
    if decoded is None:
        return None
    return decoded[stored_index(grid, owner.row, owner.column)]


def _load_tile(
    grid: TerrainGrid,
    tiles: dict[tuple[int, int], array.array[int] | None],
    tile_x: int,
    tile_y: int,
) -> array.array[int] | None:
    """Fetch and decode one tile into the per-sample tile map.

    Args:
        grid: The loaded grid definition.
        tiles: Tiles decoded so far. Mutated.
        tile_x: The tile's column.
        tile_y: The tile's row.

    Returns:
        The decoded tile, or None when the origin does not publish it.

    Raises:
        _TerrainUnavailable: If the origin could not be read.

    """
    key = (tile_x, tile_y)
    if key in tiles:
        return tiles[key]

    raw = _fetch_tile(tile_x, tile_y, grid.tile_bytes)
    decoded = None if raw is None else _decode_tile(raw)
    tiles[key] = decoded
    return decoded


def _decode_tile(raw: bytes) -> array.array[int]:
    """Decode one tile's bytes into signed 16-bit stored values.

    The published byte order is little-endian, which ``array`` reads with
    the host's, so a big-endian host has to swap. Nobody runs one, and the
    two lines are cheaper than the bug would be to find.

    Args:
        raw: The tile's bytes, already length-checked.

    Returns:
        The stored values, row-major from the tile's north-west stored
        corner.

    """
    values: array.array[int] = array.array("h")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    return values


@functools.lru_cache(maxsize=TILE_CACHE_SIZE)
def _fetch_tile(tile_x: int, tile_y: int, expected_bytes: int) -> bytes | None:
    """Fetch one terrain tile's bytes, or None when it is not published.

    Memoised for the life of the process, which the origin's ``immutable,
    max-age=31536000`` licenses. **A ``204`` memoises as None on purpose**:
    a route running along the coverage edge asks for the empty tile once
    rather than at every point on it.

    A failure raises instead of returning, so that ``lru_cache`` — which
    does not memoise an exception — retries it. An origin that was briefly
    down must not read as permanently empty; ``OUTSIDE_COVERAGE`` and
    ``UNAVAILABLE`` mean different things to every caller.

    **Tests must call ``_fetch_tile.cache_clear()``**, since a warm entry
    outlives an ``override_settings`` of the base URL.

    Args:
        tile_x: The tile's column.
        tile_y: The tile's row.
        expected_bytes: The exact length the grid says a tile is. Part of
            the cache key so a rebuilt grid cannot serve a stale entry.

    Returns:
        The tile's bytes, or None when the origin answers 204 or 404.

    Raises:
        _TerrainUnavailable: On a timeout, a connection failure, an
            unexpected status or a tile of the wrong length.

    """
    base = settings.TERRAIN_TILE_BASE_URL.rstrip("/")
    url = f"{base}/{tile_x}/{tile_y}.s16"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as error:
        logger.exception("terrain: tile request failed (url=%s)", url)
        raise _TerrainUnavailable(url) from error

    if response.status_code == 204:
        # Not an error and not a log line. Half the grid's rectangle
        # answers this, and it is the documented way of saying "no source
        # covers this ground".
        return None

    if response.status_code == 404:
        # The runbook is explicit that a published tile is never a 404, so
        # this is our arithmetic — a negative or out-of-range index — and
        # it is worth a line even though the answer is the same as a 204.
        logger.warning("terrain: 404 for a tile that should exist (url=%s)", url)
        return None

    if not response.ok:
        logger.warning(
            "terrain: %s fetching a tile (url=%s)", response.status_code, url
        )
        raise _TerrainUnavailable(url)

    raw = response.content
    if len(raw) != expected_bytes:
        # UNAVAILABLE rather than absent: a short body is a truncated
        # response or a proxy's error page, not a statement about the
        # ground.
        logger.warning(
            "terrain: tile is %s bytes, expected %s (url=%s)",
            len(raw),
            expected_bytes,
            url,
        )
        raise _TerrainUnavailable(url)

    return raw
