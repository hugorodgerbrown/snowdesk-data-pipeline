"""
apps/regions/models.py — Region-hierarchy and resort reference-data models.

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

Bulletin-derived models (PipelineRun, Bulletin, RegionBulletin,
RegionDayRating, WeatherSnapshot) live in ``apps.bulletins.models``.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models.
"""

from __future__ import annotations

import datetime
from typing import Any, TypedDict

from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from apps.core.models import BaseModel

# Validates a "MM-DD" month-day string, e.g. "12-01" — used for the
# hand-curated typical season open/close fields on Resort. Blank values are
# allowed: Django skips validators on empty strings.
MONTH_DAY_VALIDATOR = RegexValidator(
    r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$",
    "Enter a month-day as MM-DD, e.g. 12-01.",
)

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
# ``apps.bulletins.services.slf_fetcher._get_region``); an unknown
# ``region_id`` in an inbound bulletin raises ``UnknownRegionError`` so a
# human can update the fixtures.
#
# L1 and L2 geometry (``centre``, ``bbox``, ``boundary``) is derived —
# pre-computed once by ``refresh_eaws_fixtures`` from the union of the
# L4 children and stored in the fixture. Never computed at request time.


class Centre(TypedDict):
    """Geographic centre point.

    Returned by ``MicroRegion.centre`` and related JSON-field reads.
    """

    lat: float
    lon: float


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
        max_length=12,
        unique=True,
        db_index=True,
        help_text="EAWS L1 prefix, e.g. 'CH-4' or 'AT-02' or 'IT-32-BZ'.",
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
    display_on_map = models.BooleanField(
        default=True,
        help_text=(
            "Whether this major region appears on the public map. "
            "Set to False to hide it from the L1/L2/L4 GeoJSON endpoints "
            "while keeping bulletin pages accessible at their canonical URLs."
        ),
    )

    objects = MajorRegionQuerySet.as_manager()

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
        max_length=16,
        unique=True,
        db_index=True,
        help_text="EAWS L2 prefix, e.g. 'CH-41' or 'AT-02-14' or 'IT-32-BZ-15'.",
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
# MicroRegionNeighbour — explicit through model for the self-referential M2M
# ---------------------------------------------------------------------------


class MicroRegionNeighbour(models.Model):
    """Explicit through model for the MicroRegion.neighbours self-referential M2M."""

    from_microregion = models.ForeignKey(
        "MicroRegion",
        on_delete=models.CASCADE,
        related_name="+",
    )
    to_microregion = models.ForeignKey(
        "MicroRegion",
        on_delete=models.CASCADE,
        related_name="+",
    )

    class Meta:
        """Model metadata."""

        unique_together = [("from_microregion", "to_microregion")]


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
    An EAWS avalanche warning micro-region (e.g. "CH-4115" or "FR-68").

    Conceptually the **L4 EAWS micro-region** — the leaf of the EAWS
    hierarchy. For Swiss regions, the parent ``SubRegion`` happens to share
    the first five characters of ``region_id`` (e.g. ``"CH-41"``), and the
    grand-parent ``MajorRegion`` the first four (e.g. ``"CH-4"``). This
    slicing convention is **Swiss-specific and not enforced** — French rows
    use EAWS canonical IDs such as ``"FR-68"`` which deliberately break the
    pattern. The ``subregion`` FK is always authoritative; never navigate the
    hierarchy by slicing ``region_id``.

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
            "Parent L2 sub-region. Set via the fixture's natural-key list. "
            "Swiss rows conventionally share a ``region_id[:5]`` prefix with "
            "this sub-region; French rows set the FK explicitly — the slicing "
            "convention is Swiss-only and not enforced."
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
    basemap_download = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Precomputed offline-basemap tile coverage for this region "
            "(regions.services.basemap_tiles.MICRO_BAND), populated by "
            "`manage.py compute_basemap_download --commit` via "
            "basemap_tiles.build_region_blob, which clips the bbox derived "
            "from `boundary` (MicroRegion has no stored bbox field) down to "
            "the tiles within one margin tile of the real boundary "
            "(SNOW-583). {band, count, mb, over_ceiling, centre_tile, z} — "
            '`z` is {"<z>": {"<y>": [xmin, xmax]}}, not the rectangular '
            '{"<z>": [xmin, xmax, ymin, ymax]} the custom-area download '
            "still uses — see regions/services/basemap_tiles.py for the "
            "shape. Never computed at request time; region geometry is "
            "static reference data so this never changes once computed."
        ),
    )
    neighbours: models.ManyToManyField[MicroRegion, MicroRegionNeighbour] = (
        models.ManyToManyField(
            "self",
            through="MicroRegionNeighbour",
            symmetrical=True,
            blank=True,
            help_text=(
                "Geographic neighbours — other regions whose polygons share "
                "a border with this one. Computed at fixture-build time from "
                "the boundary geometry (see build_switzerland_fixture); "
                "not maintained at runtime."
            ),
        )
    )

    objects = MicroRegionQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["region_id"]
        verbose_name = "EAWS micro-region"
        verbose_name_plural = "EAWS micro-regions"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.region_id} — {self.name}"

    def to_string(self) -> str:
        """Return a concise canonical string (region_id + name)."""
        return f"{self.region_id} {self.name}"

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
# RegionAlias
# ---------------------------------------------------------------------------


