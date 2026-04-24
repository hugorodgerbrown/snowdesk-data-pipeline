"""
0012_backfill_subregion — Load EAWS L1/L2 fixtures and back-fill Region.subregion.

Data migration only. Loads the hand-authored ``eaws_major_regions.json``
and ``eaws_sub_regions.json`` fixtures, then sets ``subregion_id`` on
every existing ``Region`` row by mapping ``region_id[:5]`` to the matching
``EawsSubRegion`` primary key.

Running ``loaddata`` inside a migration is the idiomatic Django pattern
for seed data that the schema depends on (see the Django docs on data
migrations). It keeps fresh databases, test databases, and existing
production databases consistent without ordering assumptions at the
callsite.

Follow-up migration 0013 tightens ``Region.subregion`` to non-null with
``on_delete=PROTECT`` once every row is populated.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management import call_command
from django.db import migrations

logger = logging.getLogger(__name__)


def load_eaws_parent_fixtures_and_backfill(apps: Any, schema_editor: Any) -> None:
    """Load L1/L2 fixtures, then link every Region to its subregion."""
    call_command(
        "loaddata",
        "eaws_major_regions",
        "eaws_sub_regions",
        verbosity=0,
    )

    EawsSubRegion = apps.get_model("pipeline", "EawsSubRegion")  # noqa: N806
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
            "Add the missing L2 entries to eaws_sub_regions.json and rerun."
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
