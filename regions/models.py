"""
regions/models.py — Region-hierarchy and resort reference-data models.

Defines four concrete reference-data models:
  - MajorRegion: L1 EAWS region (e.g. "CH-4" Valais). Hand-maintained
    reference data; geometry derived from the union of descendant L4
    polygons.
  - SubRegion: L2 EAWS region (e.g. "CH-41" Lower Valais). Hand-maintained
    reference data; FK ``major`` to its parent L1; geometry derived.
  - MicroRegion: SLF avalanche warning region — the L4 EAWS micro-region
    (e.g. "CH-4115"). FK ``subregion`` to its parent L2. Fixture-backed
    reference data; unknown region_ids seen during ingest raise rather
    than being auto-created.
  - Resort: ski resorts mapped to their SLF avalanche warning region.

Split from the legacy ``pipeline`` app in SNOW-140; per the same SNOW-92
pattern that re-attributed the bulletin models, every ``Meta.db_table``
below is pinned to the existing ``pipeline_*`` table name so that the
move is state-only — no DDL runs. A future cleanup ticket can rename
the physical tables.

Bulletin-derived models (PipelineRun, Bulletin, RegionBulletin,
RegionDayRating, WeatherSnapshot) live in ``bulletins.models``.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from core.models import BaseModel

# ---------------------------------------------------------------------------
# EAWS region hierarchy
# ---------------------------------------------------------------------------
#
# EAWS (European Avalanche Warning Services) identifies avalanche warning
# regions with an N-digit code per level, with each digit narrowing the
# scope (e.g. "CH-4" → major, "CH-41" → sub, "CH-4115" → micro/warning).
#
# Snowdesk models three tiers as first-class rows:
#
#   L1  MajorRegion  prefix CH-4      ("Valais")
#   L2  SubRegion    prefix CH-41     ("Lower Valais")
#   L4  MicroRegion  region_id CH-4115 (the SLF warning region)
#
# L3 is skipped — in practice the layer is thin (often 1–2 micro-regions
# per L3 group) and can be derived from ``region_id[:6]`` if ever needed.
#
# All three models are fixture-backed and treated as static reference
# data. ``MicroRegion`` is NOT auto-created at bulletin-ingest time (see
# ``bulletins.services.data_fetcher._get_region``); an unknown
# ``region_id`` in an inbound bulletin raises ``UnknownRegionError`` so a
# human can update the fixtures.
#
# L1 and L2 geometry (``centre``, ``bbox``, ``boundary``) is derived —
# pre-computed once by ``refresh_eaws_fixtures`` from the union of the
# L4 children and stored in the fixture. Never computed at request time.


class MajorRegionQuerySet(models.QuerySet["MajorRegion"]):
    """Custom queryset for MajorRegion."""

    def get_by_natural_key(self, prefix: str) -> MajorRegion:
        """Look up a MajorRegion by its prefix for fixture deserialisation."""
        return self.get(prefix=prefix)


class MajorRegion(BaseModel):
    """
    L1 EAWS region — e.g. "CH-4" Valais.

    Hand-maintained reference data; one row per major region. Geometry
    fields are derived from the union of descendant L4 polygons by
    ``refresh_eaws_fixtures`` and stored in the fixture.
    """

    prefix = models.CharField(
        max_length=4,
        unique=True,
        db_index=True,
        help_text="EAWS L1 prefix, e.g. 'CH-4'.",
    )
    country = models.CharField(
        max_length=2,
        db_index=True,
        help_text="ISO-3166-1 alpha-2 country code, e.g. 'CH'.",
    )
    name_native = models.CharField(
        max_length=100,
        help_text=(
            "Region name in the locally dominant language "
            "(German / French / Italian for Switzerland)."
        ),
    )
    name_en = models.CharField(
        max_length=100,
        blank=True,
        help_text="English name where SLF publishes one; blank otherwise.",
    )
    centre = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            'Derived geographic centre as {"lon": float, "lat": float}. '
            "Computed by refresh_eaws_fixtures from the union of L4 children."
        ),
    )
    bbox = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Derived bounding box as [min_lon, min_lat, max_lon, max_lat]. "
            "Computed by refresh_eaws_fixtures from the union of L4 children."
        ),
    )
    boundary = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Derived outer boundary as a GeoJSON Polygon or MultiPolygon. "
            "Computed by refresh_eaws_fixtures from the union of L4 children."
        ),
    )

    objects = MajorRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        db_table = "pipeline_eawsmajorregion"
        ordering = ["prefix"]
        verbose_name = "EAWS major region"
        verbose_name_plural = "EAWS major regions"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.prefix} — {self.name_native}"

    def to_string(self) -> str:
        """Return a concise canonical string (prefix + native name)."""
        return f"{self.prefix} {self.name_native}"

    def natural_key(self) -> tuple[str]:
        """Return the natural key for serialisation (prefix)."""
        return (self.prefix,)


class SubRegionQuerySet(models.QuerySet["SubRegion"]):
    """Custom queryset for SubRegion."""

    def get_by_natural_key(self, prefix: str) -> SubRegion:
        """Look up a SubRegion by its prefix for fixture deserialisation."""
        return self.get(prefix=prefix)


class SubRegion(BaseModel):
    """
    L2 EAWS region — e.g. "CH-41" Lower Valais.

    Hand-maintained reference data; one row per sub-region. ``major`` is
    the parent L1 major region. Geometry fields are derived from the
    union of descendant L4 polygons by ``refresh_eaws_fixtures``.
    """

    prefix = models.CharField(
        max_length=5,
        unique=True,
        db_index=True,
        help_text="EAWS L2 prefix, e.g. 'CH-41'.",
    )
    major = models.ForeignKey(
        MajorRegion,
        on_delete=models.PROTECT,
        related_name="subregions",
    )
    name_native = models.CharField(
        max_length=100,
        help_text=(
            "Region name in the locally dominant language "
            "(German / French / Italian for Switzerland)."
        ),
    )
    name_en = models.CharField(
        max_length=100,
        blank=True,
        help_text="English name where SLF publishes one; blank otherwise.",
    )
    centre = models.JSONField(null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True)
    boundary = models.JSONField(null=True, blank=True)

    objects = SubRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        db_table = "pipeline_eawssubregion"
        ordering = ["prefix"]
        verbose_name = "EAWS sub-region"
        verbose_name_plural = "EAWS sub-regions"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.prefix} — {self.name_native}"

    def to_string(self) -> str:
        """Return a concise canonical string (prefix + native name)."""
        return f"{self.prefix} {self.name_native}"

    def natural_key(self) -> tuple[str]:
        """Return the natural key for serialisation (prefix)."""
        return (self.prefix,)


# ---------------------------------------------------------------------------
# MicroRegion (L4 EAWS micro-region / SLF warning region)
# ---------------------------------------------------------------------------


class MicroRegionQuerySet(models.QuerySet["MicroRegion"]):
    """Custom queryset for MicroRegion."""

    def get_by_natural_key(self, region_id: str) -> MicroRegion:
        """Look up a MicroRegion by its region_id for fixture deserialization."""
        return self.get(region_id=region_id)


class MicroRegion(BaseModel):
    """
    An SLF avalanche warning region (e.g. "CH-4115").

    Conceptually the **L4 EAWS micro-region** — the leaf of the EAWS
    hierarchy. Its parent ``SubRegion`` is resolved by ``region_id[:5]``
    and its grand-parent ``MajorRegion`` by ``region_id[:4]``, exposed
    via the ``major_region`` property.

    Treated as static, fixture-backed reference data. Unknown ``region_id``
    values encountered during bulletin ingest raise ``UnknownRegionError``
    rather than being silently auto-created — the data source is
    authoritative and surprises should surface as errors so the fixtures
    can be updated deliberately.
    """

    region_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SLF region identifier, e.g. 'CH-4115'.",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    subregion = models.ForeignKey(
        SubRegion,
        on_delete=models.PROTECT,
        related_name="micro_regions",
        help_text=(
            "Parent L2 sub-region. Populated from ``region_id[:5]`` in the "
            "fixture; migration 0012 back-fills historical rows."
        ),
    )
    centre = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            'Geographic centre of the region as {"lon": float, "lat": float}. '
            "Stored as JSON; uses WGS 84 coordinates."
        ),
    )
    boundary = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Region boundary as a GeoJSON Polygon geometry object "
            '({"type": "Polygon", "coordinates": [...]}). '
            "Stored as JSON rather than a PostGIS geometry type."
        ),
    )
    neighbours = models.ManyToManyField(
        "self",
        symmetrical=True,
        blank=True,
        help_text=(
            "Geographic neighbours — other regions whose polygons share "
            "a border with this one. Computed at fixture-build time from "
            "the boundary geometry (see scripts/build_regions_fixture.py); "
            "not maintained at runtime."
        ),
    )

    objects = MicroRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        db_table = "pipeline_region"
        ordering = ["region_id"]
        verbose_name = "EAWS micro-region"
        verbose_name_plural = "EAWS micro-regions"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.region_id} — {self.name}"

    def natural_key(self) -> tuple[str]:
        """Return the natural key for serialization (region_id)."""
        return (self.region_id,)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Auto-generate slug from region_id if not set."""
        if not self.slug:
            self.slug = slugify(self.region_id)
        super().save(*args, **kwargs)

    @property
    def major_region(self) -> MajorRegion:
        """Return the L1 major region this region belongs to."""
        return self.subregion.major

    @property
    def canonical_region_id(self) -> str:
        """Lowercase, hyphen-normalised ``region_id`` for URL paths.

        ``region_id`` is stored case-preserved (e.g. ``"CH-4115"``) so
        the SLF identifier round-trips through the API exactly as it
        arrived. URLs always use the slugified form so callers and
        search engines see a single canonical path per region.
        """
        return slugify(self.region_id)

    @property
    def name_slug(self) -> str:
        """Slugified region name for the second URL path component.

        Re-derived from ``self.name`` on every access rather than from
        the stored ``slug`` field — that field is auto-generated from
        ``region_id`` (e.g. ``"ch-4115"``), not from the name. Computing
        from the name keeps the URL human-readable
        (``/ch-4115/brunig-lungern/``).
        """
        return slugify(self.name)

    def get_absolute_url(self, target_date: datetime.date | None = None) -> str:
        """Return the canonical bulletin URL for this region.

        Two distinct canonical forms (SNOW-99):

        * ``target_date is None`` (default) → form 2
          ``/<region_id>/<slug>/``. The "today" / evergreen URL — its
          rendered content shifts as the calendar advances, and search
          engines index it as a single live page.
        * ``target_date`` set to a date → form 3
          ``/<region_id>/<slug>/<YYYY-MM-DD>/``. The historical URL
          for that specific calendar day; once the date is past the
          rendered content is fixed.

        Both forms always use the lowercased ``region_id`` and the
        name-derived slug so callers and search engines see one
        canonical URL per (region [, day]).
        """
        if target_date is None:
            return reverse(
                "public:bulletin",
                kwargs={
                    "region_id": self.canonical_region_id,
                    "slug": self.name_slug,
                },
            )
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": self.canonical_region_id,
                "slug": self.name_slug,
                "date_str": target_date.isoformat(),
            },
        )


