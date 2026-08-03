"""refresh_eaws_fixtures — derive L1/L2 geometry from L4 children.

Regenerates the ``centre``, ``bbox`` and ``boundary`` fields on the
L1/L2 EAWS entries in ``apps/regions/fixtures/eaws_CH.json`` from the union of
their L4 children in the same file, and (SNOW-583) the ``basemap_download``
field on every L4 entry itself.

The L4 (``regions.microregion``) data is the authoritative geographic
source — one polygon per SLF warning region. L1/L2 don't have
independently published geometry; their centre, bounding box and outer
boundary are derived from their descendants. Pre-computing avoids
runtime geometry math (per the project's pre-compute-over-runtime
preference).

Boundary computation uses ``shapely.ops.unary_union`` to merge L4
polygons into a single Polygon (or MultiPolygon if disjoint). Shapely
is a **dev-only** dependency for this L1/L2 helper — fixtures are always
rebuilt locally and committed, so the runtime never imports it via this
command. The import lives inside the helper that needs it; running this
command in an environment that lacks shapely raises a friendly
RuntimeError pointing at ``uv sync``. (The L4 ``basemap_download`` pass
below uses ``apps.regions.services.basemap_tiles.build_region_blob``,
which imports shapely the same lazy way — but that module's caller,
``compute_basemap_download``, DOES run at runtime, where shapely is an
ordinary top-level project dependency; see that module's docstring.)

This command does NOT:
  * Fetch from ``regions.avalanches.org`` — the authoritative dataset is
    already snapshotted under ``docs/`` and materialised in
    ``apps/regions/fixtures/eaws_CH.json``. Refreshing the L4 snapshot is a
    separate, manual step handled by ``build_switzerland_fixture``.
  * Edit the L1/L2 ``name_native`` / ``name_en`` labels — those are
    hand-maintained and outside this command's remit.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a diff summary and exits 0 without writing anything.

Usage:
    # Preview what would change (default — no writes).
    uv run python manage.py refresh_eaws_fixtures

    # Actually write the updated L1/L2 (+ L4 basemap_download) fixtures.
    uv run python manage.py refresh_eaws_fixtures --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from collections import defaultdict
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from apps.regions.fixture_utils import load_fixture, write_fixture
from apps.regions.services.basemap_tiles import MICRO_BAND, build_region_blob

logger = logging.getLogger(__name__)

_FIXTURES_DIR = Path("apps/regions/fixtures")
_EAWS_FIXTURE = _FIXTURES_DIR / "eaws_CH.json"

_LABEL_MAJOR = "regions.majorregion"
_LABEL_SUB = "regions.subregion"
_LABEL_MICRO = "regions.microregion"


class Command(BaseCommand):
    """Recompute L1/L2 centre + bbox from L4 unions. Read-only unless --commit.

    SNOW-602 exempt: operates on the parsed on-disk fixture JSON in memory,
    not a queryset.
    """

    help = (
        "Recompute derived centre + bbox on the L1/L2 EAWS entries in eaws_CH.json "
        "from the union of their L4 children. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the recomputed fixture to disk. Without this flag "
            "the command only reports what would change and exits 0.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the refresh."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        all_entries = load_fixture(_EAWS_FIXTURE, missing_ok=False)

        # Split by model label.
        regions = [e for e in all_entries if e["model"] == _LABEL_MICRO]
        majors = [e for e in all_entries if e["model"] == _LABEL_MAJOR]
        subs = [e for e in all_entries if e["model"] == _LABEL_SUB]

        l4_by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
        l4_by_major: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in regions:
            fields = entry["fields"]
            rid: str = fields["region_id"]
            l4_by_sub[rid[:5]].append(fields)
            l4_by_major[rid[:4]].append(fields)

        sub_changes = _update_geometry_inplace(subs, l4_by_sub, key="prefix")
        major_changes = _update_geometry_inplace(majors, l4_by_major, key="prefix")
        # SNOW-583: L4's own basemap_download, clipped to its real boundary
        # rather than its bbox — mechanised here rather than a prose
        # "run compute_basemap_download against a scratch DB" recipe, since
        # this command already rewrites centre/bbox/boundary in place on
        # this same file.
        region_changes = _update_basemap_download_inplace(regions)

        if verbosity >= 1:
            self.stdout.write(
                f"L1 major: {major_changes} change(s), L2 sub: {sub_changes} "
                f"change(s), L4 basemap_download: {region_changes} change(s)."
            )

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixtures.")
                )
            return

        if major_changes or sub_changes or region_changes:
            combined = majors + subs + regions
            write_fixture(_EAWS_FIXTURE, combined)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS("Fixtures refreshed. Run tox to verify.")
            )


def _update_basemap_download_inplace(regions: list[dict[str, Any]]) -> int:
    """Recompute ``basemap_download`` on every L4 entry; return change count.

    Mirrors ``compute_basemap_download``'s own per-``MicroRegion`` loop
    (``apps.regions.management.commands.compute_basemap_download``), but
    against the fixture's plain dicts rather than a live queryset — the
    same ``build_region_blob(boundary, *MICRO_BAND)`` call, so the
    committed fixture and a freshly-migrated database agree on every
    region's clipped tile coverage without a scratch-DB round trip.

    Args:
        regions: The ``regions.microregion`` fixture entries.

    Returns:
        The number of entries whose ``basemap_download`` changed.

    """
    min_z, max_z = MICRO_BAND
    changes = 0
    for entry in regions:
        fields = entry["fields"]
        boundary = fields.get("boundary")
        if not boundary:
            logger.warning(
                "refresh_eaws_fixtures: %s has no boundary — skipping basemap_download",
                fields.get("region_id"),
            )
            continue
        blob = build_region_blob(boundary, min_z, max_z)
        if fields.get("basemap_download") != blob:
            fields["basemap_download"] = blob
            changes += 1
    return changes


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
    """Yield every ``[lon, lat]`` position from the children's polygon rings.

    Handles both GeoJSON ``Polygon`` (``coordinates`` is ``[ring][position]``)
    and ``MultiPolygon`` (``coordinates`` is ``[polygon][ring][position]``)
    boundaries — the L4 microregion fixtures are MultiPolygons. Any other
    geometry type raises, rather than silently yielding a malformed shape
    that would surface as an opaque unpacking error downstream.
    """
    for child in children:
        boundary = child.get("boundary")
        if not boundary:
            continue
        geom_type = boundary["type"]
        if geom_type == "Polygon":
            polygons = [boundary["coordinates"]]
        elif geom_type == "MultiPolygon":
            polygons = boundary["coordinates"]
        else:
            raise ValueError(
                f"Unsupported boundary geometry type {geom_type!r}; "
                "expected Polygon or MultiPolygon."
            )
        for polygon in polygons:
            for ring in polygon:
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
            "dependency. Install it with `uv sync`."
        ) from exc

    polys = [shape(child["boundary"]) for child in children if child.get("boundary")]
    union = unary_union(polys)
    # ``shapely.geometry.mapping`` emits coordinate tuples (``(lon, lat)``).
    # Round-tripping through json normalises them to lists, so the
    # idempotence diff check in ``_update_geometry_inplace`` compares
    # like-for-like against the previously-written (lists-of-lists)
    # fixture and a second --commit reports "0 change(s)".
    return json.loads(json.dumps(mapping(union)))  # type: ignore[no-any-return]
