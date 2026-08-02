"""
apps/regions/services/basemap_tiles.py — Pure tile-math for basemap download precompute.

SNOW-521 rework: region boundaries are fixed reference data and the
basemap tile grid is static, so each region's tile coverage (and
worst-case size) never changes. This module computes that coverage once
— consumed by ``apps.regions.management.commands.compute_basemap_download``
— rather than enumerating tiles at request time or in the browser (the
project's precompute-over-runtime preference).

Deliberately no Django imports — pure functions over plain floats/dicts,
independently pytest-covered (``tests/regions/services/test_basemap_tiles.py``)
without a database.

Coordinate convention: **(lon, lat) order**, matching GeoJSON's
``[longitude, latitude]`` position tuples and the pre-existing slippy-map
tile math this module supersedes (``static/js/basemap_download_core.js``'s
now-removed ``_lonToTileX``/``_latToTileY``). This is a deliberate
carve-out from the project's usual (lat, lon) argument order for new
functions — keeping the tile math lon-first matches both its GeoJSON
input and its JS counterpart, so a reader porting between the two never
has to mentally swap axes.

SNOW-521 final shape: a single **micro-region-only** download. An
earlier iteration computed per-tier bands for Major/Minor/Micro, but
each tier's own shallower detail floor made the sizes non-monotonic with
containment (an L1 region could read smaller than an L2 it contains),
which read as a bug — so the tiered machinery was dropped in favour of
one band, always at the micro-region's full detail:

    MICRO_BAND = (10, 14)

SNOW-583 clips a region's download to its real boundary plus one z14
tile (~1.7 km) of margin, rather than the whole bounding-box rectangle
``build_blob`` (below) still computes — across the 149 Swiss
micro-regions, 44% of the rectangle's tiles touch no part of the
region. ``build_region_blob`` is the clipped counterpart, used by
``compute_basemap_download`` for every ``MicroRegion``; ``build_blob``
itself is UNCHANGED and stays rectangular — it remains the twin of
``static/js/basemap_download_core.js``'s ``buildBlob``, which builds the
*custom-area* download's blob (a user-drawn rectangle, which genuinely
is one — see that module's header). The candidate tiles a clip starts
from are always ``tile_ranges`` over the UNDILATED bbox, and a tile is
only ever dropped, never added, so a clipped region's tile set is a
subset of what ``build_blob`` would have computed for the same bbox —
see ``clip_ranges``'s docstring for why that containment matters.

Public API:
    lon_lat_to_tile(lon, lat, z)   — Web Mercator (lon, lat) → (x, y) tile
                                      indices at zoom ``z``.
    bbox_from_boundary(polygon)    — [min_lon, min_lat, max_lon, max_lat]
                                      over a GeoJSON Polygon/MultiPolygon.
    tile_ranges(bbox, min_z, max_z) — {"<z>": [xmin, xmax, ymin, ymax]} for
                                      every zoom level in the band.
    tile_count(ranges)             — total tile count across every zoom
                                      level in ``ranges``.
    centre_tile(bbox, z)           — the tile at ``bbox``'s centre point,
                                      at zoom ``z``.
    build_blob(bbox, min_z, max_z) — the full stored blob for a
                                      RECTANGULAR area (custom-area
                                      download only — see above).
    clip_ranges(boundary, ranges, max_z) — ``ranges`` (from
                                      ``tile_ranges``) dropped down to the
                                      tiles that fall within one margin
                                      tile of ``boundary``, as row spans.
    row_tile_count(z)              — total tile count across every zoom
                                      level in a clipped row-span ``z`` map.
    build_region_blob(boundary, min_z, max_z) — the full stored blob for
                                      a region, clipped to its real
                                      boundary (used by
                                      ``compute_basemap_download``).
    blob_summary(blob)             — the small "count/mb/over_ceiling/
                                      centre_tile" projection served inline
                                      on the geojson endpoints (no ``z``
                                      ranges — those are fetched on demand
                                      from ``region_basemap_tiles``).

Stored blob shape (``MicroRegion.basemap_download``, from
``build_region_blob``)::

    {
      "band": [10, 14],
      "count": 312,
      "mb": 31,
      "over_ceiling": false,
      "centre_tile": {"z": 14, "x": 8501, "y": 5820},
      "z": {"10": {"362": [530, 531]}, ..., "14": {"5792": [8502, 8504], ...}}
    }

Row spans, not run-length runs: a zoom level's entry maps each present
row (``y``, as a string key) to a single ``[xmin, xmax]`` span, even if
the boundary's true intersection with that row has a gap in the middle
(a concave region can produce one). Measured across all 149 CH regions
and every zoom in the band: 1.3% of rows have any gap at all, and a
plain span over-includes only 0.4% more tiles than tracking the exact
runs would — not worth the extra shape everywhere a blob's ``z`` is
consumed (the JS accessor, the grid-cell clamp, this module's own
counter). ``build_blob``'s RECTANGULAR shape (``{"<z>": [xmin, xmax,
ymin, ymax]}``) is unaffected — that stays exactly as it always was.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry
    from shapely.prepared import PreparedGeometry

# A clipped region's z-map shape: {"<z>": {"<y>": [xmin, xmax]}} — one row
# span per present row, at every zoom level in the band. See the module
# docstring's "Row spans, not run-length runs" note for why a span, not a
# run list.
type RegionZMap = dict[str, dict[str, list[int]]]

# The micro-region download's zoom band: (min_z, max_z) inclusive. See the
# module docstring for why Major/Minor were dropped in favour of this
# single band.
MICRO_BAND: tuple[int, int] = (10, 14)

# A conservative upper bound for a dense Liberty-style vector tile, so the
# stored "mb" estimate is a true worst-case ceiling rather than a
# typical-case average — mirrors the pre-rework
# ``BASEMAP_WORST_CASE_BYTES_PER_TILE`` client-side constant.
WORST_CASE_BYTES_PER_TILE: int = 100 * 1024

# Download hard ceiling. A region whose worst-case estimate exceeds this
# is flagged ``over_ceiling`` — a backstop against a pathologically large
# micro-region, surfaced client-side as a disabled download icon rather
# than starting a run with no sensible bound.
DOWNLOAD_CEILING_MB: int = 200

# Keys copied from a full blob into its "summary" projection — everything
# except the ``z`` tile ranges, which are only fetched on demand.
_SUMMARY_KEYS: tuple[str, ...] = ("count", "mb", "over_ceiling", "centre_tile")


class CentreTile(TypedDict):
    """A single tile reference — the done-probe key for a region's download."""

    z: int
    x: int
    y: int


