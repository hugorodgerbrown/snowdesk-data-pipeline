"""
apps/bulletins/models.py — Bulletin-derived database models.

Owns the eleven bulletin-driven models:
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
    code, sunrise/sunset times, and daily temperature/snowfall totals
    fetched from Open-Meteo. Used by the render model (SNOW-98) to
    determine whether a day is daytime or night, and by the weather panel
    (SNOW-571) to show a hi/lo temperature and a snowfall total.
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
    ``apps.bulletins.services.forecast_points.resolve_forecast_point``
    (SNOW-412, phase 1 of the Favourites feature).
  - ForecastPointWeather: one row per (ForecastPoint, date) storing the
    comprehensive Open-Meteo daily forecast block for that point —
    the point analogue of WeatherSnapshot, but with a richer field set
    (temperature, precipitation, wind, UV) since a favourited point is a
    personal detail card rather than a bulletin-page header. Fetched by
    the ``fetch_weather`` management command's active-ForecastPoint pass
    (SNOW-416).
  - ForecastPointWeatherHistory: one row per (ForecastPoint, valid date,
    issue date) retaining how a forecast for a given day evolved as that
    day approached. ForecastPointWeather is upserted on (point, date) and
    so keeps only the final, day-of view; this table keeps the earlier
    ones, which are otherwise destroyed on every run (SNOW-575).

Region hierarchy (MicroRegion, MajorRegion, SubRegion, Resort) lives
in ``apps.regions.models`` — those are stable lookup tables shared across the
whole project, not bulletin-derived data.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models — put it in ``apps/bulletins/services/``
instead (lands in SNOW-93).
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from django.db import models
from django.db.models import CASCADE
from django.utils import timezone

from apps.bulletins.schema import AvalancheProblem, DangerRating
from apps.core.models import BaseModel

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

        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

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
                apps.bulletins.services.render_model.

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

    def for_target_date(self, day: _date) -> "BulletinQuerySet":
        """
        Return all bulletins whose ``target_date`` equals the supplied date.

        Args:
            day: The calendar date to filter by.

        Returns:
            A filtered queryset of Bulletin rows targeting that date.

        """
        return self.filter(target_date=day)


class Bulletin(BaseModel):
    """
    An SLF avalanche bulletin fetched from the CAAML API.

    Keyed by bulletin_id (unique). Use update_or_create when upserting so
    that re-runs are idempotent. Regions are linked via the RegionBulletin
    through table.
    """

    class Source(models.TextChoices):
        """The provider a bulletin was ingested from."""

        SLF = "SLF", "SLF (Switzerland)"
        ALBINA = "ALBINA", "ALBINA (Austria/South Tyrol/Trentino)"
        METEOFRANCE = "METEOFRANCE", "Météo-France (France)"

    bulletin_id = models.CharField(max_length=255, unique=True, db_index=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        blank=True,
        db_index=True,
        help_text=(
            "Provider this bulletin was ingested from, detected from "
            "raw_data.customData by detect_source. Blank only for rows "
            "predating the field and not yet covered by "
            "backfill_bulletin_source, or whose customData carries no known "
            "source marker."
        ),
    )
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
            "raw_data. Shape: {version, source, danger, traits, metadata, "
            "prose, danger_patterns} — see docs/render-model.md. Rebuilt by "
            "upsert_bulletin and on demand by rebuild_render_models."
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
    target_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Calendar date this bulletin is forecasting, as determined by "
            "target_day_for_valid_from() from valid_from. Populated by "
            "upsert_bulletin at ingest time and back-filled by the "
            "backfill_bulletin_target_dates management command."
        ),
    )
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

    def to_string(self) -> str:
        """Return a concise human-readable description of this bulletin.

        Format: ``Bulletin(<bulletin_id>, <issued_at date>)``
        """
        return f"Bulletin({self.bulletin_id}, {self.issued_at:%Y-%m-%d})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

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
            "Originating bulletin source (e.g. 'SLF', 'ALBINA', 'METEOFRANCE'). "
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
    model) can determine day/night state without re-calling the API, plus
    the daily hi/lo temperature and snowfall total consumed by the weather
    panel (SNOW-571).

    ``temperature_2m_max``, ``temperature_2m_min``, and ``snowfall_sum``
    mirror ``ForecastPointWeather``'s fields of the same name (same units:
    °C, °C, cm) — see
    ``docs/decisions/weather-snapshot-vs-forecast-point-weather.md`` for why
    the two models stay separate rather than merging. All three are
    nullable: existing rows keep them ``None`` until re-fetched, and the
    panel omits each individually rather than falling back to the
    no-weather state.

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
    temperature_2m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily air temperature at 2m, in °C.",
    )
    temperature_2m_min = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum daily air temperature at 2m, in °C.",
    )
    snowfall_sum = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daily snowfall, in cm.",
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
    ``apps.bulletins.services.grouping.compute_bulletin_grouping_boundary``:
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
    """Custom queryset for ForecastPoint."""

    def active(self) -> "ForecastPointQuerySet":
        """Return points referenced by at least one favourite or resort.

        Annotates over the reverse FKs created by ``favourites.Favourite``
        and ``regions.Resort`` (SNOW-503) and filters to rows with a
        non-zero count on either side — see
        ``docs/decisions/forecast-point-quantisation.md``. Each count is
        annotated with ``distinct=True`` so joining both reverse relations
        at once cannot inflate either count; a point shared by a favourite
        and a resort still appears exactly once in the result.

        Returns:
            Filtered queryset of ForecastPoints with one or more
            favourites or resorts.

        """
        return self.annotate(
            favourite_count=models.Count("favourites", distinct=True),
            resort_count=models.Count("resorts", distinct=True),
        ).filter(models.Q(favourite_count__gt=0) | models.Q(resort_count__gt=0))

    def inactive(self) -> "ForecastPointQuerySet":
        """Return points referenced by no favourite and no resort.

        The exact complement of ``active()`` — a point lands here when the
        last favourite or resort holding it goes away (SNOW-633). Such a
        point is already excluded from the ``fetch_weather`` point pass, so
        its stored weather can only go stale; ``prune_forecast_points``
        deletes it.

        Returns:
            Filtered queryset of ForecastPoints with no favourites and no
            resorts.

        """
        return self.annotate(
            favourite_count=models.Count("favourites", distinct=True),
            resort_count=models.Count("resorts", distinct=True),
        ).filter(favourite_count=0, resort_count=0)


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
    ``apps.bulletins.services.forecast_points.resolve_forecast_point``, which
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


# ---------------------------------------------------------------------------
# ForecastPointWeather
# ---------------------------------------------------------------------------


class ForecastPointWeatherQuerySet(models.QuerySet["ForecastPointWeather"]):
    """Custom queryset for ForecastPointWeather."""

    def for_date(self, target_date: _date) -> "ForecastPointWeatherQuerySet":
        """
        Return all rows valid for a given calendar date.

        Args:
            target_date: The calendar date to filter by.

        Returns:
            A filtered queryset of ForecastPointWeather rows for that date.

        """
        return self.filter(valid_for_date=target_date)

    def forecast_for_point(
        self, point: "ForecastPoint", start_date: _date
    ) -> "ForecastPointWeatherQuerySet":
        """
        Return the forward-looking forecast window for one point.

        Unlike the model's default ``-valid_for_date`` ordering, this is
        ascending — a multi-day forecast panel wants chronological order.

        Args:
            point: The ForecastPoint to filter by.
            start_date: The earliest calendar date to include (inclusive).

        Returns:
            A queryset of ForecastPointWeather rows for that point, from
            start_date onwards, ordered by valid_for_date ascending.

        """
        return self.filter(
            forecast_point=point, valid_for_date__gte=start_date
        ).order_by("valid_for_date")


class ForecastPointWeather(BaseModel):
    """
    Open-Meteo daily forecast data for one ForecastPoint on one calendar day.

    One row per (forecast_point, valid_for_date) pair. Fetched by the
    ``fetch_weather`` management command's active-ForecastPoint pass —
    the point analogue of ``WeatherSnapshot``, storing the comprehensive
    daily Open-Meteo block rather than just the WMO code and sunrise/sunset,
    since a favourited point is rendered as a personal detail card (SNOW-416).

    Open-Meteo omits some daily variables depending on the backing weather
    model (e.g. ``precipitation_probability_max``, ``uv_index_max``), so
    every field beyond the core trio is nullable.
    """

    forecast_point = models.ForeignKey(
        "bulletins.ForecastPoint",
        on_delete=CASCADE,
        related_name="weather_snapshots",
    )
    fetched_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this row was last written (updated on every upsert).",
    )
    valid_for_date = models.DateField(
        db_index=True,
        help_text="The calendar date this weather forecast applies to.",
    )

    # Core fields (required — same as WeatherSnapshot).
    weather_code = models.PositiveSmallIntegerField(
        help_text="WMO weather interpretation code (0–99).",
    )
    sunrise = models.DateTimeField(
        help_text=(
            "Sunrise time for this point on valid_for_date (tz-aware, local time)."
        ),
    )
    sunset = models.DateTimeField(
        help_text=(
            "Sunset time for this point on valid_for_date (tz-aware, local time)."
        ),
    )

    # Extended daily aggregates — nullable because Open-Meteo omits some of
    # these depending on the backing model.
    temperature_2m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily air temperature at 2m, in °C.",
    )
    temperature_2m_min = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum daily air temperature at 2m, in °C.",
    )
    apparent_temperature_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily apparent (feels-like) temperature, in °C.",
    )
    apparent_temperature_min = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum daily apparent (feels-like) temperature, in °C.",
    )
    precipitation_sum = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daily precipitation (rain + showers + snow), in mm.",
    )
    snowfall_sum = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daily snowfall, in cm.",
    )
    precipitation_probability_max = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Maximum daily precipitation probability, as a percentage.",
    )
    precipitation_hours = models.FloatField(
        null=True,
        blank=True,
        help_text="Number of hours with measurable precipitation, in hours.",
    )
    wind_speed_10m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily wind speed at 10m, in km/h.",
    )
    wind_gusts_10m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily wind gust speed at 10m, in km/h.",
    )
    wind_direction_10m_dominant = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Dominant daily wind direction at 10m, in degrees (0–360).",
    )
    uv_index_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily UV index (dimensionless).",
    )
    daylight_duration = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daylight duration, in seconds.",
    )
    sunshine_duration = models.FloatField(
        null=True,
        blank=True,
        help_text="Total sunshine duration, in seconds.",
    )
    freezing_level_height = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Daily maximum freezing level height, in metres. Open-Meteo has "
            "no daily freezing-level aggregate — this is derived as the "
            "maximum of the hourly freezing_level_height values for the day."
        ),
    )
    hourly_series = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Near-term hourly detail for this day, as a list of dicts with "
            "keys time, temperature_2m, snowfall, precipitation, "
            "wind_speed_10m, wind_gusts_10m, freezing_level_height. Only "
            "populated for the first POINT_HOURLY_DAYS rows of a fetch; "
            "None beyond, to keep the JSON payload bounded."
        ),
    )

    objects = ForecastPointWeatherQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        unique_together = [("forecast_point", "valid_for_date")]
        ordering = ["-valid_for_date", "forecast_point__id"]
        indexes = [
            models.Index(fields=["forecast_point", "valid_for_date"]),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``46.80000,7.50000 @1500m 2026-05-01 wmo=1``
        """
        return (
            f"{self.forecast_point.to_string()} "
            f"{self.valid_for_date} wmo={self.weather_code}"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# ForecastPointWeatherHistory
# ---------------------------------------------------------------------------


class ForecastPointWeatherHistoryQuerySet(
    models.QuerySet["ForecastPointWeatherHistory"]
):
    """Custom queryset for ForecastPointWeatherHistory."""

    def convergence_for(
        self, point: "ForecastPoint", valid_for_date: _date
    ) -> "ForecastPointWeatherHistoryQuerySet":
        """
        Return how the forecast for one date evolved, oldest issue first.

        Unlike the model's default ordering, this is ascending by
        ``issued_date`` — a convergence series wants to read from the
        earliest view of the day through to the day-of one, the same way
        ``ForecastPointWeather.objects.forecast_for_point`` inverts its
        model's default ordering for a forward-looking panel.

        Args:
            point: The ForecastPoint to filter by.
            valid_for_date: The forecast day whose history is wanted.

        Returns:
            A queryset of ForecastPointWeatherHistory rows for that
            (point, day), ordered by issued_date ascending.

        """
        return self.filter(
            forecast_point=point, valid_for_date=valid_for_date
        ).order_by("issued_date")


class ForecastPointWeatherHistory(BaseModel):
    """
    How the forecast for one ForecastPoint-day evolved as the day approached.

    ``ForecastPointWeather`` is upserted on ``(forecast_point,
    valid_for_date)``, so a given day is overwritten on every run and only
    the final, day-of forecast survives. That final row is the most
    accurate one, but the earlier views of the same day — issued three or
    six days out — are what show whether a forecast was stable or swung
    late, and they are destroyed as a side effect of the upsert.

    This table retains them: one row per ``(forecast_point,
    valid_for_date, issued_date)``. Because ``issued_date`` is part of the
    key, the four runs within a single day collapse to one row (the last
    run of that day wins), so a forecast day accrues one row per day of
    its window rather than one per run.

    Deliberately narrower than ``ForecastPointWeather``. ``hourly_series``
    is excluded — it is the bulk of the payload and is populated for only
    the first ``POINT_HOURLY_DAYS`` days of a window, so it cannot form a
    series across lead times. ``sunrise``/``sunset`` are excluded because
    they are astronomical: identical on every run for a given day, so they
    carry no convergence signal.

    Written by ``fetch_weather_for_point`` alongside its
    ``ForecastPointWeather`` upsert, inside the same transaction, so the
    pair lands together or not at all (SNOW-575).

    Rows accrue only from the moment this shipped — a past forecast cannot
    be reconstructed after the fact.
    """

    forecast_point = models.ForeignKey(
        "bulletins.ForecastPoint",
        on_delete=CASCADE,
        related_name="forecast_history",
    )
    fetched_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this row was last written (updated on every upsert).",
    )
    valid_for_date = models.DateField(
        db_index=True,
        help_text="The calendar date this forecast applies to.",
    )
    issued_date = models.DateField(
        help_text=(
            "The date this view of valid_for_date was fetched — the anchor "
            "date of the run that produced it."
        ),
    )
    lead_days = models.SmallIntegerField(
        help_text=(
            "valid_for_date - issued_date, in days. Denormalised from the "
            "two dates so that querying one lead time across many days "
            "(e.g. every three-day-out forecast) is indexable. Signed: a "
            "response whose first day precedes the run anchor yields a "
            "negative value, which is recorded rather than clamped."
        ),
    )

    # Payload — the scalars worth watching converge. weather_code is
    # required, mirroring ForecastPointWeather; the rest are nullable
    # because Open-Meteo omits some variables depending on the backing
    # weather model.
    weather_code = models.PositiveSmallIntegerField(
        help_text="WMO weather interpretation code (0–99).",
    )
    temperature_2m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily air temperature at 2m, in °C.",
    )
    temperature_2m_min = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum daily air temperature at 2m, in °C.",
    )
    precipitation_sum = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daily precipitation (rain + showers + snow), in mm.",
    )
    snowfall_sum = models.FloatField(
        null=True,
        blank=True,
        help_text="Total daily snowfall, in cm.",
    )
    wind_speed_10m_max = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum daily wind speed at 10m, in km/h.",
    )
    freezing_level_height = models.FloatField(
        null=True,
        blank=True,
        help_text="Daily maximum freezing level height, in metres.",
    )

    objects = ForecastPointWeatherHistoryQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        verbose_name_plural = "forecast point weather history"
        unique_together = [("forecast_point", "valid_for_date", "issued_date")]
        ordering = ["-valid_for_date", "issued_date"]
        indexes = [
            models.Index(fields=["forecast_point", "valid_for_date"]),
            models.Index(fields=["valid_for_date", "lead_days"]),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``46.80000,7.50000 @1500m 2026-05-01 issued 2026-04-28
        (+3d) wmo=1``
        """
        return (
            f"{self.forecast_point.to_string()} "
            f"{self.valid_for_date} issued {self.issued_date} "
            f"({self.lead_days:+d}d) wmo={self.weather_code}"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
