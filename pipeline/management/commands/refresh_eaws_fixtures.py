"""refresh_eaws_fixtures — derive L1/L2 geometry from L4 children.

Regenerates the ``centre``, ``bbox`` and ``boundary`` fields on the
L1/L2 EAWS fixtures from the union of their L4 children stored in
``pipeline/fixtures/regions.json``.

The L4 (``regions.json``) data is the authoritative geographic source —
one polygon per SLF warning region. L1/L2 don't have independently
published geometry; their centre, bounding box and outer boundary are
derived from their descendants. Pre-computing avoids runtime geometry
math (per the project's pre-compute-over-runtime preference).

Boundary computation uses ``shapely.ops.unary_union`` to merge L4
polygons into a single Polygon (or MultiPolygon if disjoint). Shapely
is a **dev-only** dependency — fixtures are always rebuilt locally and
committed, so the runtime never imports it. The import lives inside
the helper that needs it; running this command in an environment that
lacks shapely raises a friendly RuntimeError pointing at
``poetry install --with dev``.

This command does NOT:
  * Fetch from ``regions.avalanches.org`` — the authoritative dataset is
    already snapshotted under ``docs/`` and materialised in
    ``pipeline/fixtures/regions.json``. Refreshing the L4 snapshot is a
    separate, manual step handled by ``scripts/build_regions_fixture.py``.
  * Edit the L1/L2 ``name_native`` / ``name_en`` labels — those are
    hand-maintained and outside this command's remit.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a diff summary and exits 0 without writing anything.

Usage:
    # Preview what would change (default — no writes).
    poetry run python manage.py refresh_eaws_fixtures

    # Actually write the updated L1/L2 fixtures.
    poetry run python manage.py refresh_eaws_fixtures --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from collections import defaultdict
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_FIXTURES_DIR = Path("pipeline/fixtures")
_REGIONS_FIXTURE = _FIXTURES_DIR / "regions.json"
_MAJOR_FIXTURE = _FIXTURES_DIR / "eaws_major_regions.json"
_SUB_FIXTURE = _FIXTURES_DIR / "eaws_sub_regions.json"


class Command(BaseCommand):
    """Recompute L1/L2 centre + bbox from L4 unions. Read-only unless --commit."""

    help = (
        "Recompute derived centre + bbox on the L1/L2 EAWS fixtures from the "
        "union of their L4 children. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the recomputed fixtures to disk. Without this flag "
            "the command only reports what would change and exits 0.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the refresh."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        regions = _load_fixture(_REGIONS_FIXTURE)
        majors = _load_fixture(_MAJOR_FIXTURE)
        subs = _load_fixture(_SUB_FIXTURE)

        l4_by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
        l4_by_major: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in regions:
            fields = entry["fields"]
            rid: str = fields["region_id"]
            l4_by_sub[rid[:5]].append(fields)
            l4_by_major[rid[:4]].append(fields)

        sub_changes = _update_geometry_inplace(subs, l4_by_sub, key="prefix")
        major_changes = _update_geometry_inplace(majors, l4_by_major, key="prefix")

        if verbosity >= 1:
            self.stdout.write(
                f"L1 major: {major_changes} change(s), L2 sub: {sub_changes} change(s)."
            )

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixtures.")
                )
            return

        if major_changes:
            _write_fixture(_MAJOR_FIXTURE, majors)
        if sub_changes:
            _write_fixture(_SUB_FIXTURE, subs)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS("Fixtures refreshed. Run tox to verify.")
            )


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    """Read a Django fixture JSON file into a list of entries."""
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _write_fixture(path: Path, data: list[dict[str, Any]]) -> None:
    """Write a Django fixture JSON file, preserving the project's format."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("Wrote %s (%d entries)", path, len(data))


def _update_geometry_inplace(
    entries: list[dict[str, Any]],
    children_by_prefix: dict[str, list[dict[str, Any]]],
    *,
    key: str,
) -> int:
    """Recompute centre + bbox + boundary on each entry; return change count."""
    changes = 0
    for entry in entries:
        fields = entry["fields"]
        prefix = fields[key]
        children = children_by_prefix.get(prefix, [])
        if not children:
            logger.warning(
                "refresh_eaws_fixtures: no L4 children found for %s — skipping",
                prefix,
            )
            continue
        centre = _centre_from_children(children)
        bbox = _bbox_from_children(children)
        boundary = _boundary_from_children(children)
        if (
            fields.get("centre") != centre
            or fields.get("bbox") != bbox
            or fields.get("boundary") != boundary
        ):
            fields["centre"] = centre
            fields["bbox"] = bbox
            fields["boundary"] = boundary
            changes += 1
    return changes


def _centre_from_children(children: list[dict[str, Any]]) -> dict[str, float]:
    """Return an area-weighted-ish centre — just the arithmetic mean of L4 centres.

    This is a cheap approximation (not the true polygon-union centroid).
    It's good enough for zoom-to-region behaviour; for precise rendering,
    use bbox or the future shapely-derived union centroid.
    """
    lons = [child["centre"]["lon"] for child in children if child.get("centre")]
    lats = [child["centre"]["lat"] for child in children if child.get("centre")]
    return {"lon": sum(lons) / len(lons), "lat": sum(lats) / len(lats)}


def _iter_coords(children: list[dict[str, Any]]) -> Any:
    """Yield every (lon, lat) tuple from the children's polygon rings."""
    for child in children:
        boundary = child.get("boundary")
        if not boundary:
            continue
        for ring in boundary["coordinates"]:
            yield from ring


def _bbox_from_children(children: list[dict[str, Any]]) -> list[float]:
    """Return [min_lon, min_lat, max_lon, max_lat] over the union of L4 bboxes."""
    lons, lats = zip(*_iter_coords(children), strict=False)
    return [min(lons), min(lats), max(lons), max(lats)]


def _boundary_from_children(children: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge L4 child polygons into a single GeoJSON Polygon/MultiPolygon.

    Imports ``shapely`` lazily so the runtime (which never calls this
    helper) doesn't need the package installed. If shapely is missing,
    raises a RuntimeError pointing at the dev install command.

    Returns a plain ``dict`` (GeoJSON shape) ready to be written to the
    fixture's ``boundary`` field.
    """
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover — dev-only dependency
        raise RuntimeError(
            "refresh_eaws_fixtures requires the dev-only `shapely` "
            "dependency. Install it with `poetry install --with dev`."
        ) from exc

    polys = [shape(child["boundary"]) for child in children if child.get("boundary")]
    union = unary_union(polys)
    # ``shapely.geometry.mapping`` emits coordinate tuples (``(lon, lat)``).
    # Round-tripping through json normalises them to lists, so the
    # idempotence diff check in ``_update_geometry_inplace`` compares
    # like-for-like against the previously-written (lists-of-lists)
    # fixture and a second --commit reports "0 change(s)".
    return json.loads(json.dumps(mapping(union)))  # type: ignore[no-any-return]