def lon_lat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Return the Web Mercator ``(x, y)`` tile indices for ``(lon, lat)`` at zoom ``z``.

    Args:
        lon: Longitude in degrees.
        lat: Latitude in degrees.
        z: Zoom level.

    Returns:
        ``(x, y)`` tile indices (not clamped to the valid ``[0, 2**z - 1]``
        range — callers that need a valid tile index, e.g. ``tile_ranges``,
        clamp explicitly).

    """
    x = math.floor((lon + 180.0) / 360.0 * (2**z))
    lat_rad = math.radians(lat)
    y = math.floor(
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * (2**z)
    )
    return x, y


def bbox_from_boundary(polygon: dict[str, Any]) -> list[float]:
    """Return ``[min_lon, min_lat, max_lon, max_lat]`` over a GeoJSON geometry.

    Handles both ``Polygon`` (``coordinates`` is ``[ring][position]``) and
    ``MultiPolygon`` (``coordinates`` is ``[polygon][ring][position]``) —
    ``MicroRegion.boundary`` is a Polygon in practice, but MultiPolygon is
    supported for consistency with the L1/L2 derived boundaries.

    Args:
        polygon: A GeoJSON ``Polygon`` or ``MultiPolygon`` geometry dict.

    Returns:
        The bounding box as ``[west, south, east, north]``.

    Raises:
        ValueError: On an unsupported geometry type.

    """
    geom_type = polygon["type"]
    if geom_type == "Polygon":
        rings = polygon["coordinates"]
    elif geom_type == "MultiPolygon":
        rings = [ring for poly in polygon["coordinates"] for ring in poly]
    else:
        raise ValueError(
            f"Unsupported boundary geometry type {geom_type!r}; "
            "expected Polygon or MultiPolygon."
        )
    lons = [pos[0] for ring in rings for pos in ring]
    lats = [pos[1] for ring in rings for pos in ring]
    return [min(lons), min(lats), max(lons), max(lats)]


def tile_ranges(bbox: list[float], min_z: int, max_z: int) -> dict[str, list[int]]:
    """Return the tile-index ranges covering ``bbox`` for every zoom in the band.

    Args:
        bbox: ``[west, south, east, north]`` in degrees.
        min_z: Shallowest zoom level (inclusive).
        max_z: Deepest zoom level (inclusive).

    Returns:
        ``{"<z>": [xmin, xmax, ymin, ymax]}`` — one entry per zoom level,
        with indices clamped to the valid ``[0, 2**z - 1]`` range.

    """
    west, south, east, north = bbox
    ranges: dict[str, list[int]] = {}
    for z in range(min_z, max_z + 1):
        x0, y0 = lon_lat_to_tile(west, north, z)
        x1, y1 = lon_lat_to_tile(east, south, z)
        xmin, xmax = sorted((x0, x1))
        ymin, ymax = sorted((y0, y1))
        max_index = (2**z) - 1
        xmin = max(0, min(xmin, max_index))
        xmax = max(0, min(xmax, max_index))
        ymin = max(0, min(ymin, max_index))
        ymax = max(0, min(ymax, max_index))
        ranges[str(z)] = [xmin, xmax, ymin, ymax]
    return ranges


def tile_count(ranges: dict[str, list[int]]) -> int:
    """Return the total tile count across every zoom level in ``ranges``.

    Args:
        ranges: The ``{"<z>": [xmin, xmax, ymin, ymax]}`` shape returned by
            ``tile_ranges``.

    Returns:
        The sum of ``(xmax - xmin + 1) * (ymax - ymin + 1)`` over every
        zoom level.

    """
    total = 0
    for xmin, xmax, ymin, ymax in ranges.values():
        total += (xmax - xmin + 1) * (ymax - ymin + 1)
    return total


def centre_tile(bbox: list[float], z: int) -> CentreTile:
    """Return the tile at ``bbox``'s centre point, at zoom ``z``.

    This is the "done-probe" key: the client checks whether this single
    tile is present in the pinned cache as a proxy for "this region's
    download completed" (the same proxy the pre-rework viewport control
    used for its own zoom level).

    Args:
        bbox: ``[west, south, east, north]`` in degrees.
        z: Zoom level — the download's detail floor (``MICRO_BAND[1]``).

    Returns:
        ``{"z": z, "x": int, "y": int}``.

    """
    west, south, east, north = bbox
    centre_lon = (west + east) / 2.0
    centre_lat = (south + north) / 2.0
    x, y = lon_lat_to_tile(centre_lon, centre_lat, z)
    return {"z": z, "x": x, "y": y}


def build_blob(bbox: list[float], min_z: int, max_z: int) -> dict[str, Any]:
    """Build the full stored ``basemap_download`` blob for a region.

    Args:
        bbox: ``[west, south, east, north]`` in degrees.
        min_z: The shallowest zoom level (``MICRO_BAND[0]``).
        max_z: The detail floor (``MICRO_BAND[1]``).

    Returns:
        The full blob dict — see the module docstring for its shape.

    """
    ranges = tile_ranges(bbox, min_z, max_z)
    count = tile_count(ranges)
    total_bytes = count * WORST_CASE_BYTES_PER_TILE
    mb = math.ceil(total_bytes / (1024 * 1024))
    return {
        "band": [min_z, max_z],
        "count": count,
        "mb": mb,
        "over_ceiling": mb > DOWNLOAD_CEILING_MB,
        "centre_tile": centre_tile(bbox, max_z),
        "z": ranges,
    }


def _mercator_y_to_lat(y: float) -> float:
    """Invert a Web Mercator world-``y`` fraction (``[0, 1]``, southward) to a latitude.

    The inverse of the ``y`` term inside ``lon_lat_to_tile`` — factored out
    because ``_tile_bounds`` needs it for both of a tile's horizontal edges.
    Mirrors ``static/js/basemap_download_core.js``'s ``_mercatorYToLat``.

    Args:
        y: World ``y`` in ``[0, 1]``.

    Returns:
        A latitude in degrees.

    """
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y))))


def _tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Return the ``[west, south, east, north]`` ground one tile covers.

    The inverse of ``lon_lat_to_tile`` — used only by ``clip_ranges`` to
    test a candidate tile's footprint against a region's real boundary.
    Mirrors ``static/js/basemap_download_core.js``'s ``tileBounds``.

    Args:
        x: Tile x index.
        y: Tile y index.
        z: Zoom level.

    Returns:
        ``(west, south, east, north)`` in degrees.

    """
    n = 2**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    # Mercator y runs southward, so y+1 is the SOUTHERN edge.
    north = _mercator_y_to_lat(y / n)
    south = _mercator_y_to_lat((y + 1) / n)
    return west, south, east, north


