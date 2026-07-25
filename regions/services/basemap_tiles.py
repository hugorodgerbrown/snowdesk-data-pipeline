"""
regions/services/basemap_tiles.py — Pure tile-math for basemap download precompute.

SNOW-521 rework: region boundaries are fixed reference data and the
basemap tile grid is static, so each region's tile coverage (and
worst-case size) never changes. This module computes that coverage once
— consumed by ``regions.management.commands.compute_basemap_download``
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
    build_blob(bbox, min_z, max_z) — the full stored blob (see below).
    blob_summary(blob)             — the small "count/mb/over_ceiling/
                                      centre_tile" projection served inline
                                      on the geojson endpoints (no ``z``
                                      ranges — those are fetched on demand
                                      from ``region_basemap_tiles``).

Stored blob shape (``MicroRegion.basemap_download``)::

    {
      "band": [10, 14],
      "count": 312,
      "mb": 31,
      "over_ceiling": false,
      "centre_tile": {"z": 14, "x": 8501, "y": 5820},
      "z": {"10": [x0, x1, y0, y1], ..., "14": [...]}
    }
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

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
