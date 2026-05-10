"""SNOW-142: state-only repoint of bulletin FKs from regions.region to regions.microregion.

After regions.0002 renames the Region model to MicroRegion, Django's
migration state still records the four bulletin-side FK/M2M fields as
pointing at regions.region.  This migration updates those references to
regions.microregion so the state graph is consistent.

No DDL runs — the physical FK constraints are unchanged (the table name
``pipeline_region`` has not moved).  ``database_operations`` is empty.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only repoint of bulletin → region FKs to MicroRegion."""

    dependencies = [
        ("bulletins", "0006_repoint_region_fks"),
        ("regions", "0002_rename_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="bulletin",
                    name="regions",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="bulletins",
                        through="bulletins.RegionBulletin",
                        to="regions.microregion",
                    ),
                ),
                migrations.AlterField(
                    model_name="regionbulletin",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bulletin_links",
                        to="regions.microregion",
                    ),
                ),
                migrations.AlterField(
                    model_name="regiondayrating",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="day_ratings",
                        to="regions.microregion",
                    ),
                ),
                migrations.AlterField(
                    model_name="weathersnapshot",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weather_snapshots",
                        to="regions.microregion",
                    ),
                ),
            ],
        ),
    ]