def _margin_degrees(lat_centre: float, max_z: int) -> tuple[float, float]:
    """Return the clip margin as ``(delta_lon, delta_lat)`` in degrees.

    One tile at ``max_z`` (the download's detail floor — ``MICRO_BAND[1]``,
    ~1.7 km in the Alps at z14) of margin around the region's real
    boundary. Longitude is exact and latitude-independent
    (``360 / 2**max_z``); latitude is scaled by ``cos(lat_centre)`` because
    a degree of longitude shrinks towards the poles while a degree of
    latitude does not.

    Deliberately NOT scaled per zoom level — a tile at z10 is 16x the
    ground size of one at z14, so dilating by "one tile of the CURRENT
    zoom" would add roughly 27 km of margin at z10 alone. One fixed
    ground distance, tested at every zoom via ``clip_ranges``, keeps the
    boundary a consistent ~1.7 km wide at every level of the band.

    Args:
        lat_centre: The region's approximate centre latitude, in degrees
            (used only to scale the longitude margin).
        max_z: The zoom level whose tile size sets the margin —
            ``MICRO_BAND[1]``, the download's detail floor.

    Returns:
        ``(delta_lon, delta_lat)`` in degrees.

    """
    delta_lon = 360.0 / (2**max_z)
    delta_lat = delta_lon * math.cos(math.radians(lat_centre))
    return delta_lon, delta_lat


