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
                      large bbox. RECTANGULAR — the custom-area download's
                      shape; unmoved by SNOW-583 (see the module docstring).
  clip_ranges / row_tile_count / build_region_blob — SNOW-583's clip to a
                      region's real boundary: the ⊆-candidates invariant
                      over every real CH region boundary, that clipping
                      only ever shrinks the tile count, and the clipped
                      blob's own shape/arithmetic.
  blob_summary      — drops the "z" (and "band") keys.
  MICRO_BAND        — the single micro-region zoom-band constant.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from apps.regions.services.basemap_tiles import (
    DOWNLOAD_CEILING_MB,
    MICRO_BAND,
    WORST_CASE_BYTES_PER_TILE,
    bbox_from_boundary,
    blob_summary,
    build_blob,
    build_region_blob,
    centre_tile,
    clip_ranges,
    lon_lat_to_tile,
    row_tile_count,
    tile_count,
    tile_ranges,
)

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "apps"
    / "regions"
    / "fixtures"
)


def _ch_region_boundaries() -> list[dict[str, Any]]:
    """Return every real ``regions.microregion`` boundary in the CH fixture."""
    entries = json.loads((_FIXTURE_DIR / "eaws_CH.json").read_text())
    return [
        entry["fields"]["boundary"]
        for entry in entries
        if entry["model"] == "regions.microregion" and entry["fields"].get("boundary")
    ]


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


def test_build_blob_golden_vector_matches_js_twin() -> None:
    """Hand-checked build_blob() output, asserted identically in JS.

    SNOW-522 re-ports this module's tile math into
    ``static/js/basemap_download_core.js`` for the custom-area download
    (a user-drawn bbox has no stable ID to precompute against
    server-side — see that module's header for the full rationale).
    There is no compiler/typechecker link between the two languages, so
    this bbox/band pair and its exact expected shape are asserted
    identically in ``tests/js/test_basemap_download_core.js``'s
    ``describe('buildBlob (golden vector)', ...)`` block — the twin
    assertion that is the parity guard for the deliberate re-port. A
    change to either implementation that isn't mirrored in the other
    will only be caught by keeping both of these up to date by hand.
    """
    bbox = [7.0, 46.0, 7.2, 46.2]
    min_z, max_z = MICRO_BAND
    blob = build_blob(bbox, min_z, max_z)
    assert blob == {
        "band": [10, 14],
        "count": 205,
        "mb": 11,
        "over_ceiling": False,
        "centre_tile": {"z": 14, "x": 8515, "y": 5822},
        "z": {
            "10": [531, 532, 363, 364],
            "11": [1063, 1064, 726, 728],
            "12": [2127, 2129, 1453, 1457],
            "13": [4255, 4259, 2907, 2914],
            "14": [8510, 8519, 5815, 5828],
        },
    }


# ---------------------------------------------------------------------------
# clip_ranges / row_tile_count / build_region_blob (SNOW-583)
# ---------------------------------------------------------------------------


def test_clip_ranges_is_a_subset_of_the_candidate_rectangle() -> None:
    """The load-bearing invariant: clipping only ever DROPS candidate tiles.

    Candidates always come from tile_ranges() over the UNDILATED bbox, so
    every clipped tile's x/y must fall inside that same rectangle at its
    zoom level — never outside it. Checked over every real CH region
    boundary, not a hand-built one: this is the property SNOW-583's
    whole approach rests on (an already-downloaded region must stay fully
    cached once regions clip).
    """
    min_z, max_z = MICRO_BAND
    for boundary in _ch_region_boundaries():
        bbox = bbox_from_boundary(boundary)
        candidates = tile_ranges(bbox, min_z, max_z)
        clipped = clip_ranges(boundary, candidates, max_z)
        for z_str, rows in clipped.items():
            cand_xmin, cand_xmax, cand_ymin, cand_ymax = candidates[z_str]
            for y_str, (row_xmin, row_xmax) in rows.items():
                y = int(y_str)
                assert cand_ymin <= y <= cand_ymax
                assert cand_xmin <= row_xmin <= row_xmax <= cand_xmax


def test_clip_ranges_shrinks_the_tile_count_for_a_real_boundary() -> None:
    """Clipping to a real (non-rectangular) region boundary drops tiles.

    Uses the whole CH fixture rather than one hand-picked region, and
    asserts on the total — a handful of near-rectangular regions may clip
    to nothing, but the aggregate must shrink, matching the ~28.5%
    measured saving across all 149 CH regions (see the module docstring).
    """
    min_z, max_z = MICRO_BAND
    total_candidates = 0
    total_clipped = 0
    for boundary in _ch_region_boundaries():
        bbox = bbox_from_boundary(boundary)
        candidates = tile_ranges(bbox, min_z, max_z)
        total_candidates += tile_count(candidates)
        total_clipped += row_tile_count(clip_ranges(boundary, candidates, max_z))
    assert total_clipped < total_candidates
    saving = 1 - (total_clipped / total_candidates)
    assert 0.2 < saving < 0.35


def test_clip_ranges_row_span_shape() -> None:
    """Each present row is a single [xmin, xmax] span, keyed by row (y)."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]
        ],
    }
    min_z, max_z = MICRO_BAND
    bbox = bbox_from_boundary(boundary)
    candidates = tile_ranges(bbox, min_z, max_z)
    clipped = clip_ranges(boundary, candidates, max_z)
    assert set(clipped.keys()) <= {str(z) for z in range(min_z, max_z + 1)}
    for rows in clipped.values():
        for xmin, xmax in rows.values():
            assert xmin <= xmax


def test_row_tile_count_sums_row_spans() -> None:
    """row_tile_count sums (xmax - xmin + 1) over every row and zoom."""
    z_map = {"10": {"5": [1, 3], "6": [2, 2]}, "11": {"10": [0, 0]}}
    # z=10: (3-1+1) + (2-2+1) = 3 + 1 = 4
    # z=11: (0-0+1) = 1
    assert row_tile_count(z_map) == 5


def test_build_region_blob_shape_and_arithmetic() -> None:
    """build_region_blob carries the same key set as build_blob, clipped."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]
        ],
    }
    min_z, max_z = MICRO_BAND
    blob = build_region_blob(boundary, min_z, max_z)

    assert blob["band"] == [min_z, max_z]
    assert set(blob["z"].keys()) <= {str(z) for z in range(min_z, max_z + 1)}
    assert blob["count"] == row_tile_count(blob["z"])
    assert blob["mb"] == math.ceil(
        blob["count"] * WORST_CASE_BYTES_PER_TILE / (1024 * 1024)
    )
    assert blob["over_ceiling"] is (blob["mb"] > DOWNLOAD_CEILING_MB)
    assert blob["centre_tile"] == centre_tile(bbox_from_boundary(boundary), max_z)


def test_build_region_blob_count_is_less_than_or_equal_to_build_blob() -> None:
    """A region's clipped count never exceeds its bbox's rectangular count."""
    min_z, max_z = MICRO_BAND
    for boundary in _ch_region_boundaries():
        bbox = bbox_from_boundary(boundary)
        rect = build_blob(bbox, min_z, max_z)
        region = build_region_blob(boundary, min_z, max_z)
        assert region["count"] <= rect["count"]


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
