"""
0019_close_region_boundary_rings — Close open polygon rings on Region.boundary.

Data migration only. Walks every ``Region`` row and, for any
``boundary`` ring whose first vertex differs from its last, appends a
copy of the first vertex so the ring satisfies RFC 7946 §3.1.6.

Background: ``Region.boundary`` is a ``JSONField`` and is stored
verbatim. SNOW-33 (commit ``c7de7bd``) closed the rings in the source
CSV (``docs/eaws_regions_ch.csv``) and rebuilt
``pipeline/fixtures/regions.json``, but the closing helper at
``scripts/build_regions_fixture.py:_close_polygon_rings`` runs at
fixture build time, not at ingest or save time. A production database
seeded from a pre-SNOW-33 fixture therefore still carries the open
rings; ``loaddata regions`` is not safe to re-run on an already-seeded
database (see the preamble of 0016 for the unique-constraint trap).

This migration is the in-place equivalent: read every Region row,
close any open ring, and ``save(update_fields=["boundary"])``.
Idempotent — already-closed rings are skipped, so re-running on a
clean database is a no-op. Only ``boundary`` is touched, so any
operator-side drift on other fields (``name``, ``slug``, ``centre``,
…) is preserved.

Reverse is a no-op: re-opening rings would re-introduce the SNOW-105
visual gap and violate the regression test added by SNOW-33
(``tests/pipeline/models/test_region.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import migrations

logger = logging.getLogger(__name__)


def _close_rings(boundary: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a closed copy of ``boundary`` and whether any ring was changed.

    Handles both ``Polygon`` and ``MultiPolygon`` GeoJSON types — the
    model's help text permits MultiPolygon even though current data is
    all Polygon. Non-geometry input is returned unchanged.
    """
    geom_type = boundary.get("type")
    if geom_type == "Polygon":
        rings: list[list[list[float]]] = boundary.get("coordinates", [])
        new_rings, changed = _close_ring_list(rings)
        if not changed:
            return boundary, False
        return {**boundary, "coordinates": new_rings}, True
    if geom_type == "MultiPolygon":
        polys: list[list[list[list[float]]]] = boundary.get("coordinates", [])
        new_polys: list[list[list[list[float]]]] = []
        any_changed = False
        for poly in polys:
            new_rings, changed = _close_ring_list(poly)
            any_changed = any_changed or changed
            new_polys.append(new_rings)
        if not any_changed:
            return boundary, False
        return {**boundary, "coordinates": new_polys}, True
    return boundary, False


def _close_ring_list(
    rings: list[list[list[float]]],
) -> tuple[list[list[list[float]]], bool]:
    """Close every open ring in ``rings``; return new list + change flag."""
    out: list[list[list[float]]] = []
    changed = False
    for ring in rings:
        if isinstance(ring, list) and len(ring) >= 2 and ring[0] != ring[-1]:
            out.append([*ring, ring[0]])
            changed = True
        else:
            out.append(ring)
    return out, changed


def close_open_rings(apps: Any, schema_editor: Any) -> None:
    """Close any open ``boundary`` rings on existing Region rows."""
    Region = apps.get_model("pipeline", "Region")  # noqa: N806

    closed = 0
    skipped = 0
    for region in Region.objects.exclude(boundary__isnull=True).iterator():
        boundary = region.boundary
        if not isinstance(boundary, dict):
            skipped += 1
            continue
        new_boundary, changed = _close_rings(boundary)
        if not changed:
            skipped += 1
            continue
        region.boundary = new_boundary
        region.save(update_fields=["boundary", "updated_at"])
        closed += 1

    logger.info(
        "0019_close_region_boundary_rings: closed %d Region.boundary rows "
        "(%d already closed or non-polygon)",
        closed,
        skipped,
    )


def noop_reverse(apps: Any, schema_editor: Any) -> None:
    """Reverse is intentionally a no-op — see module docstring."""
    return


class Migration(migrations.Migration):
    """Close open polygon rings on Region.boundary in place."""

    dependencies = [
        ("pipeline", "0018_seed_edit_map_flag"),
    ]

    operations = [
        migrations.RunPython(close_open_rings, reverse_code=noop_reverse),
    ]