class RegionAliasQuerySet(models.QuerySet["RegionAlias"]):
    """Custom queryset for RegionAlias."""

    def get_by_natural_key(self, region_id: str, alias_text: str) -> RegionAlias:
        """Look up a RegionAlias by its (region_id, alias_text) natural key."""
        return self.get(region__region_id=region_id, alias_text=alias_text)


class RegionAlias(BaseModel):
    """
    A hand-curated alternate name for a MicroRegion.

    Covers, for example, a French/German exonym pair that shares no
    letters with the canonical name ("Sitten" for "Sion", "Coire" for
    "Chur"). Fuzzy matching (``apps.mcp_server.resolvers.search_places``) already
    tolerates accents, typos, punctuation, and whitespace variance, but
    cannot bridge two names that share almost no characters. This table
    is the curated escape hatch for that narrow case — deliberately
    minimal (no ``language`` or ``source`` field; those were scoped out
    of SNOW-409) since the only consumer is the fuzzy-search candidate
    pool, which only needs a name to index.
    """

    region = models.ForeignKey(
        MicroRegion,
        on_delete=models.PROTECT,
        related_name="aliases",
        help_text="The MicroRegion this alias resolves to.",
    )
    alias_text = models.CharField(
        max_length=100,
        help_text="An alternate name for the region, e.g. a French/German exonym.",
    )

    objects = RegionAliasQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=["region", "alias_text"], name="unique_region_alias"
            )
        ]
        ordering = ["alias_text"]
        verbose_name = "region alias"
        verbose_name_plural = "region aliases"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a concise canonical string (alias_text + arrow + region_id)."""
        return f"{self.alias_text} → {self.region.region_id}"

    def natural_key(self) -> tuple[str, str]:
        """Return the natural key for serialisation (region_id, alias_text)."""
        return (self.region.region_id, self.alias_text)

    natural_key.dependencies = ["regions.microregion"]  # type: ignore[attr-defined]


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

    def resorts(self) -> "ResortQuerySet":
        """Return only rows that are actually ski resorts (SNOW-544).

        Every surface that renders "a resort" — the map's resort layer,
        the region's resort list, the resort detail page — wants this,
        not the unfiltered table.
        """
        return self.filter(kind=Resort.Kind.RESORT)

    def touring(self) -> "ResortQuerySet":
        """Return only lift-less touring terrain (SNOW-544)."""
        return self.filter(kind=Resort.Kind.TOURING_TERRAIN)


class Resort(BaseModel):
    """
    A ski resort linked to an SLF avalanche warning region.

    Curated reference data; not populated by the data pipeline. Allows
    users to look up bulletins by well-known resort names (e.g.
    "Crans-Montana") rather than official region identifiers.

    Unlike the EAWS region models, these rows are **editable data owned by
    each environment's database** — no deploy reloads them (see
    ``docs/decisions/resorts-are-editable-data.md``). Three write paths:

    * the admin, for one-off corrections;
    * the edit-resorts mode on the public map (``?edit=resorts``, DEBUG
      only), which is what populates the geocoding fields;
    * ``manage.py import_resorts --commit``, which reconciles the table
      against the curated sheet at ``apps/regions/data/resorts.tsv``.

    ``apps/regions/fixtures/resorts.json`` seeds fresh local/CI databases only;
    run ``manage.py dump_resorts_fixture --commit`` to refresh it after a
    session of edits, or those edits reach no other worktree.

    ``forecast_point`` (SNOW-503) is the shared ``bulletins.ForecastPoint``
    a geocoded resort's coordinates resolve to, set by
    ``manage.py link_resort_forecast_points --commit``; it is what widens
    the point-weather polling set to cover resorts, not just favourites.
    ``on_delete=PROTECT`` mirrors ``Favourite.forecast_point`` — the point
    may be shared by other resorts/favourites and by weather-fetch
    bookkeeping.
    """

    GEOCODE_SOURCES = [
        ("manual", "Manual"),
        ("auto", "Auto"),
        ("import", "Import"),
    ]

    class Kind(models.TextChoices):
        """What a row actually is (SNOW-544).

        The sheet grew as one row per SLF micro-region with a
        representative place name typed into ``name``, so it accumulated
        entries that are real avalanche terrain but not resorts — high
        passes, side valleys, glacier basins with no lifts at all
        (Grimsel, Klausenpass, Zervreila, Val S-charl).

        They matter to a bulletin product and should not be deleted, but
        rendering them as resort pins is a lie. Before this field the
        sheet's only verdict was ``NOT_A_SKI_RESORT``, which means
        delete — there was no way to say "keep, but not as a resort".
        """

        RESORT = "RESORT", "Ski resort"
        TOURING_TERRAIN = "TOURING_TERRAIN", "Touring terrain"

    name = models.CharField(max_length=255)
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.RESORT,
        help_text=(
            "RESORT for a lift-served ski area; TOURING_TERRAIN for "
            "avalanche terrain with no lifts, which is kept for its "
            "bulletin relevance but excluded from every resort surface."
        ),
    )
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
    operator_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Operating company name, hand-curated (not from any feed).",
    )
    website = models.URLField(
        blank=True,
        help_text="Official resort website, hand-curated (not from any feed).",
    )
    num_lifts = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Number of lifts.",
    )
    num_runs = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Number of piste runs.",
    )
    total_piste_km = models.FloatField(
        null=True,
        blank=True,
        help_text="Total piste length in kilometres.",
    )
    base_elevation_m = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Base elevation in metres.",
    )
    top_elevation_m = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Top elevation in metres.",
    )
    typical_season_open = models.CharField(
        max_length=5,
        blank=True,
        validators=[MONTH_DAY_VALIDATOR],
        help_text="Typical season opening as month-day, e.g. 12-01.",
    )
    typical_season_close = models.CharField(
        max_length=5,
        blank=True,
        validators=[MONTH_DAY_VALIDATOR],
        help_text="Typical season closing as month-day, e.g. 04-30.",
    )
    forecast_point = models.ForeignKey(
        "bulletins.ForecastPoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resorts",
        help_text="Shared weather-sampling point; resolved once from lat/lon.",
    )

    objects = ResortQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["name"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a concise canonical string (name + region_id)."""
        return f"{self.name} ({self.region.region_id})"

    @property
    def name_slug(self) -> str:
        """Slugified resort name for the resort-page URL's second path component.

        Mirrors ``MicroRegion.name_slug`` — re-derived from ``self.name`` on
        every access rather than stored, so an edited resort name is
        reflected immediately without a migration/backfill step.
        """
        return slugify(self.name)

    def get_absolute_url(self) -> str:
        """Return the canonical resort-page URL (SNOW-504).

        Mirrors ``MicroRegion.get_absolute_url`` — ``/resorts/<id>/<slug>/``,
        always the primary key plus the name-derived slug, so callers and
        search engines see a single canonical URL per resort.
        """
        return reverse(
            "public:resort",
            kwargs={"resort_id": self.pk, "slug": self.name_slug},
        )
