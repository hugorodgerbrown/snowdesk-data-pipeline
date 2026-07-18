"""
bulletins/models.py — Bulletin-derived database models.

Owns the eight bulletin-driven models:
  - PipelineRun: records each execution of the data pipeline (scheduled or
    manual), its status, and timing metadata.
  - Bulletin: stores SLF avalanche bulletins fetched from the CAAML API,
    keyed by ``bulletin_id``. Includes a ``render_model`` JSONField (a
    versioned, presentation-ready view derived from ``raw_data``) and a
    ``render_model_version`` integer used to trigger incremental rebuilds
    when the builder logic changes.
  - RegionBulletin: many-to-many through table linking bulletins to
    ``regions.MicroRegion`` rows.
  - RegionDayRating: denormalised per-(region, date) min and max danger
    ratings, updated whenever a bulletin covering that (region, date) is
    ingested or rebuilt. Drives the longitudinal calendar view.
  - WeatherSnapshot: one row per (region, date) storing the WMO weather
    code and sunrise/sunset times fetched from Open-Meteo. Used by the
    render model (SNOW-98) to determine whether a day is daytime or night.
  - BulletinShare: a tokenised short-URL share link for a bulletin page.
    Stores (region, target_date, token, bulletin) so the redirect can
    always recover the canonical destination even if the bulletin changes.
  - BulletinShareClick: one row per follow of a BulletinShare link.
    Captures client metadata (IP, UA, session, Referer, Sec-Purpose,
    country_code, visitor_hash) for share-click analytics (SNOW-217).
  - BulletinGrouping: dissolved outer boundary of all L4 micro-regions
    sharing a bulletin on a given day, computed at ingest time. Provides
    the "L3" dynamic overlay on the map (SNOW-323).
  - ForecastPoint: canonical weather-sampling location that many nearby
    map pins snap to, keyed on a quantised lat/lon grid cell and an
    elevation band. Resolved by
    ``bulletins.services.forecast_points.resolve_forecast_point``
    (SNOW-412, phase 1 of the Favourites feature).

Region hierarchy (MicroRegion, MajorRegion, SubRegion, Resort) lives
in ``regions.models`` — those are stable lookup tables shared across the
whole project, not bulletin-derived data.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models — put it in ``bulletins/services/``
instead (lands in SNOW-93).
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from django.db import models
from django.db.models import CASCADE
from django.utils import timezone

from bulletins.schema import AvalancheProblem, DangerRating
from core.models import BaseModel

logger = logging.getLogger(__name__)


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
        logger.exception("PipelineRun %s failed: %s", self.pk, error)

    @property
    def duration_seconds(self) -> float | None:
        """Return elapsed seconds, or None if the run has not finished."""
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


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
                bulletins.services.render_model.

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

    def earliest_valid_to_date(self) -> _date | None:
        """
        Return the ``valid_to`` day of the oldest stored bulletin.

        Used by ``_get_nav_dates`` to establish the lower bound of
        backward day navigation.  Once the user reaches a day on or before
        the oldest bulletin's ``valid_to`` date, no earlier ``prev_date`` is
        offered.

        Returns:
            The local-timezone ``valid_to`` day of the oldest bulletin in
            this queryset, or ``None`` if the queryset is empty.

        """
        earliest = self.aggregate(earliest=models.Min("valid_to"))["earliest"]
        if earliest is None:
            return None
        return timezone.localtime(earliest).date()

    def earliest_valid_from_date(self) -> _date | None:
        """
        Return the ``valid_from`` day of the oldest stored bulletin.

        Used by ``fetch_weather`` to derive a sensible default start date
        when no ``WeatherSnapshot`` rows exist yet — fetching weather from
        the earliest bulletin date ensures the data covers every bulletin
        already in the DB.

        Returns:
            The local-timezone ``valid_from`` day of the oldest bulletin in
            this queryset, or ``None`` if the queryset is empty.

        """
        earliest = self.aggregate(earliest=models.Min("valid_from"))["earliest"]
        if earliest is None:
            return None
        return timezone.localtime(earliest).date()


class Bulletin(BaseModel):
    """
    An SLF avalanche bulletin fetched from the CAAML API.

    Keyed by bulletin_id (unique). Use update_or_create when upserting so
    that re-runs are idempotent. Regions are linked via the RegionBulletin
    through table.
    """

    class Source(models.TextChoices):
        """Possible originating sources for a bulletin's render model."""

        SLF = "slf", "SLF (Switzerland)"
        ALBINA = "albina", "ALBINA (Austria/South Tyrol/Trentino)"
        METEOFRANCE = "meteofrance", "Météo-France (France)"

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
    pdf_url = models.URLField(
        blank=True,
        default="",
        max_length=500,
        help_text=(
            "URL of the source bulletin PDF (SLF / ALBINA / Météo-France). "
            "Populated by upsert_bulletin at ingest time and back-filled by "
            "the backfill_pdf_urls management command."
        ),
    )
    regions: models.ManyToManyField = models.ManyToManyField(
        "regions.MicroRegion",
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
    Through table linking a Bulletin to a ``regions.MicroRegion``.

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
        "regions.MicroRegion",
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
        self, region: Any, year: int, month: int
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

    def for_region_range(
        self, region: Any, start: _date, end: _date
    ) -> "RegionDayRatingQuerySet":
        """
        Return all RegionDayRating rows for a region within an inclusive date range.

        Args:
            region: The Region to filter by.
            start: First date to include (inclusive).
            end: Last date to include (inclusive).

        Returns:
            A filtered queryset covering ``[start, end]``.

        """
        return self.filter(region=region, date__gte=start, date__lte=end)

    def season_date_bounds(
        self, start: _date, end: _date
    ) -> tuple[_date | None, _date | None]:
        """
        Return (min_date, max_date) of rows whose date falls inside [start, end].

        Aggregates across all regions — callers use this to find the
        data-driven edges of the season window regardless of which region
        each row belongs to.

        Both values are None when no rows exist for the season, and callers
        should fall back to the calendar window in that case.

        Args:
            start: Season window start date (inclusive).
            end: Season window end date (inclusive).

        Returns:
            A ``(min_date, max_date)`` tuple, or ``(None, None)`` if the
            queryset contains no rows within ``[start, end]``.

        """
        agg = self.filter(date__gte=start, date__lte=end).aggregate(
            first=models.Min("date"), last=models.Max("date")
        )
        return agg["first"], agg["last"]


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
        "regions.MicroRegion",
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
    # SNOW-291: AM/PM split fields — populated only when the bulletin carries
    # both a morning (all_day/earlier) and an afternoon (later) period.  Both
    # are null when the day has no time split (uniform day), keeping the
    # existing min/max diagonal-tile behaviour unchanged for those rows.
    am_rating = models.CharField(
        max_length=16,
        choices=Rating.choices,
        null=True,
        blank=True,
        help_text=(
            "Danger rating for the morning (all_day/earlier) period on split "
            "days. Null on uniform days."
        ),
    )
    am_subdivision = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text=("Subdivision suffix for the AM period, or blank."),
    )
    pm_rating = models.CharField(
        max_length=16,
        choices=Rating.choices,
        null=True,
        blank=True,
        help_text=(
            "Danger rating for the afternoon (later) period on split days. "
            "Null on uniform days."
        ),
    )
    pm_subdivision = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text=("Subdivision suffix for the PM period, or blank."),
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
    source = models.CharField(
        max_length=16,
        choices=Bulletin.Source.choices,
        blank=True,
        default="",
        help_text=(
            "Originating bulletin source (e.g. 'slf', 'albina', 'meteofrance'). "
            "Blank for no-rating rows where source_bulletin is None."
        ),
    )
    bands = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Elevation-band breakdown for ALBINA bulletins. Each entry is "
            "{'band_id': str, 'label': str, 'rating_key': str, 'time_period': str}. "
            "None for SLF, MeteoFrance, and no-rating rows."
        ),
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


