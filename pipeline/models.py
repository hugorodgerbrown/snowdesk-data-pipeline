"""
pipeline/models.py — Database models for the pipeline application.

Defines a BaseModel abstract class and six concrete models:
  - PipelineRun: records each execution of the data pipeline (scheduled or
    manual), its status, and timing metadata.
  - Region: SLF avalanche warning regions (e.g. "CH-4115"), with an optional
    parent for broader region grouping.
  - Resort: ski resorts mapped to their SLF avalanche warning region.
  - Bulletin: stores SLF avalanche bulletins fetched from the CAAML API,
    keyed by bulletin_id. Includes a ``render_model`` JSONField (a
    versioned, presentation-ready view derived from ``raw_data``) and a
    ``render_model_version`` integer used to trigger incremental rebuilds
    when the builder logic changes.
  - RegionBulletin: many-to-many through table linking bulletins to regions.
  - RegionDayRating: denormalised per-(region, date) max danger rating,
    updated whenever a bulletin covering that (region, date) is ingested or
    rebuilt. Drives the longitudinal calendar view.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models — put it in pipeline/services/ instead.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as _date
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from pipeline.schema import AvalancheProblem, DangerRating

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseModel(models.Model):
    """
    Abstract base model providing standard fields for all concrete models.

    Provides a BigAutoField primary key, a mutable uuid4 field, and
    created_at / updated_at timestamps.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.__class__.__name__}({self.pk})"


# ---------------------------------------------------------------------------
# PipelineRun
# ---------------------------------------------------------------------------


class PipelineRunQuerySet(models.QuerySet):
    """Custom queryset for PipelineRun."""

    pass


class PipelineRun(BaseModel):
    """
    Represents a single execution of the data-fetching pipeline.

    Tracks whether the run succeeded or failed, how long it took, and how
    many records were created or updated.
    """

    class Status(models.TextChoices):
        """Possible states for a pipeline run."""

        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_failed = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Number of bulletins whose render model could not be built "
            "(stored with version=0 error sentinel)."
        ),
    )
    error_message = models.TextField(blank=True)
    triggered_by = models.CharField(
        max_length=64,
        default="unknown",
        help_text=(
            "Who or what triggered this run (e.g. 'scheduler', 'backfill', 'manual')."
        ),
    )

    objects = PipelineRunQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-started_at"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return (
            f"PipelineRun({self.pk}, {self.status}, {self.started_at:%Y-%m-%d %H:%M})"
        )

    def mark_running(self) -> None:
        """Transition the run to the RUNNING state and persist."""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status"])
        logger.info("PipelineRun %s started", self.pk)

    def mark_success(self, records_created: int, records_updated: int) -> None:
        """
        Transition the run to SUCCESS and record counts.

        Args:
            records_created: Number of new Bulletin rows created.
            records_updated: Number of existing Bulletin rows updated.

        """
        self.status = self.Status.SUCCESS
        self.finished_at = timezone.now()
        self.records_created = records_created
        self.records_updated = records_updated
        self.save(
            update_fields=[
                "status",
                "finished_at",
                "records_created",
                "records_updated",
            ]
        )
        logger.info(
            "PipelineRun %s succeeded: %d created, %d updated",
            self.pk,
            records_created,
            records_updated,
        )

    def mark_failed(self, error: Exception) -> None:
        """
        Transition the run to FAILED and store the error message.

        Args:
            error: The exception that caused the failure.

        """
        self.status = self.Status.FAILED
        self.finished_at = timezone.now()
        self.error_message = str(error)
        self.save(update_fields=["status", "finished_at", "error_message"])
        logger.error("PipelineRun %s failed: %s", self.pk, error, exc_info=True)

    @property
    def duration_seconds(self) -> float | None:
        """Return elapsed seconds, or None if the run has not finished."""
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------


class RegionQuerySet(models.QuerySet["Region"]):
    """Custom queryset for Region."""

    def get_by_natural_key(self, region_id: str) -> Region:
        """Look up a Region by its region_id for fixture deserialization."""
        return self.get(region_id=region_id)


class Region(BaseModel):
    """
    An SLF avalanche warning region (e.g. "CH-4115").

    Regions are created or looked up automatically when bulletins are
    processed.
    """

    region_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SLF region identifier, e.g. 'CH-4115'.",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
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


# ---------------------------------------------------------------------------
# Resort
# ---------------------------------------------------------------------------


class ResortQuerySet(models.QuerySet):
    """Custom queryset for Resort."""

    pass