def clip_ranges(
    boundary: dict[str, Any], ranges: dict[str, list[int]], max_z: int
) -> RegionZMap:
    """Drop ``ranges``' candidate tiles down to those near the real boundary.

    ``ranges`` (as returned by ``tile_ranges`` over the UNDILATED
    ``bbox_from_boundary(boundary)``) is the full rectangle of CANDIDATE
    tiles — this only ever DROPS tiles from it, never adds one outside it.
    That is load-bearing: it guarantees the clipped set is a subset of
    what ``build_blob`` would compute for the same bbox, so an
    already-downloaded region stays fully cached (SNOW-583's own
    verification step) rather than reading "not downloaded" forever after
    this ships.

    A candidate tile survives if its own footprint, dilated by one z14
    tile of margin (``_margin_degrees``) on every side, intersects
    ``boundary`` — the exact Minkowski equivalent of buffering the
    boundary itself by a ``(delta_lon, delta_lat)`` rectangle, without a
    scale/unscale round trip or shapely's uniform-radius ``buffer()``
    (which cannot express an anisotropic lon/lat margin directly).

    Uses ``shapely`` — a lazy, function-local import matching the
    project's convention for shapely-dependent helpers (see
    ``apps.regions.fixture_utils.boundary_from_children``) — with the
    boundary wrapped once in ``shapely.prepared.prep()`` and reused for
    every candidate tile in every zoom level, rather than re-preparing it
    per tile.

    Args:
        boundary: A GeoJSON ``Polygon`` or ``MultiPolygon`` geometry dict
            — the region's real boundary.
        ranges: The candidate ``{"<z>": [xmin, xmax, ymin, ymax]}`` shape
            from ``tile_ranges`` over the boundary's own (undilated) bbox.
        max_z: The zoom level the margin is measured in tiles of —
            ``MICRO_BAND[1]``, passed through to ``_margin_degrees``.

    Returns:
        ``{"<z>": {"<y>": [xmin, xmax]}}`` — one row span per surviving
        row, at every zoom level that has any. A zoom level with no
        surviving tiles is omitted entirely.

    """
    from shapely.geometry import box, shape
    from shapely.prepared import prep

    bbox = bbox_from_boundary(boundary)
    lat_centre = (bbox[1] + bbox[3]) / 2.0
    delta_lon, delta_lat = _margin_degrees(lat_centre, max_z)

    prepared: PreparedGeometry = prep(shape(boundary))

    clipped: RegionZMap = {}
    for z_str, (xmin, xmax, ymin, ymax) in ranges.items():
        z = int(z_str)
        rows: dict[str, list[int]] = {}
        for y in range(ymin, ymax + 1):
            row_xmin: int | None = None
            row_xmax: int | None = None
            for x in range(xmin, xmax + 1):
                west, south, east, north = _tile_bounds(x, y, z)
                footprint: BaseGeometry = box(
                    west - delta_lon,
                    south - delta_lat,
                    east + delta_lon,
                    north + delta_lat,
                )
                if not prepared.intersects(footprint):
                    continue
                if row_xmin is None:
                    row_xmin = x
                row_xmax = x
            if row_xmin is not None and row_xmax is not None:
                rows[str(y)] = [row_xmin, row_xmax]
        if rows:
            clipped[z_str] = rows
    return clipped


