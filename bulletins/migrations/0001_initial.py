"""SNOW-92: state-only initial migration for the bulletins app.

Re-attributes the four bulletin-derived models from ``pipeline`` to
``bulletins`` in Django's migration state. The underlying physical
tables (``pipeline_bulletin``, ``pipeline_regionbulletin``,
``pipeline_pipelinerun``, ``pipeline_regiondayrating``) are unchanged —
the new models pin ``Meta.db_table`` to the existing names and
``database_operations`` is empty so no DDL runs.

Pairs with ``pipeline.0017_remove_bulletin_models``; the pair must run
in the order Django's dependency graph dictates (this migration is
declared as depending on the pipeline removal) so that the pipeline
app's state is cleared before bulletins re-creates them.
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("pipeline", "0017_remove_bulletin_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="PipelineRun",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "started_at",
                            models.DateTimeField(
                                db_index=True, default=django.utils.timezone.now
                            ),
                        ),
                        ("finished_at", models.DateTimeField(blank=True, null=True)),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("pending", "Pending"),
                                    ("running", "Running"),
                                    ("success", "Success"),
                                    ("failed", "Failed"),
                                ],
                                db_index=True,
                                default="pending",
                                max_length=16,
                            ),
                        ),
                        ("records_created", models.PositiveIntegerField(default=0)),
                        ("records_updated", models.PositiveIntegerField(default=0)),
                        (
                            "records_failed",
                            models.PositiveIntegerField(
                                default=0,
                                help_text=(
                                    "Number of bulletins whose render model could not be"
                                    " built (stored with version=0 error sentinel)."
                                ),
                            ),
                        ),
                        ("error_message", models.TextField(blank=True)),
                        (
                            "triggered_by",
                            models.CharField(
                                default="unknown",
                                help_text=(
                                    "Who or what triggered this run (e.g. 'scheduler',"
                                    " 'backfill', 'manual')."
                                ),
                                max_length=64,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_pipelinerun",
                        "ordering": ["-started_at"],
                        "abstract": False,
                    },
                ),
                migrations.CreateModel(
                    name="Bulletin",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "bulletin_id",
                            models.CharField(
                                db_index=True, max_length=255, unique=True
                            ),
                        ),
                        (
                            "raw_data",
                            models.JSONField(
                                blank=True,
                                default=dict,
                                help_text=(
                                    "Full CAAML bulletin wrapped in a GeoJSON Feature"
                                    " envelope."
                                ),
                            ),
                        ),
                        (
                            "render_model",
                            models.JSONField(
                                blank=True,
                                default=dict,
                                help_text=(
                                    "Versioned, presentation-ready view of the bulletin"
                                    " built from raw_data. Shape: {version, danger,"
                                    " traits, fallback_key_message, snowpack_structure}."
                                    " Rebuilt by upsert_bulletin and on demand by"
                                    " rebuild_render_models."
                                ),
                            ),
                        ),
                        (
                            "render_model_version",
                            models.PositiveIntegerField(
                                db_index=True,
                                default=0,
                                help_text=(
                                    "Version of the render_model schema. 0 means not yet"
                                    " built."
                                ),
                            ),
                        ),
                        ("issued_at", models.DateTimeField(db_index=True)),
                        ("valid_from", models.DateTimeField()),
                        ("valid_to", models.DateTimeField()),
                        ("next_update", models.DateTimeField(blank=True, null=True)),
                        ("lang", models.CharField(default="en", max_length=8)),
                        ("unscheduled", models.BooleanField(default=False)),
                        (
                            "pipeline_run",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="bulletins",
                                to="bulletins.pipelinerun",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_bulletin",
                        "ordering": ["-issued_at"],
                        "abstract": False,
                    },
                ),
                migrations.CreateModel(
                    name="RegionBulletin",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "region_name_at_time",
                            models.CharField(
                                blank=True,
                                help_text="Region name as it appeared in this bulletin.",
                                max_length=255,
                            ),
                        ),
                        (
                            "bulletin",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="region_links",
                                to="bulletins.bulletin",
                            ),
                        ),
                        (
                            "region",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="bulletin_links",
                                to="pipeline.region",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_regionbulletin",
                        "ordering": ["region__region_id"],
                        "abstract": False,
                        "unique_together": {("bulletin", "region")},
                    },
                ),
                migrations.AddField(
                    model_name="bulletin",
                    name="regions",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="bulletins",
                        through="bulletins.RegionBulletin",
                        to="pipeline.region",
                    ),
                ),
                migrations.CreateModel(
                    name="RegionDayRating",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("date", models.DateField(db_index=True)),
                        (
                            "min_rating",
                            models.CharField(
                                choices=[
                                    ("no_rating", "No rating"),
                                    ("low", "Low"),
                                    ("moderate", "Moderate"),
                                    ("considerable", "Considerable"),
                                    ("high", "High"),
                                    ("very_high", "Very high"),
                                ],
                                default="no_rating",
                                help_text=(
                                    "Lowest danger rating across all qualifying bulletins"
                                    " for this day. Equals max_rating on uniform days;"
                                    " differs on variable days."
                                ),
                                max_length=16,
                            ),
                        ),
                        (
                            "min_subdivision",
                            models.CharField(
                                blank=True,
                                default="",
                                help_text=(
                                    "Subdivision suffix ('+', '-', '=') from the bulletin"
                                    " that gave min_rating (latest valid_from on ties),"
                                    " or blank."
                                ),
                                max_length=2,
                            ),
                        ),
                        (
                            "max_rating",
                            models.CharField(
                                choices=[
                                    ("no_rating", "No rating"),
                                    ("low", "Low"),
                                    ("moderate", "Moderate"),
                                    ("considerable", "Considerable"),
                                    ("high", "High"),
                                    ("very_high", "Very high"),
                                ],
                                default="no_rating",
                                max_length=16,
                            ),
                        ),
                        (
                            "max_subdivision",
                            models.CharField(
                                blank=True,
                                default="",
                                help_text=(
                                    "Subdivision suffix ('+', '-', '=') from the source"
                                    " bulletin, or blank."
                                ),
                                max_length=8,
                            ),
                        ),
                        (
                            "version",
                            models.PositiveIntegerField(
                                db_index=True,
                                default=0,
                                help_text=(
                                    "DAY_RATING_VERSION at the time this row was"
                                    " computed."
                                ),
                            ),
                        ),
                        (
                            "region",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="day_ratings",
                                to="pipeline.region",
                            ),
                        ),
                        (
                            "source_bulletin",
                            models.ForeignKey(
                                blank=True,
                                help_text="The bulletin that produced max_rating.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="day_ratings",
                                to="bulletins.bulletin",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_regiondayrating",
                        "ordering": ["-date", "region__region_id"],
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["region", "date"],
                                name="pipeline_re_region__fa9668_idx",
                            )
                        ],
                        "unique_together": {("region", "date")},
                    },
                ),
            ],
        ),
    ]
