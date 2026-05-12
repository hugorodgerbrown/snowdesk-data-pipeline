"""SNOW-140: state-only deletion of the four reference-data models from pipeline.

Removes EawsMajorRegion / EawsSubRegion / Region / Resort from the
pipeline app's Django state. The underlying physical tables
(``pipeline_eawsmajorregion``, ``pipeline_eawssubregion``,
``pipeline_region``, ``pipeline_resort``) are NOT dropped —
``database_operations`` is empty. The same physical tables continue
to back the new models defined in ``regions.models`` via
``Meta.db_table``.

Runs as the closing step of the SNOW-140 cutover, after
``regions.0001_initial`` has registered the new ownership and after
``bulletins.0006_repoint_region_fks`` and
``subscriptions.0001_initial`` has been applied and every
remaining FK reference to ``pipeline.region``.

Mirrors the SNOW-92 pattern (see
``pipeline/migrations/0017_remove_bulletin_models.py``).
"""

from django.db import migrations


class Migration(migrations.Migration):
    """State-only removal of the reference-data models from pipeline."""

    dependencies = [
        ("bulletins", "0006_repoint_region_fks"),
        ("pipeline", "0019_close_region_boundary_rings"),
        ("subscriptions", "0003_add_subscription"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="eawssubregion",
                    name="major",
                ),
                migrations.RemoveField(
                    model_name="region",
                    name="subregion",
                ),
                migrations.RemoveField(
                    model_name="region",
                    name="neighbours",
                ),
                migrations.RemoveField(
                    model_name="resort",
                    name="region",
                ),
                migrations.DeleteModel(
                    name="EawsMajorRegion",
                ),
                migrations.DeleteModel(
                    name="EawsSubRegion",
                ),
                migrations.DeleteModel(
                    name="Region",
                ),
                migrations.DeleteModel(
                    name="Resort",
                ),
            ],
        ),
    ]