class Resort(BaseModel):
    """
    A ski resort linked to an SLF avalanche warning region.

    Static reference data loaded from a fixture; not populated by the
    data pipeline. Allows users to look up bulletins by well-known resort
    names (e.g. "Crans-Montana") rather than official region identifiers.
    """

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

    objects = ResortQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["name"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.name} ({self.region.region_id})"


# ---------------------------------------------------------------------------
# Bulletin
# ---------------------------------------------------------------------------


class BulletinQuerySet(models.QuerySet["Bulletin"]):
    """Custom queryset for Bulletin."""

    def needs_render_model_rebuild(self, current_version: int) -> "BulletinQuerySet":
        """
        Return bulletins whose render_model_version is older than current_version.

        Args:
            current_version: The current RENDER_MODEL_VERSION constant from
                pipeline.services.render_model.

        Returns:
            A filtered queryset of stale Bulletin rows.

        """
        return self.filter(render_model_version__lt=current_version)

    def latest_valid_from_date(self) -> _date | None:
        """
        Return the ``valid_from`` day of the most recent stored bulletin.

        Used by ``fetch_bulletins`` to pick a gentle default start date so
        scheduled runs don't re-walk the full season on every invocation.
        Overlap is built in: using ``valid_from.date()`` means the same
        calendar day is re-fetched, so any earlier-in-day issues (morning
        update, prior evening re-issue) are picked up for free. The
        duplicates are ignored downstream — the optimisation is the smaller
        fetch, not the skipped upsert.

        Returns:
            The local-timezone ``valid_from`` day of the newest bulletin in
            this queryset, or ``None`` if the queryset is empty.

        """
        latest = self.aggregate(latest=models.Max("valid_from"))["latest"]
        if latest is None:
            return None
        return timezone.localtime(latest).date()


