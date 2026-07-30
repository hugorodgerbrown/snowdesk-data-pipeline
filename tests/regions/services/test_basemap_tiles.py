"""
tests/regions/services/test_basemap_tiles.py — Tests for basemap_tiles service.

Covers:
  lon_lat_to_tile  — null-island exactness at several zooms, zoom-0
                      collapse, west-edge x=0.
  bbox_from_boundary — Polygon and MultiPolygon extents.
  tile_ranges       — per-zoom range shape, ordering, and east-edge index
                      clamping to the valid [0, 2**z - 1] range.
  tile_count        — arithmetic over a hand-built ranges dict.
  centre_tile       — centre point of a symmetric bbox.
  build_blob        — full blob shape, arithmetic consistency, and the
                      over_ceiling flag for a small vs. a pathologically
                      large bbox.
  blob_summary      — drops the "z" (and "band") keys.
  MICRO_BAND        — the single micro-region zoom-band constant.
"""

from __future__ import annotations

import math

from apps.regions.services.basemap_tiles import (
    DOWNLOAD_CEILING_MB,
    MICRO_BAND,
    WORST_CASE_BYTES_PER_TILE,
    bbox_from_boundary,
    blob_summary,
    build_blob,
    centre_tile,
    lon_lat_to_tile,
    tile_count,
    tile_ranges,
)

# ---------------------------------------------------------------------------
# lon_lat_to_tile
# ---------------------------------------------------------------------------


def test_lon_lat_to_tile_null_island_is_exact_grid_centre() -> None:
    """(0, 0) always lands on the exact centre tile (2**(z-1), 2**(z-1))."""
    for z in (1, 5, 8, 10, 14):
        assert lon_lat_to_tile(0.0, 0.0, z) == (2 ** (z - 1), 2 ** (z - 1))


def test_lon_lat_to_tile_zoom_zero_collapses_to_single_tile() -> None:
    """At zoom 0 the whole world is one tile — (0, 0) for any coordinate."""
    assert lon_lat_to_tile(0.0, 0.0, 0) == (0, 0)
    assert lon_lat_to_tile(-179.0, 46.0, 0) == (0, 0)
    assert lon_lat_to_tile(179.0, -46.0, 0) == (0, 0)


def test_lon_lat_to_tile_west_edge_is_x_zero() -> None:
    """The west antimeridian (lon=-180) is always tile column 0."""
    for z in (1, 4, 10):
        x, _y = lon_lat_to_tile(-180.0, 0.0, z)
        assert x == 0


# ---------------------------------------------------------------------------
# bbox_from_boundary
# ---------------------------------------------------------------------------


def test_bbox_from_boundary_polygon() -> None:
    """A simple square Polygon returns its exact extents."""
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[7.0, 46.0], [8.0, 46.0], [8.0, 47.0], [7.0, 47.0], [7.0, 46.0]]
        ],
    }
    assert bbox_from_boundary(polygon) == [7.0, 46.0, 8.0, 47.0]


def test_bbox_from_boundary_multipolygon_unions_both_parts() -> None:
    """A MultiPolygon's bbox spans the union of every constituent polygon."""
    multipolygon = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]],
            [[[9.0, 47.0], [9.5, 47.0], [9.5, 47.5], [9.0, 47.5], [9.0, 47.0]]],
        ],
    }
    assert bbox_from_boundary(multipolygon) == [7.0, 46.0, 9.5, 47.5]


def test_bbox_from_boundary_rejects_unsupported_geometry_type() -> None:
    """A non-Polygon/MultiPolygon geometry raises rather than silently misparsing."""
    import pytest

    with pytest.raises(ValueError, match="Unsupported boundary geometry type"):
        bbox_from_boundary({"type": "Point", "coordinates": [7.0, 46.0]})


# ---------------------------------------------------------------------------
# tile_ranges
# ---------------------------------------------------------------------------


def test_tile_ranges_covers_requested_zoom_band() -> None:
    """Every zoom in [min_z, max_z] gets exactly one entry, min<=max on both axes."""
    bbox = [7.0, 46.0, 8.0, 47.0]
    ranges = tile_ranges(bbox, 8, 12)
    assert set(ranges.keys()) == {"8", "9", "10", "11", "12"}
    for xmin, xmax, ymin, ymax in ranges.values():
        assert xmin <= xmax
        assert ymin <= ymax


