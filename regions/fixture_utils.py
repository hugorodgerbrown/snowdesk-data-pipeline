"""regions/fixture_utils.py — Shared geometry helpers for fixture-build commands.

Provides the geometry utility functions used by ``build_france_fixture``,
``build_austria_fixture``, ``build_italy_fixture``, and
``build_switzerland_fixture`` when computing parent-region geometry from
child (L4 MicroRegion) geometry.

All functions operate on GeoJSON geometry dicts and on lists of field-dicts
(the ``fields`` portion of a Django fixture entry).

Functions:
    centre_from_children — arithmetic mean of child centre points.
    bbox_from_children   — bounding box over all child boundaries.
    boundary_from_children — Shapely-derived union of child polygons.
    centre_from_bbox     — bbox midpoint of a single GeoJSON geometry.
    _iter_coords_from_geometry — flat list of (lon, lat) pairs from a geometry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
