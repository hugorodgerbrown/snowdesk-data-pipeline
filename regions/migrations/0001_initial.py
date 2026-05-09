"""SNOW-140: state-only initial migration for the regions app.

Re-attributes the four reference-data models (EawsMajorRegion,
EawsSubRegion, Region, Resort) from ``pipeline`` to ``regions`` in
Django's migration state. The underlying physical tables
(``pipeline_eawsmajorregion``, ``pipeline_eawssubregion``,
``pipeline_region``, ``pipeline_resort``) are unchanged — the new
models pin ``Meta.db_table`` to the existing names and
``database_operations`` is empty so no DDL runs.

Pairs with ``pipeline.0020_remove_reference_models``;
``bulletins.0006_repoint_region_fks`` and
``subscriptions.0004_alter_subscription_region`` repoint the FKs
that previously targeted ``pipeline.region`` so the cutover is
purely a re-attribution of which app Django thinks owns each model.
All four migrations have ``database_operations = []``.

Mirrors the SNOW-92 pattern used to move the bulletin models out
of pipeline (see ``bulletins/migrations/0001_initial.py``).
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only re-attribution of the reference-data models to regions."""

    initial = True

    dependencies = [
        ("pipeline", "0019_close_region_boundary_rings"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="EawsMajorRegion",
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
                            "prefix",
                            models.CharField(
                                db_index=True,
                                help_text="EAWS L1 prefix, e.g. 'CH-4'.",
                                max_length=4,
                                unique=True,
                            ),
                        ),
                        (
                            "country",
                            models.CharField(
                                db_index=True,
                                help_text=(
                                    "ISO-3166-1 alpha-2 country code, e.g. 'CH'."
                                ),
                                max_length=2,
                            ),
                        ),
                        (
                            "name_native",
                            models.CharField(
                                help_text=(
                                    "Region name in the locally dominant language "
                                    "(German / French / Italian for Switzerland)."
                                ),
                                max_length=100,
                            ),
                        ),
                        (
                            "name_en",
                            models.CharField(
                                blank=True,
                                help_text=(
                                    "English name where SLF publishes one; blank "
                                    "otherwise."
                                ),
                                max_length=100,
                            ),
                        ),
                        (
                            "centre",
                            models.JSONField(
                                blank=True,
                                help_text=(
                                    'Derived geographic centre as {"lon": float, '
                                    '"lat": float}. Computed by '
                                    "refresh_eaws_fixtures from the union of L4 "
                                    "children."
                                ),
                                null=True,
                            ),
                        ),
                        (
                            "bbox",
                            models.JSONField(
                                blank=True,
                                help_text=(
                                    "Derived bounding box as [min_lon, min_lat, "
                                    "max_lon, max_lat]. Computed by "
                                    "refresh_eaws_fixtures from the union of L4 "
                                    "children."
                                ),
                                null=True,
                            ),
                        ),
                        (
                            "boundary",
                            models.JSONField(
                                blank=True,
                                help_text=(
                                    "Derived outer boundary as a GeoJSON Polygon "
                                    "or MultiPolygon. Computed by "
                                    "refresh_eaws_fixtures from the union of L4 "
                                    "children."
                                ),
                                null=True,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "EAWS major region",
                        "verbose_name_plural": "EAWS major regions",
                        "db_table": "pipeline_eawsmajorregion",
                        "ordering": ["prefix"],
                        "abstract": False,
                    },
                ),
                migrations.CreateModel(
                    name="EawsSubRegion",
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
                            "prefix",
                            models.CharField(
                                db_index=True,
                                help_text="EAWS L2 prefix, e.g. 'CH-41'.",
                                max_length=5,
                                unique=True,
                            ),
                        ),
                        (
                            "name_native",
                            models.CharField(
                                help_text=(
                                    "Region name in the locally dominant language "
                                    "(German / French / Italian for Switzerland)."
                                ),
                                max_length=100,
                            ),
                        ),
                        (
                            "name_en",
                            models.CharField(
                                blank=True,
                                help_text=(
                                    "English name where SLF publishes one; blank "
                                    "otherwise."
                                ),
                                max_length=100,
                            ),
                        ),
                        ("centre", models.JSONField(blank=True, null=True)),
                        ("bbox", models.JSONField(blank=True, null=True)),
                        ("boundary", models.JSONField(blank=True, null=True)),
                        (
                            "major",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="subregions",
                                to="regions.eawsmajorregion",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "EAWS sub-region",
                        "verbose_name_plural": "EAWS sub-regions",
                        "db_table": "pipeline_eawssubregion",
                        "ordering": ["prefix"],
                        "abstract": False,
                    },
                ),
                migrations.CreateModel(
                    name="Region",
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
                            "region_id",
                            models.CharField(
                                db_index=True,
                                help_text="SLF region identifier, e.g. 'CH-4115'.",
                                max_length=64,
                                unique=True,
                            ),
                        ),
                        ("name", models.CharField(max_length=255)),
                        ("slug", models.SlugField(max_length=255, unique=True)),
                        (
                            "centre",
                            models.JSONField(
                                blank=True,
                                help_text=(
                                    'Geographic centre of the region as {"lon": '
                                    'float, "lat": float}. Stored as JSON; uses '
                                    "WGS 84 coordinates."
                                ),
                                null=True,
                            ),
                        ),
                        (
                            "boundary",
                            models.JSONField(
                                blank=True,
                                help_text=(
                                    "Region boundary as a GeoJSON Polygon "
                                    'geometry object ({"type": "Polygon", '
                                    '"coordinates": [...]}). Stored as JSON '
                                    "rather than a PostGIS geometry type."
                                ),
                                null=True,
                            ),
                        ),
                        (
                            "neighbours",
                            models.ManyToManyField(
                                blank=True,
                                help_text=(
                                    "Geographic neighbours — other regions whose "
                                    "polygons share a border with this one. "
                                    "Computed at fixture-build time from the "
                                    "boundary geometry (see "
                                    "scripts/build_regions_fixture.py); not "
                                    "maintained at runtime."
                                ),
                                to="regions.region",
                            ),
                        ),
                        (
                            "subregion",
                            models.ForeignKey(
                                help_text=(
                                    "Parent L2 sub-region. Populated from "
                                    "``region_id[:5]`` in the fixture; migration "
                                    "0012 back-fills historical rows."
                                ),
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="micro_regions",
                                to="regions.eawssubregion",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_region",
                        "ordering": ["region_id"],
                        "abstract": False,
                    },
                ),
                migrations.CreateModel(
                    name="Resort",
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
                        ("name", models.CharField(max_length=255)),
                        (
                            "name_alt",
                            models.CharField(
                                blank=True,
                                help_text=(
                                    "Alternative or marketing name for the resort."
                                ),
                                max_length=255,
                            ),
                        ),
                        (
                            "canton",
                            models.CharField(
                                help_text=(
                                    "Swiss canton abbreviation, e.g. 'VS', 'GR'."
                                ),
                                max_length=5,
                            ),
                        ),
                        ("notes", models.TextField(blank=True)),
                        ("latitude", models.FloatField(blank=True, null=True)),
                        ("longitude", models.FloatField(blank=True, null=True)),
                        (
                            "geocode_source",
                            models.CharField(
                                blank=True,
                                choices=[
                                    ("manual", "Manual"),
                                    ("auto", "Auto"),
                                    ("import", "Import"),
                                ],
                                default="",
                                max_length=16,
                            ),
                        ),
                        (
                            "geocode_confidence",
                            models.FloatField(blank=True, null=True),
                        ),
                        ("geocoded_at", models.DateTimeField(blank=True, null=True)),
                        ("needs_review", models.BooleanField(default=False)),
                        (
                            "region",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="resorts",
                                to="regions.region",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pipeline_resort",
                        "ordering": ["name"],
                        "abstract": False,
                    },
                ),
            ],
        ),
    ]