# ---------------------------------------------------------------------------
# Resort
# ---------------------------------------------------------------------------


class ResortQuerySet(models.QuerySet):
    """Custom queryset for Resort."""

    def geocoded(self) -> "ResortQuerySet":
        """Return only resorts with both latitude and longitude set."""
        return self.filter(latitude__isnull=False, longitude__isnull=False)

    def needs_geocoding(self) -> "ResortQuerySet":
        """Return resorts missing coords or flagged for review."""
        return self.filter(
            models.Q(latitude__isnull=True)
            | models.Q(longitude__isnull=True)
            | models.Q(needs_review=True)
        )


class Resort(BaseModel):
    """
    A ski resort linked to an SLF avalanche warning region.

    Static reference data loaded from a fixture; not populated by the
    data pipeline. Allows users to look up bulletins by well-known resort
    names (e.g. "Crans-Montana") rather than official region identifiers.

    Geocoding fields (latitude/longitude/etc.) are populated by the
    edit-resorts mode on the public map (``?edit=resorts`` in DEBUG only).
    The fixture in ``regions/fixtures/resorts.json`` is the source of truth
    in git; run ``manage.py dump_resorts_fixture --commit`` after a session
    of edits to persist them.
    """

    GEOCODE_SOURCES = [
        ("manual", "Manual"),
        ("auto", "Auto"),
        ("import", "Import"),
    ]

    name = models.CharField(max_length=255)
    name_alt = models.CharField(
        max_length=255,
        blank=True,
        help_text="Alternative or marketing name for the resort.",
    )
    region = models.ForeignKey(
        MicroRegion,
        on_delete=models.CASCADE,
        related_name="resorts",
    )
    canton = models.CharField(
        max_length=5,
        help_text="Swiss canton abbreviation, e.g. 'VS', 'GR'.",
    )
    notes = models.TextField(blank=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(
        max_length=16,
        choices=GEOCODE_SOURCES,
        blank=True,
        default="",
    )
    geocode_confidence = models.FloatField(null=True, blank=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    needs_review = models.BooleanField(default=False)

    objects = ResortQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        db_table = "pipeline_resort"
        ordering = ["name"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.name} ({self.region.region_id})"
