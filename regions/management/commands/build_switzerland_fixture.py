"""build_switzerland_fixture — build regions/fixtures/eaws_CH.json.

Combines two source files to produce a Django fixture with 9 L1
``MajorRegion``, 21 L2 ``SubRegion``, and 149 L4 ``MicroRegion``
entries for Switzerland:

    reference_data/eaws/micro-regions/CH_micro-regions.geojson
        — EAWS L4 IDs + geometry
    reference_data/eaws/names/de.json
        — EAWS canonical German names

L4 names are resolved via ``regions.names.lookup(region_id, "de")``,
falling back to the region ID string if ``lookup`` returns ``None``.

L1 / L2 hierarchy derived from region ID prefixes:
    L2 prefix = region_id[:5]  (e.g. 'CH-1111' → 'CH-11')
    L1 prefix = region_id[:4]  (e.g. 'CH-1111' → 'CH-1')

L1 and L2 ``name_native`` / ``name_en`` values are **not** published by
EAWS for CH L1/L2 prefixes (``CH-1`` … ``CH-9``, ``CH-11`` … ``CH-93``
do not appear in any EAWS names JSON). These names are hand-maintained in
the existing on-disk fixture and are carried through unchanged at build
time by reading ``eaws_CH.json`` before writing it.

Geographic neighbours (L4 regions whose polygons share a border) are
computed using a Shapely buffer-intersects approach — identical to the
approach previously in ``scripts/build_regions_fixture.py``.

L1 and L2 centre / bbox / boundary are derived from their L4 children
using the shared helpers in ``regions.fixture_utils``.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a summary and exits 0 without writing anything.

Usage:
    # Preview only (default — no writes).
    poetry run python manage.py build_switzerland_fixture

    # Write regions/fixtures/eaws_CH.json.
    poetry run python manage.py build_switzerland_fixture --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from itertools import combinations
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from regions.fixture_utils import (
    bbox_from_children,
    boundary_from_children,
    centre_from_bbox,
    centre_from_children,
)
from regions.names import lookup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source / output paths (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_EAWS_GEOJSON = (
    _REPO_ROOT
    / "reference_data"
    / "eaws"
    / "micro-regions"
    / "CH_micro-regions.geojson"
)
_CH_FIXTURE = _REPO_ROOT / "regions" / "fixtures" / "eaws_CH.json"

_FIXTURE_TIMESTAMP = "2026-01-01T00:00:00Z"

# ~10 m at Swiss latitudes — absorbs the sub-metre float gaps that show up
# between cantonal polygons where the same boundary line was re-digitised
# from two sides.
_NEIGHBOUR_EPS_DEGREES = 1e-4


class Command(BaseCommand):
    """Build regions/fixtures/eaws_CH.json from EAWS micro-region source files.

    Read-only by default; pass ``--commit`` to write the fixture.
    """

    help = (
        "Build regions/fixtures/eaws_CH.json from the vendored EAWS Switzerland "
        "micro-region GeoJSON and EAWS German names. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write the generated fixture to disk. Without this flag the "
                "command only reports what would be written and exits 0."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the fixture build."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        existing_fixture = _load_fixture(_CH_FIXTURE)
        existing_l1_names = _extract_l1_names(existing_fixture)
        existing_l2_names = _extract_l2_names(existing_fixture)

        eaws_features = _load_geojson(_EAWS_GEOJSON)
        entries = _build_entries(eaws_features, existing_l1_names, existing_l2_names)

        l1_count = sum(1 for e in entries if e["model"] == "regions.majorregion")
        l2_count = sum(1 for e in entries if e["model"] == "regions.subregion")
        l4_count = sum(1 for e in entries if e["model"] == "regions.microregion")

        if verbosity >= 1:
            self.stdout.write(f"Built: L1={l1_count} L2={l2_count} L4={l4_count}")

        changes = _diff_against_existing(_CH_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(f"Change(s) vs existing fixture: {changes}")

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixture.")
                )
            return

        _write_fixture(_CH_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(f"Wrote {_CH_FIXTURE} ({len(entries)} entries).")
            )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_geojson(path: Path) -> list[dict[str, Any]]:
    """Load a GeoJSON FeatureCollection and return its features list."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]  # type: ignore[no-any-return]