# ---------------------------------------------------------------------------
# WeatherSnapshot
# ---------------------------------------------------------------------------


class WeatherSnapshotQuerySet(models.QuerySet["WeatherSnapshot"]):
    """Custom queryset for WeatherSnapshot."""

    def for_date(self, target_date: _date) -> "WeatherSnapshotQuerySet":
        """
        Return all snapshots valid for a given calendar date.

        Args:
            target_date: The calendar date to filter by.

        Returns:
            A filtered queryset of WeatherSnapshot rows for that date.

        """
        return self.filter(valid_for_date=target_date)

    def latest_date(self) -> _date | None:
        """
        Return the most recent ``valid_for_date`` across all snapshots.

        Used by ``fetch_weather`` to derive a sensible default start date
        when the DB already has some weather data — fetching from the
        latest snapshot date avoids redundant re-fetches of the full
        season.

        Returns:
            The maximum ``valid_for_date`` in this queryset, or ``None``
            if the queryset is empty.

        """
        result: _date | None = self.aggregate(latest=models.Max("valid_for_date"))[
            "latest"
        ]
        return result


class WeatherSnapshot(BaseModel):
    """
    Open-Meteo weather data for one region on one calendar day.

    One row per (region, valid_for_date) pair. Fetched by the
    ``fetch_weather`` management command (forecast or historical range).
    Stores the WMO weather code and tz-aware
    sunrise/sunset times so that downstream consumers (SNOW-98 render
    model) can determine day/night state without re-calling the API.

    ``is_day`` is intentionally NOT stored here — it is computed at render
    time by the consumer (SNOW-98) because it depends on the display
    timestamp, not the snapshot.
    """

    region = models.ForeignKey(
        "regions.MicroRegion",
        on_delete=CASCADE,
        related_name="weather_snapshots",
    )
    fetched_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this snapshot was last written (updated on every upsert).",
    )
    valid_for_date = models.DateField(
        db_index=True,
        help_text="The calendar date this weather observation/forecast applies to.",
    )
    weather_code = models.PositiveSmallIntegerField(
        help_text="WMO weather interpretation code (0–99).",
    )
    sunrise = models.DateTimeField(
        help_text=(
            "Sunrise time for this region on valid_for_date (tz-aware, local time)."
        ),
    )
    sunset = models.DateTimeField(
        help_text=(
            "Sunset time for this region on valid_for_date (tz-aware, local time)."
        ),
    )

    objects = WeatherSnapshotQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        unique_together = [("region", "valid_for_date")]
        ordering = ["-valid_for_date", "region__region_id"]
        indexes = [
            models.Index(fields=["region", "valid_for_date"]),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this snapshot.

        Format: ``CH-4115 2026-05-01 wmo=1``
        """
        return f"{self.region.region_id} {self.valid_for_date} wmo={self.weather_code}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# BulletinShare
# ---------------------------------------------------------------------------


class BulletinShareQuerySet(models.QuerySet["BulletinShare"]):
    """Custom queryset for BulletinShare."""

    pass


class BulletinShare(BaseModel):
    """A tokenised share link for a bulletin page.

    Created when a user taps the share button on a bulletin page. Stores the
    (region, target_date) the sharer was viewing so the redirect always lands
    on the correct page regardless of later bulletin re-issues.

    ``bulletin`` is nullable (SET_NULL) so the share row and its click data
    are preserved even if the source bulletin is deleted or replaced.
    ``region`` and ``target_date`` are the canonical identifiers used to
    reconstruct the canonical URL on redirect.

    ``token`` is generated via ``secrets.token_urlsafe(8)`` at creation
    time — 11 URL-safe chars giving ~66 bits of entropy, sufficient for
    click-tracking without guessing.
    """

    token = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="URL-safe random token used in the /s/<token>/ short URL.",
    )
    bulletin = models.ForeignKey(
        Bulletin,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
        help_text=(
            "The bulletin that was active when this share was created. "
            "Nulled when the bulletin is deleted; the share row is preserved."
        ),
    )
    region = models.ForeignKey(
        "regions.MicroRegion",
        on_delete=models.CASCADE,
        related_name="bulletin_shares",
        help_text="The region the sharer was viewing.",
    )
    target_date = models.DateField(
        help_text="The calendar date the sharer was viewing.",
    )

    objects = BulletinShareQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this share.

        Format: ``BulletinShare(<token>, <region.region_id>, <target_date>)``
        """
        region_id = self.region.region_id
        return f"BulletinShare({self.token}, {region_id}, {self.target_date})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# BulletinShareClick
# ---------------------------------------------------------------------------


class BulletinShareClickQuerySet(models.QuerySet["BulletinShareClick"]):
    """Custom queryset for BulletinShareClick."""

    pass


class BulletinShareClick(BaseModel):
    """A single click (follow) of a BulletinShare short URL.

    One row per visitor follow of a ``/s/<token>/`` link. All request
    context (IP, UA, session, Referer, Sec-Purpose, country) is stored on
    the linked ``RequestLog`` row rather than inline.

    ``visitor_hash`` is the first 16 hex chars of
    ``sha256((ip + "|" + ua).encode()).hexdigest()``. It is a privacy-
    respecting pseudonymous identifier — not reversible to a real IP by
    any party that doesn't already have the IP.

    No bot filtering is applied at write time. Filter on
    ``request__user_agent`` patterns or ``request__sec_purpose`` (the
    ``Sec-Purpose`` header stored on ``RequestLog``) at query time.
    """

    share = models.ForeignKey(
        BulletinShare,
        on_delete=models.CASCADE,
        related_name="clicks",
        help_text="The share link that was followed.",
    )
    request = models.ForeignKey(
        "core.RequestLog",
        on_delete=models.PROTECT,
        related_name="bulletin_share_clicks",
        help_text="Request context captured when this link was followed.",
    )
    visitor_hash = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text=(
            "First 16 hex chars of sha256(ip + '|' + ua). Pseudonymous "
            "cross-visit identifier; not reversible without the original IP."
        ),
    )

    objects = BulletinShareClickQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this click.

        Format: ``BulletinShareClick(<share_token>, <country_code>)``
        """
        return f"BulletinShareClick({self.share.token}, {self.request.country_code!r})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# BulletinGrouping (SNOW-323)
# ---------------------------------------------------------------------------


class BulletinGroupingQuerySet(models.QuerySet["BulletinGrouping"]):
    """Custom queryset for BulletinGrouping."""

    def for_date(self, target_date: _date) -> "BulletinGroupingQuerySet":
        """
        Return all groupings whose target_date equals the supplied date.

        Args:
            target_date: The calendar date to filter by.

        Returns:
            A filtered queryset of BulletinGrouping rows for that date.

        """
        return self.filter(target_date=target_date)


class BulletinGrouping(BaseModel):
    """
    Dissolved outer boundary of the micro-regions sharing a bulletin.

    One row per bulletin. Computed at ingest time by
    ``bulletins.services.grouping.compute_bulletin_grouping_boundary``:
    the micro-regions linked to the bulletin via ``RegionBulletin`` that
    carry a ``boundary`` are dissolved into a single GeoJSON
    Polygon/MultiPolygon using Shapely's ``unary_union``. The result is
    stored here so the ``/api/bulletin-groupings.geojson`` endpoint can
    serve a date-keyed FeatureCollection without touching Shapely at
    request time.

    ``countries`` is a sorted JSON list of ISO-2 country codes (e.g.
    ``["AT", "IT"]``) derived from the linked regions' parent
    ``MajorRegion.country``. A bulletin that spans two countries produces
    a two-element list; a single-country bulletin produces a one-element
    list.

    The relationship is ``OneToOne`` because each bulletin dissolves to
    exactly one polygon (or is absent when no boundaried regions are
    linked). Re-ingest is idempotent via ``update_or_create``.
    """

    bulletin = models.OneToOneField(
        Bulletin,
        on_delete=CASCADE,
        related_name="grouping",
        help_text="The bulletin whose linked micro-regions were dissolved.",
    )
    target_date = models.DateField(
        db_index=True,
        help_text=(
            "Calendar date this bulletin is forecasting, as determined by "
            "the same morning/evening boundary rule used by the day-rating service."
        ),
    )
    boundary = models.JSONField(
        help_text=(
            "Dissolved GeoJSON geometry (Polygon or MultiPolygon) covering "
            "all micro-regions linked to the bulletin that carry a boundary."
        ),
    )
    countries = models.JSONField(
        default=list,
        help_text=(
            "Sorted list of ISO-2 country codes touched by the bulletin's regions "
            "(e.g. ['AT', 'IT'] for an ALBINA cross-border bulletin)."
        ),
    )

    objects = BulletinGroupingQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-target_date"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this grouping.

        Format: ``BulletinGrouping(<bulletin_id>, <target_date>, <countries>)``
        """
        return (
            f"BulletinGrouping({self.bulletin.bulletin_id},"
            f" {self.target_date}, {self.countries})"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# ForecastPoint
# ---------------------------------------------------------------------------


class ForecastPointQuerySet(models.QuerySet["ForecastPoint"]):
    """
    Custom queryset for ForecastPoint.

    Deliberately empty of domain methods for now. A future ``active()``
    method (annotating over the reverse FK from the not-yet-built
    ``favourites.Favourite`` model) is deferred to the ticket that
    introduces that app — see
    ``docs/decisions/forecast-point-quantisation.md``.
    """


class ForecastPoint(BaseModel):
    """
    A canonical weather-sampling location shared by nearby map pins.

    Many user pins that land close together (horizontally and in
    elevation) should reuse the same Open-Meteo forecast fetch rather than
    each triggering its own. To make "close together" a stable, indexable
    concept, every pin is quantised onto a coarse lat/lon grid cell plus
    an elevation band, and one ``ForecastPoint`` row is kept per distinct
    (lat_cell, lon_cell, elevation_band) triple.

    Rows are created and reused by
    ``bulletins.services.forecast_points.resolve_forecast_point``, which
    also decides whether a pin should reuse an existing nearby point (via
    haversine distance and elevation difference) or mint a new one. See
    ``docs/decisions/forecast-point-quantisation.md`` for the grid sizing
    rationale.

    ``latitude``, ``longitude``, and ``elevation`` are the *representative*
    values captured from the pin that created (or last matched) this row —
    not the cell's geometric centre.
    """

    lat_cell = models.IntegerField(
        help_text="floor(latitude / LAT_CELL_SIZE) — see forecast_points.py.",
    )
    lon_cell = models.IntegerField(
        help_text="floor(longitude / LON_CELL_SIZE) — see forecast_points.py.",
    )
    elevation_band = models.IntegerField(
        help_text="floor(elevation / ELEVATION_BAND_SIZE) — see forecast_points.py.",
    )
    latitude = models.FloatField(
        help_text="Representative latitude of the pin that resolved to this point.",
    )
    longitude = models.FloatField(
        help_text="Representative longitude of the pin that resolved to this point.",
    )
    elevation = models.FloatField(
        help_text="Representative elevation in metres, from the Open-Meteo lookup.",
    )

    objects = ForecastPointQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        unique_together = [("lat_cell", "lon_cell", "elevation_band")]

    def to_string(self) -> str:
        """Return a concise human-readable description of this point.

        Format: ``46.80000,7.50000 @1500m``
        """
        return f"{self.latitude:.5f},{self.longitude:.5f} @{self.elevation:.0f}m"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
