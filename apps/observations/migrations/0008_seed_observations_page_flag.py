"""
0008_seed_observations_page_flag — retained no-op.

Originally a data migration seeding a ``waffle.Flag`` row so that SNOW-476's
/observations/ page reached superusers on first deploy. SNOW-502 replaced
that pattern with a declarative manifest —
``apps/core/fixtures/waffle_flags.json``, reconciled by the
``sync_waffle_flags`` management command on every deploy — which left this
migration creating a row the very next command either kept or deleted.
SNOW-724 emptied the body for good.

The node and its ``dependencies`` are kept so migration history stays valid
on every deployed database; only the work is gone. Do not copy this
migration's original shape when adding a flag — declare it in the manifest
instead (see docs/feature-flags.md).
"""

from __future__ import annotations

from typing import Any

from django.db import migrations


def seed_observations_page_flag(apps: Any, schema_editor: Any) -> None:
    """No-op. Flags come from apps/core/fixtures/waffle_flags.json."""


def remove_observations_page_flag(apps: Any, schema_editor: Any) -> None:
    """No-op. Flags come from apps/core/fixtures/waffle_flags.json."""


class Migration(migrations.Migration):
    """Retained no-op node — the ``observations_page`` Flag lived here once."""

    dependencies = [
        (
            "observations",
            "0007_fieldobservation_fieldobservation_latitude_within_wgs84_and_more",
        ),
        # Retained from the original seeding migration: the waffle pin
        # is part of this node's recorded history and removing it would
        # rewrite the graph these no-ops exist to preserve.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_observations_page_flag,
            reverse_code=remove_observations_page_flag,
        ),
    ]
