"""SNOW-92: state-only deletion of bulletin-derived models from pipeline.

Removes Bulletin / RegionBulletin / PipelineRun / RegionDayRating from
the pipeline app's Django state. The underlying tables
(``pipeline_bulletin``, ``pipeline_regionbulletin``,
``pipeline_pipelinerun``, ``pipeline_regiondayrating``) are NOT dropped —
``database_operations`` is empty. The same physical tables continue to
back the new models defined in ``bulletins.models`` via ``Meta.db_table``.

This migration must run as a pair with ``bulletins.0001_initial``; both
have ``database_operations = []`` so the cutover is purely a re-attribution
of which app Django thinks owns each model.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0016_backfill_region_neighbours"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="bulletin",
                    name="pipeline_run",
                ),
                migrations.RemoveField(
                    model_name="bulletin",
                    name="regions",
                ),
                migrations.RemoveField(
                    model_name="regionbulletin",
                    name="bulletin",
                ),
                migrations.RemoveField(
                    model_name="regiondayrating",
                    name="source_bulletin",
                ),
                migrations.AlterUniqueTogether(
                    name="regionbulletin",
                    unique_together=None,
                ),
                migrations.RemoveField(
                    model_name="regionbulletin",
                    name="region",
                ),
                migrations.AlterUniqueTogether(
                    name="regiondayrating",
                    unique_together=None,
                ),
                migrations.RemoveField(
                    model_name="regiondayrating",
                    name="region",
                ),
                migrations.DeleteModel(
                    name="PipelineRun",
                ),
                migrations.DeleteModel(
                    name="Bulletin",
                ),
                migrations.DeleteModel(
                    name="RegionBulletin",
                ),
                migrations.DeleteModel(
                    name="RegionDayRating",
                ),
            ],
        ),
    ]
