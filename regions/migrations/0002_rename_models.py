"""SNOW-142: state-only rename of the three EAWS region hierarchy models.

Renames:
  EawsMajorRegion → MajorRegion
  EawsSubRegion   → SubRegion
  Region          → MicroRegion

Also adds verbose_name / verbose_name_plural to MicroRegion.Meta so that the
admin label stays human-readable (previously no explicit verbose_name, which
would default to "Micro region" after the rename).

All three ``Meta.db_table`` values are unchanged (``pipeline_eawsmajorregion``,
``pipeline_eawssubregion``, ``pipeline_region``), so this is purely a
state-level rename — zero DDL runs.

``database_operations`` is empty; ``state_operations`` carries the three
RenameModel operations plus the AlterModelOptions for MicroRegion.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """State-only rename of EawsMajorRegion/EawsSubRegion/Region."""

    dependencies = [
        ("regions", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(
                    old_name="EawsMajorRegion",
                    new_name="MajorRegion",
                ),
                migrations.RenameModel(
                    old_name="EawsSubRegion",
                    new_name="SubRegion",
                ),
                migrations.RenameModel(
                    old_name="Region",
                    new_name="MicroRegion",
                ),
                migrations.AlterModelOptions(
                    name="MicroRegion",
                    options={
                        "ordering": ["region_id"],
                        "verbose_name": "EAWS micro-region",
                        "verbose_name_plural": "EAWS micro-regions",
                    },
                ),
            ],
        ),
    ]
