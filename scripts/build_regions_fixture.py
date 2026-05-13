"""
scripts/build_regions_fixture.py — Builds Django fixture files for EAWS regions.

Two build modes are supported: ``ch`` (Swiss/SLF regions) and ``euregio``
(Tyrol / South Tyrol / Trentino, the ALBINA/EUREGIO area).

CH mode
-------
Reads docs/eaws_regions_ch.csv and produces ``regions/fixtures/eaws.json``
with MicroRegion records only.  The MajorRegion and SubRegion parent rows
that must precede them are maintained by hand in that fixture file.

The CSV schema is:

  region_id, region_name, slug, centre, boundary[, subregion_prefix]

Boundary polygon rings are defensively closed (first position appended as
last if missing) so the fixture always satisfies RFC 7946 §3.1.6, even if
a hand-edited CSV row forgets the closing vertex.

EUREGIO mode
------------
Reads three GeoJSON FeatureCollection files:

  docs/eaws_regions_at-07.geojson    — Tyrol micro-regions
  docs/eaws_regions_it-32-bz.geojson — South Tyrol micro-regions
  docs/eaws_regions_it-32-tn.geojson — Trentino micro-regions

Source: https://gitlab.com/eaws/eaws-regions (CC0 licence)

Each GeoJSON feature has ``properties.id`` (the EAWS region ID),
``properties.start_date``, ``properties.end_date``, and a ``geometry``
(always MultiPolygon in the upstream files).  When a region ID appears in
more than one feature (historical boundary revisions), the active feature
is selected by ``end_date == null``.

Region names are extracted from the sample EUREGIO bulletin file at
``sample_data/EUREGIO_en_CAAMLv6.json``.  Any micro-region ID present
in the GeoJSON but absent from that bulletin file is skipped.

Parent-region derivation rules:

  AT-07-NN        → major=AT-07,    sub=AT-07        (passthrough)
  AT-07-NN-XX     → major=AT-07,    sub=AT-07-NN
  IT-32-BZ-NN     → major=IT-32-BZ, sub=IT-32-BZ     (passthrough)
  IT-32-BZ-NN-XX  → major=IT-32-BZ, sub=IT-32-BZ-NN
  IT-32-TN-NN     → major=IT-32-TN, sub=IT-32-TN     (passthrough)
  IT-32-TN-NN-XX  → major=IT-32-TN, sub=IT-32-TN-NN  (none in current data)

EUREGIO mode produces ``regions/fixtures/eaws_euregio.json`` containing:

  * MajorRegion rows: AT-07, IT-32-BZ, IT-32-TN
  * SubRegion rows: one per distinct sub prefix derived above
  * MicroRegion rows: one per bulletin-referenced micro-region

L1/L2 geometry is derived from the union of child polygons (Shapely).
The neighbour graph is computed via the existing helper.

Usage:

  # CH regions → regions/fixtures/eaws.json (MicroRegion records only)
  python scripts/build_regions_fixture.py

  # EUREGIO regions → regions/fixtures/eaws_euregio.json (full fixture)
  python scripts/build_regions_fixture.py euregio

Geographic neighbours (regions whose polygons share a border) are computed
once here using Shapely and emitted on each MicroRegion record as
``neighbours`` — a list of natural keys (each ``["region_id"]``) consumed
by Django's ``loaddata`` natural-key M2M format. The graph is symmetric by
construction.
"""

import csv
import json
import logging
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH_CH = REPO_ROOT / "docs" / "eaws_regions_ch.csv"
GEOJSON_AT07 = REPO_ROOT / "docs" / "eaws_regions_at-07.geojson"
GEOJSON_IT_BZ = REPO_ROOT / "docs" / "eaws_regions_it-32-bz.geojson"
GEOJSON_IT_TN = REPO_ROOT / "docs" / "eaws_regions_it-32-tn.geojson"
FIXTURE_PATH_CH = REPO_ROOT / "regions" / "fixtures" / "eaws.json"
FIXTURE_PATH_EUREGIO = REPO_ROOT / "regions" / "fixtures" / "eaws_euregio.json"

# Bulletin file used to extract the 90 referenced region IDs and their names.
BULLETIN_PATH = REPO_ROOT / "sample_data" / "EUREGIO_en_CAAMLv6.json"

