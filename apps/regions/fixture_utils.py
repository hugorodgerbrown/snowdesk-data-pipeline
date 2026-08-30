"""apps/regions/fixture_utils.py — Shared geometry and fixture-I/O helpers.

Provides the geometry utility functions used by ``build_france_fixture``,
``build_austria_fixture``, ``build_italy_fixture``, and
``build_switzerland_fixture`` when computing parent-region geometry from
child (L4 MicroRegion) geometry, plus the fixture file-I/O helpers
(``load_fixture``, ``write_fixture``, ``diff_against_existing``,
``entry_key``) shared by those four builders and by
``refresh_eaws_fixtures`` (SNOW-488 — these were previously duplicated
verbatim in each command module).

All functions operate on GeoJSON geometry dicts and on lists of field-dicts
(the ``fields`` portion of a Django fixture entry).

Functions:
    build_entries_from_eaws_dir — shared entry-list builder for multi-file
        EAWS fixtures (Austria, Italy).
    centre_from_children — arithmetic mean of child centre points.
    bbox_from_children   — bounding box over all child boundaries.
    boundary_from_children — Shapely-derived union of child polygons.
    centre_from_bbox     — bbox midpoint of a single GeoJSON geometry.
    load_fixture          — read a Django fixture JSON file into entries.
    write_fixture         — write entries to a Django fixture JSON file.
    diff_against_existing — count entries that differ from the on-disk fixture.
    entry_key             — stable string key for a fixture entry.
    _iter_coords_from_geometry — flat list of (lon, lat) pairs from a geometry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def build_entries_from_eaws_dir(
    eaws_dir: Path,
    region_codes: list[str],
    build_code_entries: Callable[
        [str, list[dict[str, Any]]],
        tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]],
    ],
    command_name: str,
) -> list[dict[str, Any]]:
    """Build the full fixture entry list (L1 + L2 + L4) from a directory of EAWS files.

    Shared implementation for ``build_austria_fixture`` and
    ``build_italy_fixture``, which both iterate a set of region-code GeoJSON
    files and delegate per-code entry construction to a caller-supplied
    function.

    Args:
        eaws_dir: Directory containing the vendored EAWS source GeoJSON files.
        region_codes: List of EAWS region codes to process (e.g. ``['AT-02', …]``).
        build_code_entries: Callable that accepts ``(region_code, features)``
            and returns ``(l1_entry, l2_entries_by_prefix, l4_entries)``.
        command_name: Name used in warning log messages when a source file is
            missing (e.g. ``"build_austria_fixture"``).

    Returns:
        Ordered list of Django fixture entry dicts (L1s first, then L2s, then
        L4s, with L4s sorted by ``region_id``).

    """
    l1_entries: list[dict[str, Any]] = []
    l2_entries: list[dict[str, Any]] = []
    l4_entries: list[dict[str, Any]] = []

    for code in region_codes:
        source_file = eaws_dir / f"{code}_micro-regions.geojson.json"
        if not source_file.exists():
            logger.warning(
                "%s: missing source file %s — skipping", command_name, source_file
            )
            continue

        data: dict[str, Any] = json.loads(source_file.read_text(encoding="utf-8"))
        features: list[dict[str, Any]] = data["features"]

        l1, l2s, l4s = build_code_entries(code, features)
        l1_entries.append(l1)
        l2_entries.extend(l2s.values())
        l4_entries.extend(l4s)

    l4_entries.sort(key=lambda e: e["fields"]["region_id"])

    return l1_entries + l2_entries + l4_entries


def centre_from_children(children: list[dict[str, Any]]) -> dict[str, float]:
    """Return the arithmetic mean of the children's ``centre`` values.

    Args:
        children: List of field-dicts, each with a ``centre`` key.

    Returns:
        ``{"lon": float, "lat": float}`` centroid.

    Raises:
        ValueError: When ``children`` is empty or none of the children
            carry a ``centre`` value, which would produce a silent
            ``ZeroDivisionError``.

    """
    lons = [c["centre"]["lon"] for c in children if c.get("centre")]
    lats = [c["centre"]["lat"] for c in children if c.get("centre")]
    if not lons:
        raise ValueError(
            "cannot compute centre of empty children list — "
            "no children with a 'centre' key were found"
        )
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

    Uses ``shapely.ops.unary_union`` — a runtime dependency (promoted from
    dev-only in SNOW-323 because grouping dissolves now run at ingest time).
    Raises ``RuntimeError`` with install instructions if shapely is absent.

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
            "Install it with `uv sync`."
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


def load_fixture(path: Path, *, missing_ok: bool = True) -> list[dict[str, Any]]:
    """Read a Django fixture JSON file into a list of entries.

    Args:
        path: Path to the fixture file.
        missing_ok: When True (the default — matches the four
            ``build_*_fixture`` commands), a missing file returns an empty
            list instead of raising, since a first-ever build has nothing
            to diff against. Pass False (matches ``refresh_eaws_fixtures``,
            which only recomputes geometry on an already-committed
            fixture) to let a missing file raise instead.

    Returns:
        The list of fixture entry dicts.

    Raises:
        FileNotFoundError: When ``missing_ok`` is False and the file is
            absent.

    """
    if missing_ok and not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def write_fixture(path: Path, data: list[dict[str, Any]]) -> None:
    """Write a Django fixture file in the project's canonical format."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d entries)", path, len(data))


