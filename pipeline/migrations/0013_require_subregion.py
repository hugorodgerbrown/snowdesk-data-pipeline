"""
0013_require_subregion — Tighten Region.subregion to non-null.

All Region rows were populated with a subregion in 0012, and the fixtures
now include a subregion natural key for every entry. This migration
enforces that invariant at the schema level (NOT NULL + ``PROTECT``).
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Make Region.subregion non-null with PROTECT."""

    dependencies = [
        ("pipeline", "0012_backfill_subregion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="region",
            name="subregion",
            field=models.ForeignKey(
                help_text=(
                    "Parent L2 sub-region. Populated from ``region_id[:5]`` "
                    "in the fixture; migration 0012 back-fills historical rows."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="micro_regions",
                to="pipeline.eawssubregion",
            ),
        ),
    ]
