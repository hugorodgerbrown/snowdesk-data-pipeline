"""
scripts/build_regions_fixture.py — Builds the MicroRegion slice of eaws_CH.json.

Reads reference_data/slf/CH_micro-regions.csv and produces fixture entries for
the regions.MicroRegion model.  The script reads the existing eaws_CH.json
fixture, retains all non-MicroRegion rows (L1 MajorRegion and L2 SubRegion,
which are hand-maintained by refresh_eaws_fixtures), and replaces only the
regions.microregion entries.

Each MicroRegion record omits pk and uuid (so Django assigns them) and sets
created_at/updated_at to 2026-04-13T00:00:00Z to match the existing
resorts.json fixture pattern.

L4 names are resolved via ``regions.names.lookup(..., "de")`` from the
vendored EAWS ``reference_data/eaws/names/de.json`` (CC0), falling back to
the CSV ``region_name`` column when EAWS has no entry for that key.

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
CSV_PATH = REPO_ROOT / "reference_data" / "slf" / "CH_micro-regions.csv"
FIXTURE_PATH = REPO_ROOT / "regions" / "fixtures" / "eaws_CH.json"

# EAWS canonical German-language names for L4 regions. Loaded lazily on first use.
_EAWS_NAMES_DE_PATH = REPO_ROOT / "reference_data" / "eaws" / "names" / "de.json"
_eaws_names_de: dict[str, str] | None = None


def _get_eaws_name_de(region_id: str) -> str | None:
    """Return the EAWS canonical German name for *region_id*, or ``None`` on miss.

    Loads ``reference_data/eaws/names/de.json`` on first call and caches the
    result in ``_eaws_names_de``.

    Args:
        region_id: EAWS region identifier (e.g. ``"CH-3221"``).

    Returns:
        Human-readable German name string, or ``None`` if not found.

    """
    global _eaws_names_de  # noqa: PLW0603 — module-level cache; script context, not importable library
    if _eaws_names_de is None:
        _eaws_names_de = json.loads(_EAWS_NAMES_DE_PATH.read_text(encoding="utf-8"))
    return _eaws_names_de.get(region_id)


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
    Read the CSV and write the MicroRegion slice back into eaws_CH.json.

    Reads the existing ``fixture_path`` to retain any non-MicroRegion rows
    (L1 MajorRegion and L2 SubRegion entries, which are hand-maintained by
    ``refresh_eaws_fixtures``). Only the ``regions.microregion`` entries are
    replaced — derived from the CSV with EAWS de.json name overrides.

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

    microregion_records: list[dict[str, Any]] = []
    for row in rows:
        region_id = row["region_id"]
        # Prefer EAWS de.json canonical name; fall back to CSV region_name.
        name = _get_eaws_name_de(region_id) or row["name"]
        microregion_records.append(
            {
                "model": "regions.microregion",
                "fields": {
                    "region_id": region_id,
                    "name": name,
                    "slug": row["slug"],
                    # Parent L2 sub-region natural key (region_id[:5]).
                    # The referenced SubRegion must exist in
                    # regions/fixtures/eaws_CH.json.
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

    # Retain hand-maintained L1/L2 entries from the existing fixture;
    # replace only the regions.microregion slice.
    existing: list[dict[str, Any]] = []
    if fixture_path.exists():
        existing = json.loads(fixture_path.read_text(encoding="utf-8"))

    non_micro_entries = [e for e in existing if e.get("model") != "regions.microregion"]
    fixture_entries = non_micro_entries + microregion_records

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8") as fh:
        json.dump(fixture_entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    logger.info(
        "Wrote %d entries to %s (%d L1/L2 retained, %d microregions)",
        len(fixture_entries),
        fixture_path,
        len(non_micro_entries),
        len(microregion_records),
    )


# ---------------------------------------------------------------------------
# Shared geometry helpers — used by build_austria_fixture and
# build_italy_fixture (and available for future country commands).
# ---------------------------------------------------------------------------


def centre_from_children(children: list[dict[str, Any]]) -> dict[str, float]:
    """Return the arithmetic mean of the children's ``centre`` values.

    Args:
        children: List of field-dicts, each with a ``centre`` key.

    Returns:
        ``{"lon": float, "lat": float}`` centroid.

    """
    lons = [c["centre"]["lon"] for c in children if c.get("centre")]
    lats = [c["centre"]["lat"] for c in children if c.get("centre")]
    return {"lon": sum(lons) / len(lons), "lat": sum(lats) / len(lats)}


def bbox_from_children(children: list[dict[str, Any]]) -> list[float]:
    """Return ``[min_lon, min_lat, max_lon, max_lat]`` over all child boundaries.

    Args:
        children: List of field-dicts, each with a ``boundary`` key.

    Returns:
        Bounding box as ``[west, south, east, north]``.

    """
    all_lons: list[float] = []
    all_lats: list[float] = []
    for child in children:
        boundary = child.get("boundary")
        if not boundary:
            continue
        for coord in _iter_coords_from_geometry(boundary):
            all_lons.append(coord[0])
            all_lats.append(coord[1])
    return [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]


def boundary_from_children(children: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge child boundaries into a single GeoJSON Polygon/MultiPolygon.

    Uses ``shapely.ops.unary_union`` — a dev-only dependency. Raises
    ``RuntimeError`` with install instructions if shapely is absent.

    Args:
        children: List of field-dicts, each with a ``boundary`` key.

    Returns:
        GeoJSON geometry dict (Polygon or MultiPolygon).

    """
    try:
        from shapely.geometry import mapping, shape as shp
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover — dev-only dependency
        raise RuntimeError(
            "boundary_from_children requires the dev-only `shapely` dependency. "
            "Install it with `poetry install --with dev`."
        ) from exc

    polys = [shp(child["boundary"]) for child in children if child.get("boundary")]
    union = unary_union(polys)
    return json.loads(json.dumps(mapping(union)))  # type: ignore[no-any-return]


def centre_from_bbox(geometry: dict[str, Any]) -> dict[str, float]:
    """Return the bbox midpoint of a GeoJSON geometry as ``{"lon": …, "lat": …}``.

    Args:
        geometry: A GeoJSON geometry object (Polygon or MultiPolygon).

    Returns:
        ``{"lon": float, "lat": float}`` centroid of the bounding box.

    """
    coords = _iter_coords_from_geometry(geometry)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return {
        "lon": (min(lons) + max(lons)) / 2,
        "lat": (min(lats) + max(lats)) / 2,
    }


def _iter_coords_from_geometry(
    geometry: dict[str, Any],
) -> list[tuple[float, float]]:
    """Return every ``(lon, lat)`` coordinate pair from a GeoJSON geometry.

    Handles both 2D (``[lon, lat]``) and 3D (``[lon, lat, alt]``) positions by
    keeping only the first two values — EAWS source files for some Italian
    regions carry a zero-altitude third component.

    Args:
        geometry: A GeoJSON geometry object (Polygon or MultiPolygon).

    Returns:
        List of ``(lon, lat)`` pairs.

    """
    geo_type: str = geometry["type"]
    if geo_type == "Polygon":
        return [(c[0], c[1]) for ring in geometry["coordinates"] for c in ring]
    if geo_type == "MultiPolygon":
        return [
            (c[0], c[1])
            for polygon in geometry["coordinates"]
            for ring in polygon
            for c in ring
        ]
    raise ValueError(f"Unsupported geometry type: {geo_type}")


if __name__ == "__main__":
    build_fixture(CSV_PATH, FIXTURE_PATH)