CREATED_AT = "2026-04-13T00:00:00Z"
UPDATED_AT = "2026-04-13T00:00:00Z"

# ~10 m at Swiss/Alpine latitudes — absorbs the sub-metre float gaps that show
# up between cantonal polygons where the same boundary line was re-digitised
# from two sides. Strict ``polygon_a.touches(polygon_b)`` misses these pairs,
# so we buffer one polygon by EPS and test ``intersects``.
NEIGHBOUR_EPS_DEGREES = 1e-4

# ---------------------------------------------------------------------------
# EUREGIO static metadata — names and country codes for the three top-level
# major regions and their passthrough sub-regions.  Intermediate sub-regions
# (e.g. AT-07-02, IT-32-BZ-01) receive auto-generated names from their prefix.
# ---------------------------------------------------------------------------

_EUREGIO_MAJOR_META: dict[str, dict[str, str]] = {
    "AT-07": {"country": "AT", "name_native": "Tirol", "name_en": "Tyrol"},
    "IT-32-BZ": {"country": "IT", "name_native": "Südtirol", "name_en": "South Tyrol"},
    "IT-32-TN": {"country": "IT", "name_native": "Trentino", "name_en": "Trentino"},
}

_EUREGIO_PASSTHROUGH_SUB_META: dict[str, dict[str, str]] = {
    "AT-07": {"name_native": "Tirol", "name_en": "Tyrol"},
    "IT-32-BZ": {"name_native": "Südtirol", "name_en": "South Tyrol"},
    "IT-32-TN": {"name_native": "Trentino", "name_en": "Trentino"},
}