def _extract_l1_names(
    fixture: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Extract ``{prefix: (name_native, name_en)}`` from L1 MajorRegion entries.

    Args:
        fixture: Django fixture entry list.

    Returns:
        Mapping from L1 prefix (e.g. ``'CH-1'``) to ``(name_native, name_en)``.

    """
    return {
        e["fields"]["prefix"]: (
            e["fields"]["name_native"],
            e["fields"]["name_en"],
        )
        for e in fixture
        if e["model"] == "regions.majorregion"
    }


def _extract_l2_names(
    fixture: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Extract ``{prefix: (name_native, name_en)}`` from L2 SubRegion entries.

    Args:
        fixture: Django fixture entry list.

    Returns:
        Mapping from L2 prefix (e.g. ``'CH-11'``) to ``(name_native, name_en)``.

    """
    return {
        e["fields"]["prefix"]: (
            e["fields"]["name_native"],
            e["fields"]["name_en"],
        )
        for e in fixture
        if e["model"] == "regions.subregion"
    }


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------


def _build_entries(
    eaws_features: list[dict[str, Any]],
    existing_l1_names: dict[str, tuple[str, str]],
    existing_l2_names: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Build the full fixture entry list (L1 + L2 + L4) for Switzerland.

    Args:
        eaws_features: GeoJSON features from ``CH_micro-regions.geojson``.
        existing_l1_names: Hand-maintained L1 ``{prefix: (name_native, name_en)}``.
        existing_l2_names: Hand-maintained L2 ``{prefix: (name_native, name_en)}``.

    Returns:
        Ordered list of Django fixture entry dicts (L1s first, then L2s, then L4s).

    """
    # --- L4 micro-regions and neighbour graph ---------------------------------
    l4_fields_list: list[dict[str, Any]] = []
    boundaries: list[tuple[str, dict[str, Any]]] = []

    for feature in eaws_features:
        region_id: str = feature["properties"]["id"]
        geometry: dict[str, Any] = feature["geometry"]
        centre = centre_from_bbox(geometry)
        l4_name = lookup(region_id, "de") or region_id

        l4_field: dict[str, Any] = {
            "region_id": region_id,
            "name": l4_name,
            "slug": slugify(region_id),
            "subregion": [region_id[:5]],
            "centre": centre,
            "boundary": geometry,
            "neighbours": [],
            "created_at": _FIXTURE_TIMESTAMP,
            "updated_at": _FIXTURE_TIMESTAMP,
        }
        l4_fields_list.append(l4_field)
        boundaries.append((region_id, geometry))

    neighbour_map = _compute_neighbour_graph(boundaries)
    for l4_field in l4_fields_list:
        region_id = l4_field["region_id"]
        l4_field["neighbours"] = [[nid] for nid in neighbour_map[region_id]]

    l4_entries: list[dict[str, Any]] = [
        {"model": "regions.microregion", "fields": f} for f in l4_fields_list
    ]
    l4_entries.sort(key=lambda e: e["fields"]["region_id"])

    # --- L2 sub-regions -------------------------------------------------------
    l2_children: dict[str, list[dict[str, Any]]] = {}
    for l4_field in l4_fields_list:
        l2_prefix = l4_field["region_id"][:5]
        l2_children.setdefault(l2_prefix, []).append(l4_field)

    l2_entries: list[dict[str, Any]] = []
    for l2_prefix in sorted(l2_children):
        children = l2_children[l2_prefix]
        l1_prefix = l2_prefix[:4]
        centre = centre_from_children(children)
        bbox = bbox_from_children(children)
        boundary = boundary_from_children(children)
        name_native, name_en = existing_l2_names.get(l2_prefix, (l2_prefix, l2_prefix))
        l2_entries.append(
            {
                "model": "regions.subregion",
                "fields": {
                    "prefix": l2_prefix,
                    "major": [l1_prefix],
                    "name_native": name_native,
                    "name_en": name_en,
                    "centre": centre,
                    "bbox": bbox,
                    "boundary": boundary,
                    "created_at": _FIXTURE_TIMESTAMP,
                    "updated_at": _FIXTURE_TIMESTAMP,
                },
            }
        )

    # --- L1 major regions -----------------------------------------------------
    l1_children: dict[str, list[dict[str, Any]]] = {}
    for l4_field in l4_fields_list:
        l1_prefix = l4_field["region_id"][:4]
        l1_children.setdefault(l1_prefix, []).append(l4_field)

    l1_entries: list[dict[str, Any]] = []
    for l1_prefix in sorted(l1_children):
        children = l1_children[l1_prefix]
        centre = centre_from_children(children)
        bbox = bbox_from_children(children)
        boundary = boundary_from_children(children)
        name_native, name_en = existing_l1_names.get(l1_prefix, (l1_prefix, l1_prefix))
        l1_entries.append(
            {
                "model": "regions.majorregion",
                "fields": {
                    "prefix": l1_prefix,
                    "country": "CH",
                    "name_native": name_native,
                    "name_en": name_en,
                    "centre": centre,
                    "bbox": bbox,
                    "boundary": boundary,
                    "created_at": _FIXTURE_TIMESTAMP,
                    "updated_at": _FIXTURE_TIMESTAMP,
                },
            }
        )

    return l1_entries + l2_entries + l4_entries


# ---------------------------------------------------------------------------
# Neighbour graph
# ---------------------------------------------------------------------------


def _compute_neighbour_graph(
    boundaries: list[tuple[str, dict[str, Any]]],
    eps: float = _NEIGHBOUR_EPS_DEGREES,
) -> dict[str, list[str]]:
    """Return a region_id → sorted list of neighbour region_ids mapping.

    Two regions are considered neighbours when one polygon, expanded by
    ``eps`` degrees, intersects the other. The buffer absorbs the small
    float gaps that appear between independently-digitised cantonal
    boundaries, where strict ``touches()`` would falsely report a near-
    miss. The resulting graph is symmetric — every adjacency is recorded
    on both endpoints.

    Args:
        boundaries: Pairs of (region_id, GeoJSON geometry dict).
        eps: Buffer distance in degrees applied before the intersect test.

    Returns:
        ``{region_id: [neighbour_id, …]}`` with neighbour lists sorted
        alphabetically. Regions with no neighbours are present with an
        empty list.

    """
    from shapely.geometry import shape

    polygons: dict[str, Any] = {rid: shape(b) for rid, b in boundaries}
    neighbours: dict[str, set[str]] = {rid: set() for rid in polygons}

    for (rid_a, poly_a), (rid_b, poly_b) in combinations(polygons.items(), 2):
        if poly_a.buffer(eps).intersects(poly_b):
            neighbours[rid_a].add(rid_b)
            neighbours[rid_b].add(rid_a)

    return {rid: sorted(ns) for rid, ns in neighbours.items()}


# ---------------------------------------------------------------------------
# Fixture I/O
# ---------------------------------------------------------------------------


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    """Read an existing Django fixture file; return an empty list if absent."""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _write_fixture(path: Path, data: list[dict[str, Any]]) -> None:
    """Write a Django fixture file in the project's canonical format."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d entries)", path, len(data))


def _diff_against_existing(path: Path, new_data: list[dict[str, Any]]) -> int:
    """Return the number of entries that differ from the on-disk fixture.

    Compares the serialised JSON string of each entry after round-tripping
    both sides through ``json.dumps`` so normalisation is identical.

    Args:
        path: Path to the existing fixture file.
        new_data: Newly generated fixture entry list.

    Returns:
        Count of changed / added / removed entries.

    """
    existing = _load_fixture(path)
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False)
    old_str = json.dumps(existing, indent=2, ensure_ascii=False)
    if new_str == old_str:
        return 0
    new_by_key = {_entry_key(e): json.dumps(e, sort_keys=True) for e in new_data}
    old_by_key = {_entry_key(e): json.dumps(e, sort_keys=True) for e in existing}
    all_keys = set(new_by_key) | set(old_by_key)
    return sum(1 for k in all_keys if new_by_key.get(k) != old_by_key.get(k))


def _entry_key(entry: dict[str, Any]) -> str:
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
