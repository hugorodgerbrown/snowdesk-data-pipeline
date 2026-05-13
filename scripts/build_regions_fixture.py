"""
scripts/build_regions_fixture.py — Builds the MicroRegion slice of eaws.json.

Reads a CSV of EAWS micro-regions and produces a Django fixture file for the
regions.MicroRegion model.  Two CSV files are supported:

  docs/eaws_regions_ch.csv     — Swiss (SLF/CH) regions; 113 rows.
  docs/eaws_regions_euregio.csv — EUREGIO (AT/IT) regions; 70 rows.

Both CSVs share the same column schema:

  region_id, region_name, slug, centre, boundary[, subregion_prefix]

The optional ``subregion_prefix`` column overrides the default parent
sub-region lookup, which is ``region_id[:5]`` for CH regions. EUREGIO
regions that map to L2 prefixes longer than 5 characters (e.g.
``IT-32-BZ`` for South Tyrol, ``IT-32-TN`` for Trentino) must supply
this column; AT-07 regions may omit it (the first 5 chars already give
the correct L2 prefix ``AT-07``).

Each record omits pk and uuid (so Django assigns them) and sets
created_at/updated_at to 2026-04-13T00:00:00Z to match the existing
resorts.json fixture pattern.

Boundary polygon rings are defensively closed (first position appended as
last if missing) so the fixture always satisfies RFC 7946 §3.1.6, even if
a hand-edited CSV row forgets the closing vertex.

Geographic neighbours (regions whose polygons share a border) are computed
once here using Shapely and emitted on each record as ``neighbours`` —
a list of natural keys (each ``["region_id"]``) consumed by Django's
``loaddata`` natural-key M2M format. The graph is symmetric by
construction.

Usage:

  # CH regions → regions/fixtures/eaws.json (only MicroRegion records)
  python scripts/build_regions_fixture.py

  # EUREGIO regions → regions/fixtures/eaws_euregio.json
  python scripts/build_regions_fixture.py euregio

Running either mode writes only MicroRegion records. The MajorRegion and
SubRegion rows that must precede them in the fixture are maintained by hand
in regions/fixtures/eaws.json (for CH) and regions/fixtures/eaws_euregio.json
(for EUREGIO).
"""

import csv
import json
import logging
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, shape

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH_CH = REPO_ROOT / "docs" / "eaws_regions_ch.csv"
CSV_PATH_EUREGIO = REPO_ROOT / "docs" / "eaws_regions_euregio.csv"
FIXTURE_PATH_CH = REPO_ROOT / "regions" / "fixtures" / "eaws.json"
FIXTURE_PATH_EUREGIO = REPO_ROOT / "regions" / "fixtures" / "eaws_euregio.json"

CREATED_AT = "2026-04-13T00:00:00Z"
UPDATED_AT = "2026-04-13T00:00:00Z"

# ~10 m at Swiss/Alpine latitudes — absorbs the sub-metre float gaps that show
# up between cantonal polygons where the same boundary line was re-digitised
# from two sides. Strict ``polygon_a.touches(polygon_b)`` misses these pairs,
# so we buffer one polygon by EPS and test ``intersects``.
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


def _derive_subregion_prefix(region_id: str, explicit: str | None) -> str:
    """
    Return the L2 sub-region prefix for a micro-region.

    For CH regions the first 5 characters of ``region_id`` always give the
    correct L2 prefix (e.g. ``CH-4115`` → ``CH-41``).  EUREGIO regions
    with longer L2 prefixes (e.g. ``IT-32-BZ-01`` → ``IT-32-BZ``) must
    supply an explicit override via the optional ``subregion_prefix``
    CSV column.

    Args:
        region_id: The micro-region identifier.
        explicit: Value of the ``subregion_prefix`` CSV column, or None.

    Returns:
        The L2 sub-region prefix string.

    """
    if explicit:
        return explicit.strip()
    return region_id[:5]


def build_fixture(csv_path: Path, fixture_path: Path) -> None:
    """
    Read the CSV and write a Django fixture for regions.MicroRegion.

    Only MicroRegion records are written. The MajorRegion and SubRegion
    rows referenced by natural key must already exist in the fixture file
    or in a previously loaded fixture.

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
            subregion_prefix = _derive_subregion_prefix(
                region_id, row.get("subregion_prefix")
            )
            rows.append(
                {
                    "region_id": region_id,
                    "name": row["region_name"].strip(),
                    "slug": row["slug"].strip(),
                    "subregion_prefix": subregion_prefix,
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
                "model": "regions.microregion",
                "fields": {
                    "region_id": region_id,
                    "name": row["name"],
                    "slug": row["slug"],
                    # Parent L2 sub-region natural key.  For CH regions this
                    # is region_id[:5]; for EUREGIO regions the explicit
                    # subregion_prefix column overrides this default.
                    "subregion": [row["subregion_prefix"]],
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
    mode = sys.argv[1] if len(sys.argv) > 1 else "ch"
    if mode == "euregio":
        build_fixture(CSV_PATH_EUREGIO, FIXTURE_PATH_EUREGIO)
    else:
        build_fixture(CSV_PATH_CH, FIXTURE_PATH_CH)
