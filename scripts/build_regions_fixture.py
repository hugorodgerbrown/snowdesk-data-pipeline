"""
scripts/build_regions_fixture.py — Generates pipeline/fixtures/regions.json.

Reads docs/eaws_regions_ch.csv and produces a Django fixture file for the
pipeline.Region model. Each record omits pk and uuid (so Django assigns them)
and sets created_at/updated_at to 2026-04-13T00:00:00Z to match the existing
resorts.json fixture pattern.

Boundary polygon rings are defensively closed (first position appended as
last if missing) so the fixture always satisfies RFC 7946 §3.1.6, even if
a hand-edited CSV row forgets the closing vertex.

Geographic neighbours (regions whose polygons share a border) are computed
once here using Shapely and emitted on each record as ``neighbours`` —
a list of natural keys (each ``["CH-xxxx"]``) consumed by Django's
``loaddata`` natural-key M2M format. The graph is symmetric by
construction. SLF only ships CH regions, so no cross-country filter is
required; if foreign regions are added later, the algorithm needs no
changes.
"""

import csv
import json
import logging
from itertools import combinations
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, shape

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "docs" / "eaws_regions_ch.csv"
FIXTURE_PATH = REPO_ROOT / "pipeline" / "fixtures" / "regions.json"

CREATED_AT = "2026-04-13T00:00:00Z"
UPDATED_AT = "2026-04-13T00:00:00Z"

# ~10 m at Swiss latitudes — absorbs the sub-metre float gaps that show up
# between cantonal polygons where the same boundary line was re-digitised
# from two sides. Strict ``polygon_a.touches(polygon_b)`` misses these
# pairs, so we buffer one polygon by EPS and test ``intersects``.
NEIGHBOUR_EPS_DEGREES = 1e-4


def _close_polygon_rings(boundary: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of ``boundary`` with every linear ring closed.

    GeoJSON (RFC 7946 §3.1.6) requires each ring's first and last positions
    to be identical. If the CSV row is missing the closing vertex, this
    helper appends a copy of the first coordinate so the downstream map
    line layer draws the full outline without a visible gap.

    Args:
        boundary: A GeoJSON Polygon geometry object.

    Returns:
        A new boundary dict with closed rings; non-polygon input is
        returned unchanged.

    """
    if boundary.get("type") != "Polygon":
        return boundary
    rings = boundary.get("coordinates", [])
    closed_rings: list[list[list[float]]] = []
    for ring in rings:
        if isinstance(ring, list) and len(ring) >= 2 and ring[0] != ring[-1]:
            closed_rings.append([*ring, ring[0]])
        else:
            closed_rings.append(ring)
    return {**boundary, "coordinates": closed_rings}


def _compute_neighbour_graph(
    boundaries: list[tuple[str, dict[str, Any]]],
    eps: float = NEIGHBOUR_EPS_DEGREES,
) -> dict[str, list[str]]:
    """
    Return a region_id → sorted list of neighbour region_ids mapping.

    Two regions are considered neighbours when one polygon, expanded by
    ``eps`` degrees, intersects the other. The buffer absorbs the small
    float gaps that appear between independently-digitised cantonal
    boundaries, where strict ``touches()`` would falsely report a near-
    miss. The resulting graph is symmetric — every adjacency is recorded
    on both endpoints.

    Args:
        boundaries: Pairs of (region_id, GeoJSON Polygon dict).
        eps: Buffer distance in degrees applied before the intersect test.

    Returns:
        ``{region_id: [neighbour_id, …]}`` with neighbour lists sorted
        alphabetically. Regions with no neighbours are present with an
        empty list.

    """
    polygons: dict[str, Polygon] = {rid: shape(b) for rid, b in boundaries}
    neighbours: dict[str, set[str]] = {rid: set() for rid in polygons}

    for (rid_a, poly_a), (rid_b, poly_b) in combinations(polygons.items(), 2):
        if poly_a.buffer(eps).intersects(poly_b):
            neighbours[rid_a].add(rid_b)
            neighbours[rid_b].add(rid_a)

    return {rid: sorted(ns) for rid, ns in neighbours.items()}


def build_fixture(csv_path: Path, fixture_path: Path) -> None:
    """
    Read the CSV and write a Django JSON fixture for pipeline.Region.

    Args:
        csv_path: Path to the source CSV file.
        fixture_path: Destination path for the generated JSON fixture.

    """
    rows: list[dict[str, Any]] = []
    boundaries: list[tuple[str, dict[str, Any]]] = []

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            centre = json.loads(row["centre"])
            boundary = _close_polygon_rings(json.loads(row["boundary"]))
            region_id = row["region_id"].strip()
            rows.append(
                {
                    "region_id": region_id,
                    "name": row["region_name"].strip(),
                    "slug": row["slug"].strip(),
                    "centre": centre,
                    "boundary": boundary,
                }
            )
            boundaries.append((region_id, boundary))

    neighbour_map = _compute_neighbour_graph(boundaries)
    counts = [len(neighbour_map[r["region_id"]]) for r in rows]
    isolated = [r["region_id"] for r in rows if not neighbour_map[r["region_id"]]]
    logger.info(
        "Computed neighbours: %d regions, mean=%.1f, min=%d, max=%d, isolated=%d",
        len(rows),
        sum(counts) / len(counts) if counts else 0,
        min(counts) if counts else 0,
        max(counts) if counts else 0,
        len(isolated),
    )
    if isolated:
        logger.warning("Regions with zero neighbours: %s", isolated)

    records: list[dict[str, Any]] = []
    for row in rows:
        region_id = row["region_id"]
        records.append(
            {
                "model": "pipeline.region",
                "fields": {
                    "region_id": region_id,
                    "name": row["name"],
                    "slug": row["slug"],
                    # Parent L2 sub-region natural key (region_id[:5]).
                    # The referenced EawsSubRegion must exist in
                    # pipeline/fixtures/eaws_sub_regions.json.
                    "subregion": [region_id[:5]],
                    "centre": row["centre"],
                    "boundary": row["boundary"],
                    # Geographic neighbours as a list of natural keys —
                    # ``loaddata`` rehydrates the symmetric M2M from this.
                    "neighbours": [[nid] for nid in neighbour_map[region_id]],
                    "created_at": CREATED_AT,
                    "updated_at": UPDATED_AT,
                },
            }
        )

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    logger.info("Wrote %d region records to %s", len(records), fixture_path)


if __name__ == "__main__":
    build_fixture(CSV_PATH, FIXTURE_PATH)