def row_tile_count(z: RegionZMap) -> int:
    """Return the total tile count across every zoom level in a clipped ``z`` map.

    The ``row_tile_count`` counterpart of ``tile_count`` for the row-span
    shape ``clip_ranges`` returns, rather than ``tile_ranges``'s rectangle
    shape.

    Args:
        z: The ``{"<z>": {"<y>": [xmin, xmax]}}`` shape returned by
            ``clip_ranges``.

    Returns:
        The sum of ``(xmax - xmin + 1)`` over every row, across every
        zoom level.

    """
    total = 0
    for rows in z.values():
        for xmin, xmax in rows.values():
            total += xmax - xmin + 1
    return total


def build_region_blob(
    boundary: dict[str, Any], min_z: int, max_z: int
) -> dict[str, Any]:
    """Build the full stored ``basemap_download`` blob for a region's real boundary.

    Unlike ``build_blob`` (RECTANGULAR — the custom-area download's
    shape), this clips the candidate bbox rectangle down to the tiles
    that fall within one margin tile of ``boundary`` itself
    (``clip_ranges``) — see the module docstring for the saving this
    buys and why the two functions coexist.

    Args:
        boundary: A GeoJSON ``Polygon`` or ``MultiPolygon`` geometry dict
            — the region's real boundary.
        min_z: The shallowest zoom level (``MICRO_BAND[0]``).
        max_z: The detail floor (``MICRO_BAND[1]``).

    Returns:
        The full blob dict — see the module docstring for its shape.

    """
    bbox = bbox_from_boundary(boundary)
    candidates = tile_ranges(bbox, min_z, max_z)
    clipped = clip_ranges(boundary, candidates, max_z)
    count = row_tile_count(clipped)
    total_bytes = count * WORST_CASE_BYTES_PER_TILE
    mb = math.ceil(total_bytes / (1024 * 1024))
    return {
        "band": [min_z, max_z],
        "count": count,
        "mb": mb,
        "over_ceiling": mb > DOWNLOAD_CEILING_MB,
        "centre_tile": centre_tile(bbox, max_z),
        "z": clipped,
    }


def blob_summary(blob: dict[str, Any]) -> dict[str, Any]:
    """Project a full blob down to its small API-inline summary.

    Drops the ``z`` tile ranges (and ``band``) — the summary is what
    ``regions_geojson`` embeds inline on every feature as
    ``properties.download``; the full blob (incl. ``z``) is only fetched
    on demand from ``region_basemap_tiles`` when the user clicks the
    download icon.

    Args:
        blob: A full blob as returned by ``build_blob``.

    Returns:
        ``{"count": ..., "mb": ..., "over_ceiling": ..., "centre_tile": ...}``.

    """
    return {key: blob[key] for key in _SUMMARY_KEYS}
