"""
pipeline/models.py — Region-hierarchy and resort models.

Defines four concrete reference-data models:
  - EawsMajorRegion: L1 EAWS region (e.g. "CH-4" Valais). Hand-maintained
    reference data; geometry derived from the union of descendant L4
    polygons.
  - EawsSubRegion: L2 EAWS region (e.g. "CH-41" Lower Valais). Hand-maintained
    reference data; FK ``major`` to its parent L1; geometry derived.
  - Region: SLF avalanche warning region — the L4 EAWS micro-region
    (e.g. "CH-4115"). FK ``subregion`` to its parent L2. Fixture-backed
    reference data; unknown region_ids seen during ingest raise rather
    than being auto-created.
  - Resort: ski resorts mapped to their SLF avalanche warning region.

Bulletin-derived models (PipelineRun, Bulletin, RegionBulletin,
RegionDayRating) live in ``bulletins.models`` — see SNOW-92.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models — put it in pipeline/services/ instead.
"""

from __future__ import annotations

from typing import Any

from django.db import models
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
#   L1  EawsMajorRegion  prefix CH-4      ("Valais")
#   L2  EawsSubRegion    prefix CH-41     ("Lower Valais")
#   L4  Region           region_id CH-4115 (the SLF warning region)
#
# L3 is skipped — in practice the layer is thin (often 1–2 micro-regions
# per L3 group) and can be derived from ``region_id[:6]`` if ever needed.
#
# All three models are fixture-backed and treated as static reference
# data. ``Region`` is NOT auto-created at bulletin-ingest time (see
# ``pipeline.services.data_fetcher._get_region``); an unknown
# ``region_id`` in an inbound bulletin raises ``UnknownRegionError`` so a
# human can update the fixtures.
#
# L1 and L2 geometry (``centre``, ``bbox``, ``boundary``) is derived —
# pre-computed once by ``refresh_eaws_fixtures`` from the union of the
# L4 children and stored in the fixture. Never computed at request time.


class EawsMajorRegionQuerySet(models.QuerySet["EawsMajorRegion"]):
    """Custom queryset for EawsMajorRegion."""

    def get_by_natural_key(self, prefix: str) -> EawsMajorRegion:
        """Look up an EawsMajorRegion by its prefix for fixture deserialisation."""
        return self.get(prefix=prefix)


class EawsMajorRegion(BaseModel):
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

    objects = EawsMajorRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

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


class EawsSubRegionQuerySet(models.QuerySet["EawsSubRegion"]):
    """Custom queryset for EawsSubRegion."""

    def get_by_natural_key(self, prefix: str) -> EawsSubRegion:
        """Look up an EawsSubRegion by its prefix for fixture deserialisation."""
        return self.get(prefix=prefix)


class EawsSubRegion(BaseModel):
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
        EawsMajorRegion,
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

    objects = EawsSubRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

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
# Region (L4 EAWS micro-region / SLF warning region)
# ---------------------------------------------------------------------------


class RegionQuerySet(models.QuerySet["Region"]):
    """Custom queryset for Region."""

    def get_by_natural_key(self, region_id: str) -> Region:
        """Look up a Region by its region_id for fixture deserialization."""
        return self.get(region_id=region_id)


class Region(BaseModel):
    """
    An SLF avalanche warning region (e.g. "CH-4115").

    Conceptually the **L4 EAWS micro-region** — the leaf of the EAWS
    hierarchy. Its parent ``EawsSubRegion`` is resolved by ``region_id[:5]``
    and its grand-parent ``EawsMajorRegion`` by ``region_id[:4]``, exposed
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
        EawsSubRegion,
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

    objects = RegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["region_id"]

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
    def major_region(self) -> EawsMajorRegion:
        """Return the L1 major region this region belongs to."""
        return self.subregion.major


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
    The fixture in ``pipeline/fixtures/resorts.json`` is the source of truth
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
        Region,
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

        ordering = ["name"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.name} ({self.region.region_id})"
