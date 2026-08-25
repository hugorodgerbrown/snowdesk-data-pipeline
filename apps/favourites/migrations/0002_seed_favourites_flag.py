"""
0002_seed_favourites_flag — retained no-op.

Originally a data migration seeding a ``waffle.Flag`` row so that SNOW-413's
saved-map-pin favourites feature reached superusers on first deploy.
SNOW-502 replaced that pattern with a declarative manifest —
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


def seed_favourites_flag(apps: Any, schema_editor: Any) -> None:
    """No-op. Flags come from apps/core/fixtures/waffle_flags.json."""


def remove_favourites_flag(apps: Any, schema_editor: Any) -> None:
    """No-op. Flags come from apps/core/fixtures/waffle_flags.json."""


class Migration(migrations.Migration):
    """Retained no-op node — the ``favourites`` Flag lived here once."""

    dependencies = [
        ("favourites", "0001_initial"),
        # Retained from the original seeding migration: the waffle pin
        # is part of this node's recorded history and removing it would
        # rewrite the graph these no-ops exist to preserve.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_favourites_flag,
            reverse_code=remove_favourites_flag,
        ),
    ]