class Bulletin(BaseModel):
    """
    An SLF avalanche bulletin fetched from the CAAML API.

    Keyed by bulletin_id (unique). Use update_or_create when upserting so
    that re-runs are idempotent. Regions are linked via the RegionBulletin
    through table.
    """

    bulletin_id = models.CharField(max_length=255, unique=True, db_index=True)
    raw_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full CAAML bulletin wrapped in a GeoJSON Feature envelope.",
    )
    render_model = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Versioned, presentation-ready view of the bulletin built from "
            "raw_data. Shape: {version, danger, traits, fallback_key_message, "
            "snowpack_structure}. Rebuilt by upsert_bulletin and on demand by "
            "rebuild_render_models."
        ),
    )
    render_model_version = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Version of the render_model schema. 0 means not yet built.",
    )
    issued_at = models.DateTimeField(db_index=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    next_update = models.DateTimeField(null=True, blank=True)
    lang = models.CharField(max_length=8, default="en")
    unscheduled = models.BooleanField(default=False)
    regions: models.ManyToManyField[Region, RegionBulletin] = models.ManyToManyField(
        Region,
        through="RegionBulletin",
        related_name="bulletins",
        blank=True,
    )
    pipeline_run = models.ForeignKey(
        PipelineRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bulletins",
    )

    objects = BulletinQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-issued_at"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"Bulletin({self.bulletin_id}, {self.issued_at:%Y-%m-%d})"

    @property
    def _properties(self) -> dict:
        """Return the inner CAAML properties dict from the GeoJSON envelope."""
        return self.raw_data.get("properties", {}) if self.raw_data else {}

    def region_count(self) -> int:
        """Return the number of regions in the bulletin."""
        return len(self._properties.get("regions", []))

    def get_danger_ratings(self) -> list[DangerRating]:
        """
        Return the bulletin's ``dangerRatings`` as dataclass instances.

        Returns an empty list if the field is absent from the raw data.
        """
        return [
            DangerRating.from_dict(r) for r in self._properties.get("dangerRatings", [])
        ]

    def get_avalanche_problems(self) -> list[AvalancheProblem]:
        """
        Return the bulletin's ``avalancheProblems`` as dataclass instances.

        Returns an empty list if the field is absent from the raw data.
        """
        return [
            AvalancheProblem.from_dict(p)
            for p in self._properties.get("avalancheProblems", [])
        ]

    def highest_danger_rating(self) -> list[str]:
        """Return the highest rating 1..5."""
        return [r.main_value for r in self.get_danger_ratings()]


# ---------------------------------------------------------------------------
# RegionBulletin
# ---------------------------------------------------------------------------


class RegionBulletinQuerySet(models.QuerySet):
    """Custom queryset for RegionBulletin."""

    pass


class RegionBulletin(BaseModel):
    """
    Through table linking a Bulletin to a Region.

    Created automatically when a bulletin is processed. Stores the
    region name as it appeared in that specific bulletin (region names
    can theoretically change over time).
    """

    bulletin = models.ForeignKey(
        Bulletin,
        on_delete=models.CASCADE,
        related_name="region_links",
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        related_name="bulletin_links",
    )
    region_name_at_time = models.CharField(
        max_length=255,
        blank=True,
        help_text="Region name as it appeared in this bulletin.",
    )

    objects = RegionBulletinQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        unique_together = [("bulletin", "region")]
        ordering = ["region__region_id"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.bulletin.bulletin_id} ↔ {self.region.region_id}"


# ---------------------------------------------------------------------------
# RegionDayRating
# ---------------------------------------------------------------------------


class RegionDayRatingQuerySet(models.QuerySet["RegionDayRating"]):
    """Custom queryset for RegionDayRating."""

    def for_region_month(
        self, region: "Region", year: int, month: int
    ) -> "RegionDayRatingQuerySet":
        """
        Return all RegionDayRating rows for a region within a calendar month.

        Args:
            region: The Region to filter by.
            year: Calendar year (e.g. 2026).
            month: Calendar month as an integer 1–12.

        Returns:
            A filtered queryset covering the full calendar month.

        """
        import calendar

        last_day = calendar.monthrange(year, month)[1]
        return self.filter(
            region=region,
            date__gte=_date(year, month, 1),
            date__lte=_date(year, month, last_day),
        )


class RegionDayRating(BaseModel):
    """
    Denormalised per-(region, date) min and max danger ratings.

    One row per (region, calendar day) pair. Updated by the day_rating
    service whenever a bulletin covering the (region, date) is ingested or
    its render model is rebuilt. Drives the longitudinal calendar view.

    For each (region, day) we pick a single authoritative bulletin — the
    one with the latest ``valid_from`` among those whose target day equals
    this date (morning-of-day if present, else the prior day's evening
    issue). ``min_rating`` and ``max_rating`` are then derived from the
    traits *within* that single bulletin: the lowest and highest
    ``danger_level`` among its traits. If the bulletin has no traits
    (quiet day) both fall back to its headline ``danger.key``; if there is
    no qualifying bulletin at all both are set to ``NO_RATING``.

    When ``min_rating != max_rating`` the day is "variable" and the
    calendar tile renders a diagonal split fill.
    """

    class Rating(models.TextChoices):
        """Danger rating choices for the calendar view."""

        NO_RATING = "no_rating", "No rating"
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        CONSIDERABLE = "considerable", "Considerable"
        HIGH = "high", "High"
        VERY_HIGH = "very_high", "Very high"

    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        related_name="day_ratings",
    )
    date = models.DateField(db_index=True)
    min_rating = models.CharField(
        max_length=16,
        choices=Rating.choices,
        default=Rating.NO_RATING,
        help_text=(
            "Lowest danger rating across all qualifying bulletins for this day. "
            "Equals max_rating on uniform days; differs on variable days."
        ),
    )
    min_subdivision = models.CharField(
        max_length=2,
        blank=True,
        default="",
        help_text=(
            "Subdivision suffix ('+', '-', '=') from the bulletin that gave "
            "min_rating (latest valid_from on ties), or blank."
        ),
    )
    max_rating = models.CharField(
        max_length=16,
        choices=Rating.choices,
        default=Rating.NO_RATING,
    )
    max_subdivision = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text=(
            "Subdivision suffix ('+', '-', '=') from the source bulletin, or blank."
        ),
    )
    source_bulletin = models.ForeignKey(
        Bulletin,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="day_ratings",
        help_text="The bulletin that produced max_rating.",
    )
    version = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="DAY_RATING_VERSION at the time this row was computed.",
    )

    objects = RegionDayRatingQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        unique_together = [("region", "date")]
        ordering = ["-date", "region__region_id"]
        indexes = [
            models.Index(fields=["region", "date"]),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this day rating.

        Format for uniform days (min == max):
            ``CH-4115 2026-04-16 considerable+``
        Format for variable days (min != max):
            ``CH-4115 2026-04-16 moderate..considerable``
        """
        if self.min_rating != self.max_rating:
            return (
                f"{self.region.region_id} {self.date}"
                f" {self.min_rating}..{self.max_rating}"
            )
        suffix = self.max_subdivision or ""
        return f"{self.region.region_id} {self.date} {self.max_rating}{suffix}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