def _close_polygon_rings(boundary: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of ``boundary`` with every linear ring closed.

    GeoJSON (RFC 7946 §3.1.6) requires each ring's first and last positions
    to be identical. If the source data is missing the closing vertex, this
    helper appends a copy of the first coordinate so the downstream map
    line layer draws the full outline without a visible gap.

    MultiPolygon geometries are handled by closing each polygon's rings.
    Non-polygon input is returned unchanged.

    Args:
        boundary: A GeoJSON Polygon or MultiPolygon geometry object.

    Returns:
        A new boundary dict with closed rings; non-polygon input is
        returned unchanged.

    """
    geom_type = boundary.get("type")
    if geom_type == "Polygon":
        rings = boundary.get("coordinates", [])
        closed_rings: list[list[list[float]]] = []
        for ring in rings:
            if isinstance(ring, list) and len(ring) >= 2 and ring[0] != ring[-1]:
                closed_rings.append([*ring, ring[0]])
            else:
                closed_rings.append(ring)
        return {**boundary, "coordinates": closed_rings}
    if geom_type == "MultiPolygon":
        polygons = boundary.get("coordinates", [])
        closed_polygons: list[list[list[list[float]]]] = []
        for poly_rings in polygons:
            closed_poly_rings: list[list[list[float]]] = []
            for ring in poly_rings:
                if isinstance(ring, list) and len(ring) >= 2 and ring[0] != ring[-1]:
                    closed_poly_rings.append([*ring, ring[0]])
                else:
                    closed_poly_rings.append(ring)
            closed_polygons.append(closed_poly_rings)
        return {**boundary, "coordinates": closed_polygons}
    return boundary


def _compute_neighbour_graph(
    boundaries: list[tuple[str, dict[str, Any]]],
    eps: float = NEIGHBOUR_EPS_DEGREES,
) -> dict[str, list[str]]:
    """
    Return a region_id → sorted list of neighbour region_ids mapping.

    Two regions are considered neighbours when one polygon, expanded by
    ``eps`` degrees, intersects the other. The buffer absorbs the small
    float gaps that appear between independently-digitised boundaries,
    where strict ``touches()`` would falsely report a near-miss. The
    resulting graph is symmetric — every adjacency is recorded on both
    endpoints.

    Accepts both Polygon and MultiPolygon geometries.

    Args:
        boundaries: Pairs of (region_id, GeoJSON geometry dict).
        eps: Buffer distance in degrees applied before the intersect test.

    Returns:
        ``{region_id: [neighbour_id, …]}`` with neighbour lists sorted
        alphabetically. Regions with no neighbours are present with an
        empty list.

    """
    polygons: dict[str, Polygon | MultiPolygon] = {
        rid: shape(b) for rid, b in boundaries
    }
    neighbours: dict[str, set[str]] = {rid: set() for rid in polygons}

    for (rid_a, poly_a), (rid_b, poly_b) in combinations(polygons.items(), 2):
        if poly_a.buffer(eps).intersects(poly_b):
            neighbours[rid_a].add(rid_b)
            neighbours[rid_b].add(rid_a)

    return {rid: sorted(ns) for rid, ns in neighbours.items()}


def _derive_subregion_prefix(region_id: str, explicit: str | None) -> str:
    """
    Return the L2 sub-region prefix for a micro-region (CH path).

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


def _derive_euregio_parent_pair(region_id: str) -> tuple[str, str]:
    """
    Derive the (major_prefix, sub_prefix) pair for an EUREGIO micro-region.

    Rules:

    * ``AT-07-NN``       → major=``AT-07``,    sub=``AT-07``        (passthrough)
    * ``AT-07-NN-XX``    → major=``AT-07``,    sub=``AT-07-NN``
    * ``IT-32-BZ-NN``    → major=``IT-32-BZ``, sub=``IT-32-BZ``     (passthrough)
    * ``IT-32-BZ-NN-XX`` → major=``IT-32-BZ``, sub=``IT-32-BZ-NN``
    * ``IT-32-TN-NN``    → major=``IT-32-TN``, sub=``IT-32-TN``     (passthrough)
    * ``IT-32-TN-NN-XX`` → major=``IT-32-TN``, sub=``IT-32-TN-NN``

    Args:
        region_id: An EUREGIO micro-region identifier.

    Returns:
        Tuple of (major_prefix, sub_prefix).

    Raises:
        ValueError: If the region_id does not match any known EUREGIO scheme.

    """
    parts = region_id.split("-")
    if region_id.startswith("AT-07"):
        major = "AT-07"
        sub = f"AT-07-{parts[2]}" if len(parts) == 4 else "AT-07"
    elif region_id.startswith("IT-32-BZ"):
        major = "IT-32-BZ"
        sub = f"IT-32-BZ-{parts[3]}" if len(parts) == 5 else "IT-32-BZ"
    elif region_id.startswith("IT-32-TN"):
        major = "IT-32-TN"
        sub = f"IT-32-TN-{parts[3]}" if len(parts) == 5 else "IT-32-TN"
    else:
        raise ValueError(f"Cannot derive EUREGIO parent pair for: {region_id!r}")
    return major, sub


def _shapely_to_geojson(geom: Polygon | MultiPolygon) -> dict[str, Any]:
    """
    Convert a Shapely geometry to a GeoJSON-compatible dict.

    Always returns either a Polygon or MultiPolygon.  If Shapely produces
    a GeometryCollection (e.g. from a degenerate union), it is treated as
    a MultiPolygon of the polygon members.

    Args:
        geom: A Shapely geometry object.

    Returns:
        A GeoJSON geometry dict.

    """
    result: dict[str, Any] = mapping(geom)  # type: ignore[assignment]
    return result


def _union_geometries(
    geom_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return the spatial union of a list of GeoJSON geometry dicts.

    Uses Shapely's ``unary_union`` to merge all geometries into one.
    The result is always a Polygon or MultiPolygon.

    Args:
        geom_list: List of GeoJSON geometry dicts (Polygon or MultiPolygon).

    Returns:
        A GeoJSON geometry dict representing the union.

    """
    shapes = [shape(g) for g in geom_list]
    merged = unary_union(shapes)
    return _shapely_to_geojson(merged)


def _compute_centre(geom_dict: dict[str, Any]) -> dict[str, float]:
    """
    Return the centroid of a GeoJSON geometry as ``{"lon": …, "lat": …}``.

    Args:
        geom_dict: A GeoJSON geometry dict.

    Returns:
        A dict with ``lon`` and ``lat`` float values.

    """
    centroid = shape(geom_dict).centroid
    return {"lon": centroid.x, "lat": centroid.y}


def _compute_bbox(geom_dict: dict[str, Any]) -> list[float]:
    """
    Return the bounding box of a GeoJSON geometry.

    Args:
        geom_dict: A GeoJSON geometry dict.

    Returns:
        ``[min_lon, min_lat, max_lon, max_lat]``.

    """
    bounds = shape(geom_dict).bounds  # (minx, miny, maxx, maxy)
    return list(bounds)


def _load_geojson_features(
    path: Path,
) -> dict[str, dict[str, Any]]:
    """
    Load a GeoJSON FeatureCollection and return active features keyed by ID.

    Where the same region ID appears in multiple features (historical boundary
    revisions), only the feature with ``properties.end_date == null`` is kept
    (i.e. the currently active boundary).  If no active feature exists for a
    given ID, the most recently listed feature is used as a fallback.

    Args:
        path: Path to a GeoJSON FeatureCollection file.

    Returns:
        ``{region_id: feature_dict}`` mapping with one entry per unique ID.

    """
    with path.open(encoding="utf-8") as fh:
        collection = json.load(fh)

    active: dict[str, dict[str, Any]] = {}
    fallback: dict[str, dict[str, Any]] = {}

    for feature in collection.get("features", []):
        rid = feature["properties"]["id"]
        end_date = feature["properties"].get("end_date")
        if end_date is None:
            # Actively valid boundary — always prefer this.
            active[rid] = feature
        else:
            # Historical boundary — only keep as fallback.
            fallback[rid] = feature

    # Merge: prefer active, fall back to historical for any ID missing from active.
    result = {**fallback, **active}
    logger.debug(
        "Loaded %d features from %s (%d active)", len(result), path.name, len(active)
    )
    return result


def _load_bulletin_region_names(path: Path) -> dict[str, str]:
    """
    Return a ``{region_id: name}`` mapping extracted from a CAAML bulletin file.

    The bulletin JSON has the shape ``{"bulletins": [{…, "regions": [{"regionID": …,
    "name": …}]}]}``.  Where the same region ID appears in multiple bulletin entries,
    the first occurrence is kept (all duplicates are identical in practice).

    Args:
        path: Path to a CAAML v6 JSON bulletin file.

    Returns:
        A dict mapping each region ID to its English name.

    """
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    names: dict[str, str] = {}
    for bulletin in data.get("bulletins", []):
        for region in bulletin.get("regions", []):
            rid = region["regionID"]
            if rid not in names:
                names[rid] = region.get("name", rid)
    return names


def build_fixture(csv_path: Path, fixture_path: Path) -> None:
    """
    Read the CH CSV and write a Django fixture for regions.MicroRegion.

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


def _load_euregio_source_data(
    bulletin_path: Path,
    geojson_paths: tuple[Path, Path, Path],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """
    Load region names from the bulletin and geometries from the GeoJSON files.

    Validates that every bulletin ID has a corresponding GeoJSON feature.

    Args:
        bulletin_path: Path to the CAAML v6 JSON bulletin file.
        geojson_paths: Tuple of (AT-07, IT-32-BZ, IT-32-TN) GeoJSON paths.

    Returns:
        A tuple of (region_names, micro_geoms) where:
        - region_names maps each bulletin region ID to its English name.
        - micro_geoms maps each bulletin region ID to a closed GeoJSON geometry.

    Raises:
        FileNotFoundError: If the bulletin file does not exist.
        ValueError: If any bulletin region ID lacks a GeoJSON geometry.

    """
    if not bulletin_path.exists():
        raise FileNotFoundError(
            f"Sample bulletin not found: {bulletin_path}\n"
            "Copy the EUREGIO sample bulletin to sample_data/EUREGIO_en_CAAMLv6.json"
        )
    region_names = _load_bulletin_region_names(bulletin_path)
    bulletin_ids: set[str] = set(region_names)
    logger.info("Bulletin references %d unique micro-region IDs", len(bulletin_ids))

    all_features: dict[str, dict[str, Any]] = {}
    for path in geojson_paths:
        all_features.update(_load_geojson_features(path))

    missing = bulletin_ids - set(all_features)
    if missing:
        raise ValueError(
            f"{len(missing)} bulletin region ID(s) not found in any GeoJSON source: "
            f"{sorted(missing)}"
        )

    micro_geoms: dict[str, dict[str, Any]] = {
        rid: _close_polygon_rings(all_features[rid]["geometry"]) for rid in bulletin_ids
    }
    return region_names, micro_geoms


def _derive_euregio_parent_maps(
    bulletin_ids: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Derive micro→sub and sub→major mappings for all bulletin IDs.

    Args:
        bulletin_ids: Set of bulletin micro-region IDs.

    Returns:
        A tuple of (micro_to_sub, sub_to_major) dicts.

    """
    sub_to_major: dict[str, str] = {}
    micro_to_sub: dict[str, str] = {}
    for rid in sorted(bulletin_ids):
        major, sub = _derive_euregio_parent_pair(rid)
        micro_to_sub[rid] = sub
        sub_to_major[sub] = major
    return micro_to_sub, sub_to_major


def _build_aggregate_geoms(
    micro_geoms: dict[str, dict[str, Any]],
    micro_to_sub: dict[str, str],
    sub_to_major: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    Compute union geometries for sub-regions and major regions.

    Args:
        micro_geoms: Maps each micro-region ID to its GeoJSON geometry.
        micro_to_sub: Maps each micro-region ID to its sub-region prefix.
        sub_to_major: Maps each sub-region prefix to its major-region prefix.

    Returns:
        A tuple of (sub_geoms, major_geoms) where each maps a prefix to a
        closed GeoJSON geometry dict.

    """
    sub_children: dict[str, list[str]] = {sp: [] for sp in sub_to_major}
    for rid, sub in micro_to_sub.items():
        sub_children[sub].append(rid)

    all_major_prefixes = sorted(set(sub_to_major.values()))
    major_children: dict[str, list[str]] = {mp: [] for mp in all_major_prefixes}
    for rid, sub in micro_to_sub.items():
        major_children[sub_to_major[sub]].append(rid)

    sub_geoms: dict[str, dict[str, Any]] = {
        sub: _close_polygon_rings(_union_geometries([micro_geoms[r] for r in children]))
        for sub, children in sub_children.items()
    }
    major_geoms: dict[str, dict[str, Any]] = {
        major: _close_polygon_rings(
            _union_geometries([micro_geoms[r] for r in children])
        )
        for major, children in major_children.items()
    }
    return sub_geoms, major_geoms


def _assemble_euregio_records(
    bulletin_ids: set[str],
    region_names: dict[str, str],
    micro_geoms: dict[str, dict[str, Any]],
    micro_to_sub: dict[str, str],
    sub_to_major: dict[str, str],
    sub_geoms: dict[str, dict[str, Any]],
    major_geoms: dict[str, dict[str, Any]],
    neighbour_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """
    Assemble the ordered list of fixture records.

    Emits MajorRegion → SubRegion → MicroRegion (loaddata dependency order).

    Args:
        bulletin_ids: Set of micro-region IDs.
        region_names: Maps region ID → English name.
        micro_geoms: Maps region ID → closed GeoJSON geometry.
        micro_to_sub: Maps micro ID → sub prefix.
        sub_to_major: Maps sub prefix → major prefix.
        sub_geoms: Maps sub prefix → closed GeoJSON geometry.
        major_geoms: Maps major prefix → closed GeoJSON geometry.
        neighbour_map: Maps region ID → list of neighbour IDs.

    Returns:
        List of fixture record dicts ready for JSON serialisation.

    """
    records: list[dict[str, Any]] = []
    all_major_prefixes = sorted(set(sub_to_major.values()))
    all_sub_prefixes = sorted(sub_to_major)

    for major in all_major_prefixes:
        meta = _EUREGIO_MAJOR_META[major]
        geom = major_geoms[major]
        records.append(
            {
                "model": "regions.majorregion",
                "fields": {
                    "prefix": major,
                    "country": meta["country"],
                    "name_native": meta["name_native"],
                    "name_en": meta["name_en"],
                    "centre": _compute_centre(geom),
                    "bbox": _compute_bbox(geom),
                    "boundary": geom,
                    "created_at": CREATED_AT,
                    "updated_at": UPDATED_AT,
                },
            }
        )

    for sub in all_sub_prefixes:
        major = sub_to_major[sub]
        geom = sub_geoms[sub]
        passthrough_meta = _EUREGIO_PASSTHROUGH_SUB_META.get(sub, {})
        name_native = passthrough_meta.get("name_native", sub)
        name_en = passthrough_meta.get("name_en", sub)
        records.append(
            {
                "model": "regions.subregion",
                "fields": {
                    "prefix": sub,
                    "major": [major],
                    "name_native": name_native,
                    "name_en": name_en,
                    "centre": _compute_centre(geom),
                    "bbox": _compute_bbox(geom),
                    "boundary": geom,
                    "created_at": CREATED_AT,
                    "updated_at": UPDATED_AT,
                },
            }
        )

    for rid in sorted(bulletin_ids):
        geom = micro_geoms[rid]
        records.append(
            {
                "model": "regions.microregion",
                "fields": {
                    "region_id": rid,
                    "name": region_names[rid],
                    "slug": rid.lower().replace("-", "_"),
                    "subregion": [micro_to_sub[rid]],
                    "centre": _compute_centre(geom),
                    "boundary": geom,
                    "neighbours": [[nid] for nid in neighbour_map[rid]],
                    "created_at": CREATED_AT,
                    "updated_at": UPDATED_AT,
                },
            }
        )

    return records


def build_euregio_fixture() -> None:
    """
    Build the complete EUREGIO fixture from the three GeoJSON source files.

    Reads the bulletin file at ``sample_data/EUREGIO_en_CAAMLv6.json`` to
    establish which micro-region IDs to include and to extract their English
    names.  Only IDs referenced in that bulletin are emitted.  Any ID present
    in the GeoJSON but absent from the bulletin is silently skipped.

    Writes ``regions/fixtures/eaws_euregio.json`` containing:

    * ``regions.majorregion`` rows: AT-07, IT-32-BZ, IT-32-TN
    * ``regions.subregion`` rows: one per distinct sub prefix derived from
      the 90 bulletin micro-region IDs
    * ``regions.microregion`` rows: 90 records (one per bulletin ID)

    L1/L2 geometry is derived from the union of child polygons using Shapely.

    Raises:
        FileNotFoundError: If any source GeoJSON or the bulletin file is absent.
        ValueError: If a bulletin region ID is not found in any GeoJSON source.

    """
    region_names, micro_geoms = _load_euregio_source_data(
        BULLETIN_PATH, (GEOJSON_AT07, GEOJSON_IT_BZ, GEOJSON_IT_TN)
    )
    bulletin_ids = set(region_names)

    micro_to_sub, sub_to_major = _derive_euregio_parent_maps(bulletin_ids)
    all_sub_prefixes = sorted(sub_to_major)
    all_major_prefixes = sorted(set(sub_to_major.values()))
    logger.info(
        "Derived %d major prefix(es) and %d sub prefix(es)",
        len(all_major_prefixes),
        len(all_sub_prefixes),
    )

    sub_geoms, major_geoms = _build_aggregate_geoms(
        micro_geoms, micro_to_sub, sub_to_major
    )

    boundaries: list[tuple[str, dict[str, Any]]] = list(micro_geoms.items())
    neighbour_map = _compute_neighbour_graph(boundaries)
    counts = [len(neighbour_map[rid]) for rid in bulletin_ids]
    isolated = [rid for rid in sorted(bulletin_ids) if not neighbour_map[rid]]
    logger.info(
        "Computed neighbours: %d regions, mean=%.1f, min=%d, max=%d, isolated=%d",
        len(bulletin_ids),
        sum(counts) / len(counts) if counts else 0,
        min(counts) if counts else 0,
        max(counts) if counts else 0,
        len(isolated),
    )
    if isolated:
        logger.warning("Micro-regions with zero neighbours: %s", isolated)

    records = _assemble_euregio_records(
        bulletin_ids,
        region_names,
        micro_geoms,
        micro_to_sub,
        sub_to_major,
        sub_geoms,
        major_geoms,
        neighbour_map,
    )

    FIXTURE_PATH_EUREGIO.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURE_PATH_EUREGIO.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    logger.info(
        "Wrote %d records to %s (%d major, %d sub, %d micro)",
        len(records),
        FIXTURE_PATH_EUREGIO,
        len(all_major_prefixes),
        len(all_sub_prefixes),
        len(bulletin_ids),
    )


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "ch"
    if mode == "euregio":
        build_euregio_fixture()
    else:
        build_fixture(CSV_PATH_CH, FIXTURE_PATH_CH)
