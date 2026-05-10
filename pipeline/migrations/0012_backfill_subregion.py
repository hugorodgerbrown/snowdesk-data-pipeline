"""
0012_backfill_subregion — Load EAWS L1/L2 seed data and back-fill Region.subregion.

Data migration only. Reads the L1/L2 entries from ``regions/fixtures/eaws.json``
directly (parsing JSON rather than using loaddata so that a single combined
fixture file can serve both this historical migration and the post-SNOW-140
``regions`` app). Sets ``subregion_id`` on every existing ``Region`` row by
mapping ``region_id[:5]`` to the matching ``EawsSubRegion`` primary key.

Follow-up migration 0013 tightens ``Region.subregion`` to non-null with
``on_delete=PROTECT`` once every row is populated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from django.db import migrations

logger = logging.getLogger(__name__)

# The EAWS combined fixture. Resolve from the repo root so the path survives
# after the fixture was consolidated from the old separate files in SNOW-142.
_EAWS_FIXTURE = (
    Path(__file__).resolve().parent.parent.parent / "regions" / "fixtures" / "eaws.json"
)


def load_eaws_parent_fixtures_and_backfill(apps: Any, schema_editor: Any) -> None:
    """Load L1/L2 seed rows directly from eaws.json, then link every Region."""
    EawsMajorRegion = apps.get_model("pipeline", "EawsMajorRegion")  # noqa: N806
    EawsSubRegion = apps.get_model("pipeline", "EawsSubRegion")  # noqa: N806

    fixture = json.loads(_EAWS_FIXTURE.read_text(encoding="utf-8"))

    # Seed MajorRegion (L1) rows.
    major_entries = [e for e in fixture if e["model"] == "regions.majorregion"]
    for entry in major_entries:
        f = entry["fields"]
        EawsMajorRegion.objects.get_or_create(
            prefix=f["prefix"],
            defaults={
                "country": f.get("country", "CH"),
                "name_native": f["name_native"],
                "name_en": f["name_en"],
            },
        )

    # Seed SubRegion (L2) rows.
    sub_entries = [e for e in fixture if e["model"] == "regions.subregion"]
    for entry in sub_entries:
        f = entry["fields"]
        major_prefix = f["major"][0] if isinstance(f["major"], list) else f["major"]
        try:
            major = EawsMajorRegion.objects.get(prefix=major_prefix)
        except EawsMajorRegion.DoesNotExist:
            logger.warning(
                "0012: MajorRegion %s not found, skipping sub %s",
                major_prefix,
                f["prefix"],
            )
            continue
        EawsSubRegion.objects.get_or_create(
            prefix=f["prefix"],
            defaults={
                "major_id": major.pk,
                "name_native": f["name_native"],
                "name_en": f["name_en"],
            },
        )

    Region = apps.get_model("pipeline", "Region")  # noqa: N806

    sub_by_prefix = {sr.prefix: sr.pk for sr in EawsSubRegion.objects.all()}

    updated = 0
    missing: list[str] = []
    for region in Region.objects.all():
        key = region.region_id[:5]
        sub_pk = sub_by_prefix.get(key)
        if sub_pk is None:
            missing.append(region.region_id)
            continue
        if region.subregion_id != sub_pk:
            region.subregion_id = sub_pk
            region.save(update_fields=["subregion", "updated_at"])
            updated += 1

    if missing:
        raise RuntimeError(
            "0012_backfill_subregion: no matching EawsSubRegion found for "
            f"{len(missing)} Region rows (prefixes not in fixture): "
            f"{sorted(set(r[:5] for r in missing))}. "
            "Add the missing L2 entries to regions/fixtures/eaws.json and rerun."
        )

    logger.info(
        "0012_backfill_subregion: back-filled subregion on %d Region rows", updated
    )


def noop_reverse(apps: Any, schema_editor: Any) -> None:
    """Irreversible data — leave subregion FK values intact on reverse."""


class Migration(migrations.Migration):
    """Apply the EAWS L1/L2 seed + Region.subregion back-fill."""

    dependencies = [
        ("pipeline", "0011_eaws_region_hierarchy"),
    ]

    operations = [
        migrations.RunPython(
            load_eaws_parent_fixtures_and_backfill,
            reverse_code=noop_reverse,
        ),
    ]