def test_tile_ranges_grows_with_zoom() -> None:
    """A fixed bbox covers more tiles at a deeper zoom."""
    bbox = [7.0, 46.0, 8.0, 47.0]
    ranges = tile_ranges(bbox, 8, 12)
    shallow_count = (ranges["8"][1] - ranges["8"][0] + 1) * (
        ranges["8"][3] - ranges["8"][2] + 1
    )
    deep_count = (ranges["12"][1] - ranges["12"][0] + 1) * (
        ranges["12"][3] - ranges["12"][2] + 1
    )
    assert deep_count > shallow_count


def test_tile_ranges_clamps_east_edge_index() -> None:
    """A bbox touching the east antimeridian clamps x to 2**z - 1, never 2**z."""
    bbox = [179.0, -1.0, 180.0, 1.0]
    ranges = tile_ranges(bbox, 4, 4)
    xmin, xmax, _ymin, _ymax = ranges["4"]
    assert xmax == (2**4) - 1
    assert xmin <= xmax


# ---------------------------------------------------------------------------
# tile_count
# ---------------------------------------------------------------------------


def test_tile_count_sums_area_across_every_zoom() -> None:
    """count = sum over zoom of (xmax-xmin+1) * (ymax-ymin+1)."""
    ranges = {"8": [10, 12, 5, 6], "9": [20, 20, 10, 10]}
    # z=8: (12-10+1) * (6-5+1) = 3 * 2 = 6
    # z=9: (20-20+1) * (10-10+1) = 1 * 1 = 1
    assert tile_count(ranges) == 7


# ---------------------------------------------------------------------------
# centre_tile
# ---------------------------------------------------------------------------


def test_centre_tile_of_symmetric_bbox_around_null_island() -> None:
    """A bbox symmetric around (0, 0) has its centre tile at the exact grid centre."""
    bbox = [-1.0, -1.0, 1.0, 1.0]
    for z in (8, 14):
        assert centre_tile(bbox, z) == {"z": z, "x": 2 ** (z - 1), "y": 2 ** (z - 1)}


# ---------------------------------------------------------------------------
# build_blob
# ---------------------------------------------------------------------------


def test_build_blob_shape_and_arithmetic() -> None:
    """The blob carries band/count/mb/over_ceiling/centre_tile/z, consistently."""
    bbox = [7.0, 46.0, 8.0, 47.0]
    min_z, max_z = MICRO_BAND
    blob = build_blob(bbox, min_z, max_z)

    assert blob["band"] == [min_z, max_z]
    assert set(blob["z"].keys()) == {str(z) for z in range(min_z, max_z + 1)}
    assert blob["count"] == tile_count(blob["z"])
    assert blob["mb"] == math.ceil(
        blob["count"] * WORST_CASE_BYTES_PER_TILE / (1024 * 1024)
    )
    assert blob["over_ceiling"] is (blob["mb"] > DOWNLOAD_CEILING_MB)
    assert blob["centre_tile"] == centre_tile(bbox, max_z)


def test_build_blob_small_region_is_under_ceiling() -> None:
    """A realistic single micro-region bbox stays well under the 200 MB ceiling."""
    bbox = [7.0, 46.0, 7.2, 46.2]
    min_z, max_z = MICRO_BAND
    blob = build_blob(bbox, min_z, max_z)
    assert blob["over_ceiling"] is False


def test_build_blob_pathologically_large_bbox_flags_over_ceiling() -> None:
    """A near-continental bbox at the micro band's z10-14 range exceeds the ceiling."""
    bbox = [-10.0, 30.0, 20.0, 60.0]
    min_z, max_z = MICRO_BAND
    blob = build_blob(bbox, min_z, max_z)
    assert blob["over_ceiling"] is True


# ---------------------------------------------------------------------------
# blob_summary
# ---------------------------------------------------------------------------


def test_blob_summary_drops_z_ranges_and_band() -> None:
    """The summary keeps only count/mb/over_ceiling/centre_tile."""
    bbox = [7.0, 46.0, 8.0, 47.0]
    min_z, max_z = MICRO_BAND
    blob = build_blob(bbox, min_z, max_z)
    summary = blob_summary(blob)
    assert set(summary.keys()) == {"count", "mb", "over_ceiling", "centre_tile"}
    assert summary["count"] == blob["count"]
    assert summary["mb"] == blob["mb"]
    assert summary["over_ceiling"] == blob["over_ceiling"]
    assert summary["centre_tile"] == blob["centre_tile"]


# ---------------------------------------------------------------------------
# MICRO_BAND
# ---------------------------------------------------------------------------


def test_micro_band_constant() -> None:
    """The micro-region download's zoom band matches the SNOW-521 final spec."""
    assert MICRO_BAND == (10, 14)