# Fields a rebuild must carry across from the committed fixture rather than
# recompute. Each is per-estate data that lives on a fixture-managed row but
# is NOT derivable from the upstream source the builders read.
#
# ``centroid_elevation_m`` is the one that matters (SNOW-771): it costs an
# Open-Meteo call per region, and a rebuild that dropped it would leave 461
# regions with no height and nothing to say so — the same silent-loss shape
# as the deploy-time wipe that ticket fixed, one layer up. Unlike
# ``basemap_download``, which ``compute_basemap_download`` recomputes on
# every deploy, nothing restores this one offline.
PRESERVED_L4_FIELDS = ("centroid_elevation_m",)


def carry_forward_preserved_fields(
    path: Path, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Copy PRESERVED_L4_FIELDS from the committed fixture onto fresh entries.

    The ``build_*_fixture`` commands rebuild each micro-region's fields from
    the upstream EAWS GeoJSON, so any field that is not in that source is
    absent from the new dict and would be lost on write. This restores them,
    matched on ``region_id``.

    Args:
        path: The existing fixture file; missing is fine (first build).
        entries: The freshly built entries, mutated in place.

    Returns:
        ``entries``, for chaining.

    """
    existing = load_fixture(path, missing_ok=True)
    by_region: dict[str, dict[str, Any]] = {
        e["fields"]["region_id"]: e["fields"]
        for e in existing
        if e.get("model") == "regions.microregion"
        and "region_id" in e.get("fields", {})
    }
    for entry in entries:
        if entry.get("model") != "regions.microregion":
            continue
        old = by_region.get(entry["fields"].get("region_id"))
        if not old:
            continue
        for name in PRESERVED_L4_FIELDS:
            if name not in entry["fields"] and old.get(name) is not None:
                entry["fields"][name] = old[name]
    return entries


def diff_against_existing(path: Path, new_data: list[dict[str, Any]]) -> int:
    """Return the number of entries that differ from the on-disk fixture.

    Compares the serialised JSON string of each entry after round-tripping
    both sides through ``json.dumps`` so normalisation is identical.

    Args:
        path: Path to the existing fixture file.
        new_data: Newly generated fixture entry list.

    Returns:
        Count of changed / added / removed entries.

    """
    existing = load_fixture(path)
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False)
    old_str = json.dumps(existing, indent=2, ensure_ascii=False)
    if new_str == old_str:
        return 0
    new_by_key = {entry_key(e): json.dumps(e, sort_keys=True) for e in new_data}
    old_by_key = {entry_key(e): json.dumps(e, sort_keys=True) for e in existing}
    all_keys = set(new_by_key) | set(old_by_key)
    return sum(1 for k in all_keys if new_by_key.get(k) != old_by_key.get(k))


def entry_key(entry: dict[str, Any]) -> str:
    """Return a stable string key for a fixture entry (model + natural PK field)."""
    model: str = entry["model"]
    fields: dict[str, Any] = entry["fields"]
    if model == "regions.majorregion":
        return f"{model}:{fields['prefix']}"
    if model == "regions.subregion":
        return f"{model}:{fields['prefix']}"
    if model == "regions.microregion":
        return f"{model}:{fields['region_id']}"
    return f"{model}:{json.dumps(fields, sort_keys=True)}"


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
