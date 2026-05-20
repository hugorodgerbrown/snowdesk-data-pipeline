"""regions/fixture_utils.py — Shared geometry helpers for fixture-build commands.

Provides the geometry utility functions used by ``build_france_fixture``,
``build_austria_fixture``, ``build_italy_fixture``, and
``build_switzerland_fixture`` when computing parent-region geometry from
child (L4 MicroRegion) geometry.

All functions operate on GeoJSON geometry dicts and on lists of field-dicts
(the ``fields`` portion of a Django fixture entry).

Functions:
    build_entries_from_eaws_dir — shared entry-list builder for multi-file
        EAWS fixtures (Austria, Italy).
    centre_from_children — arithmetic mean of child centre points.
    bbox_from_children   — bounding box over all child boundaries.
    boundary_from_children — Shapely-derived union of child polygons.
    centre_from_bbox     — bbox midpoint of a single GeoJSON geometry.
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
