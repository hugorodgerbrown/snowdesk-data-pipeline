"""
0016_backfill_region_neighbours — Populate Region.neighbours from regions.json.

Data migration only. Reads the ``neighbours`` natural-key list emitted on
each entry of ``regions/fixtures/regions.json`` (post-SNOW-140; was at
``pipeline/fixtures/regions.json`` when this migration was first written;
see ``scripts/build_regions_fixture.py``) and writes the symmetric M2M
for every existing Region row.

The preceding schema migration (0015) adds the M2M field but leaves the
through table empty. On a fresh database, ``loaddata regions`` covers
neighbours via Django's natural-key M2M serialisation; on an already-
seeded production database, ``loaddata`` would fail on the
``region_id`` unique constraint, so this migration takes the same data
out of the fixture and applies it in-place via ``set()``.

Reversal clears the through table — the field itself is reverted by
unwinding 0015.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from django.db import migrations

logger = logging.getLogger(__name__)

# Originally ``pipeline/fixtures/regions.json``; the fixture moved to
# ``regions/fixtures/regions.json`` in SNOW-140. Resolve relative to the
# repo root (the migration file's grand-grand-parent) so the historical
# data migration keeps finding the fixture after the app rename.
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "regions"
    / "fixtures"
    / "regions.json"
)


def backfill_neighbours(apps: Any, schema_editor: Any) -> None:
    """Read regions.json and write Region.neighbours for every existing row."""
    # SNOW-140: Region moved from pipeline to regions. Try the new
    # location first; fall back to the historical location so the
    # migration still works at its historical replay position.
    try:
        Region = apps.get_model("regions", "Region")  # noqa: N806
    except LookupError:
        Region = apps.get_model("pipeline", "Region")  # noqa: N806

    if not FIXTURE_PATH.exists():
        logger.warning(
            "0016_backfill_region_neighbours: fixture not found at %s — "
            "skipping (deploy will need a manual neighbour seed)",
            FIXTURE_PATH,
        )
        return

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    region_ids = {entry["fields"]["region_id"] for entry in fixture}
    pk_by_id = dict(
        Region.objects.filter(region_id__in=region_ids).values_list("region_id", "pk")
    )

    applied = 0
    skipped: list[str] = []
    for entry in fixture:
        fields = entry["fields"]
        region_id = fields["region_id"]
        if region_id not in pk_by_id:
            skipped.append(region_id)
            continue
        neighbour_ids = [n[0] for n in fields.get("neighbours", [])]
        neighbour_pks = [pk_by_id[nid] for nid in neighbour_ids if nid in pk_by_id]
        Region.objects.get(pk=pk_by_id[region_id]).neighbours.set(neighbour_pks)
        applied += 1

    logger.info(
        "0016_backfill_region_neighbours: applied to %d regions (skipped %d not in DB)",
        applied,
        len(skipped),
    )


def clear_neighbours(apps: Any, schema_editor: Any) -> None:
    """Reverse: detach all neighbour links so 0015 can drop the field cleanly."""
    try:
        Region = apps.get_model("regions", "Region")  # noqa: N806
    except LookupError:
        Region = apps.get_model("pipeline", "Region")  # noqa: N806
    for region in Region.objects.all():
        region.neighbours.clear()


class Migration(migrations.Migration):
    """Backfill the Region.neighbours through table from the fixture."""

    dependencies = [
        ("pipeline", "0015_region_neighbours"),
    ]

    operations = [
        migrations.RunPython(backfill_neighbours, reverse_code=clear_neighbours),
    ]
