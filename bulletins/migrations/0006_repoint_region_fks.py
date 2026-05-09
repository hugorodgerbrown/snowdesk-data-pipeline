"""SNOW-140: state-only repoint of bulletin FKs from pipeline.region to regions.region.

Updates Django's migration state so that the four bulletin-side FK
fields (``bulletin.regions`` M2M, ``regionbulletin.region``,
``regiondayrating.region``, ``weathersnapshot.region``) point at
``regions.region`` instead of ``pipeline.region``. The underlying
foreign-key constraints in the DB are unchanged because both
``pipeline.Region`` and ``regions.Region`` map to the same physical
table (``pipeline_region``), so ``database_operations`` is empty.

Pairs with ``regions.0001_initial`` (which introduces
``regions.Region`` to Django state) and
``pipeline.0020_remove_reference_models`` (which removes
``pipeline.Region`` after this migration has cleared the references).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only re-target of the bulletin → region FKs."""

    dependencies = [
        ("bulletins", "0005_recompute_day_ratings_v5"),
        ("regions", "0001_initial"),
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
                        to="regions.region",
                    ),
                ),
                migrations.AlterField(
                    model_name="regionbulletin",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bulletin_links",
                        to="regions.region",
                    ),
                ),
                migrations.AlterField(
                    model_name="regiondayrating",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="day_ratings",
                        to="regions.region",
                    ),
                ),
                migrations.AlterField(
                    model_name="weathersnapshot",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weather_snapshots",
                        to="regions.region",
                    ),
                ),
            ],
        ),
    ]
