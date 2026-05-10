"""SNOW-142: state-only introduction of MicroRegionNeighbour explicit through model.

The MicroRegion.neighbours self-referential M2M previously used an
auto-created through table (pipeline_region_neighbours) with columns
from_region_id / to_region_id. After renaming Region → MicroRegion,
Django's ORM derives column names from the model class name and expects
from_microregion_id / to_microregion_id, which don't exist.

Fix: introduce an explicit MicroRegionNeighbour through model with
db_column overrides that pin the physical column names to the existing
from_region_id / to_region_id values. The db_table is also pinned to
pipeline_region_neighbours.

Both operations are state-only (database_operations=[]) — no DDL runs.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only introduction of MicroRegionNeighbour explicit through model."""

    dependencies = [
        ("regions", "0002_rename_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="MicroRegionNeighbour",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "from_microregion",
                            models.ForeignKey(
                                db_column="from_region_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="regions.microregion",
                            ),
                        ),
                        (
                            "to_microregion",
                            models.ForeignKey(
                                db_column="to_region_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="regions.microregion",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_region_neighbours",
                        "unique_together": {("from_microregion", "to_microregion")},
                    },
                ),
                migrations.AlterField(
                    model_name="microregion",
                    name="neighbours",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "Geographic neighbours — other regions whose polygons share "
                            "a border with this one. Computed at fixture-build time from "
                            "the boundary geometry (see scripts/build_regions_fixture.py); "
                            "not maintained at runtime."
                        ),
                        through="regions.MicroRegionNeighbour",
                        to="regions.microregion",
                    ),
                ),
            ],
        ),
    ]
