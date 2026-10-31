"""
apps/bulletins/models.py — Bulletin-derived database models.

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
  - BulletinShare: a tokenised short-URL share link for a bulletin page.
    Stores (region, target_date, token, bulletin) so the redirect can
    always recover the canonical destination even if the bulletin changes.
  - BulletinShareClick: one row per follow of a BulletinShare link.
    Captures client metadata (IP, UA, session, Referer, Sec-Purpose,
    country_code, visitor_hash) for share-click analytics (SNOW-217).
  - BulletinGrouping: dissolved outer boundary of all L4 micro-regions
    sharing a bulletin on a given day, computed at ingest time. Provides
    the "L3" dynamic overlay on the map (SNOW-323).
  - BulletinAlert: the bulletin-alert send log (SNOW-942) — one row per
    state an account was told, or deliberately not told, about.

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
from django.db.models import CASCADE, Count, Q
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
        # SNOW-1228: finished, but some records failed (a render model, a day
        # rating or a grouping outline) — see mark_finished.
        PARTIAL = "PARTIAL", "Partial"
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
            "Number of records that failed during the run: a bulletin that "
            "could not be stored or whose render model could not be built "
            "(stored with a version=0 error sentinel), a day rating that "
            "could not be recomputed, or a grouping outline that could not "
            "be recomputed. Non-zero finishes the run as PARTIAL."
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

    def to_string(self) -> str:
        """Return a concise human-readable description of this run.

        Format: ``PipelineRun(<pk>, <status>, <YYYY-MM-DD HH:MM>)``
        """
        return (
            f"PipelineRun({self.pk}, {self.status}, {self.started_at:%Y-%m-%d %H:%M})"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def mark_running(self) -> None:
        """Transition the run to the RUNNING state and persist."""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status"])
        logger.info("PipelineRun %s started", self.pk)

    def mark_success(self, records_created: int, records_updated: int) -> None:
        """
        Transition the run to SUCCESS and record counts.

        For a caller that records no per-record failures. One that does —
        anything that goes through ``upsert_bulletin`` — finishes with
        ``mark_finished`` instead, so a run with failures is not filed as a
        success.

        Args:
            records_created: Number of new Bulletin rows created.
            records_updated: Number of existing Bulletin rows updated.

        """
        self._finish(self.Status.SUCCESS, records_created, records_updated)

    def mark_finished(self, records_created: int, records_updated: int) -> None:
        """
        Finish the run as PARTIAL when any record failed, else as SUCCESS.

        SNOW-1228: a run that completed with ``records_failed > 0`` used to
        be stored as SUCCESS, so anything reading the status alone (the ops
        ``ingest_status`` tool, the admin list filter) saw a clean run.
        PARTIAL is a terminal state of its own: the run finished, the
        failures are counted, and it is neither a success nor a FAILED run
        (which means the pipeline itself raised).

        Args:
            records_created: Number of new Bulletin rows created.
            records_updated: Number of existing Bulletin rows updated.

        """
        status = self.Status.PARTIAL if self.records_failed else self.Status.SUCCESS
        self._finish(status, records_created, records_updated)

    def _finish(
        self, status: "PipelineRun.Status", records_created: int, records_updated: int
    ) -> None:
        """
        Persist a finished run's terminal status and counts, and log it.

        Args:
            status: SUCCESS or PARTIAL.
            records_created: Number of new Bulletin rows created.
            records_updated: Number of existing Bulletin rows updated.

        """
        self.status = status
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
        if status == self.Status.PARTIAL:
            logger.warning(
                "PipelineRun %s finished with failures: %d created, %d updated, "
                "%d failed",
                self.pk,
                records_created,
                records_updated,
                self.records_failed,
            )
        else:
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

    def earliest_valid_from_date(self) -> _date | None:
        """
        Return the ``valid_from`` day of the oldest stored bulletin.

        Used to derive a sensible default start date for a backfill that
        needs to cover every bulletin already in the DB.

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
        indexes = [
            # The data feed's keyset (SNOW-1197): every page is a range on
            # (updated_at, id) in that order, so a page is an index walk
            # rather than a scan and sort of every bulletin's CAAML.
            models.Index(fields=["updated_at", "id"], name="bulletin_feed_keyset"),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this bulletin.

        Format: ``Bulletin(<bulletin_id>, <issued_at date>)``
        """
        return f"Bulletin({self.bulletin_id}, {self.issued_at:%Y-%m-%d})"

    def row_label(self) -> str:
        """Return the label a management command prints beside this row's id.

        Format: ``<source> <day> <bulletin_id>``, e.g.
        ``SLF 2026-01-20 6a1f…``. The provider and the day the bulletin
        forecasts are what an operator recognises; the provider's own id
        lets them find the bulletin upstream. Reads only this row's own
        columns, so it costs no query. For a row with no ``target_date``
        yet, ``day`` is derived from ``valid_from`` by the same rule as
        ``target_day_for_valid_from`` — an issue from noon onwards forecasts
        the next day — so the label matches the day a backfill would store.
        ``source`` prints ``-`` for a row whose provider is not yet detected,
        so the line keeps three columns.
        """
        from apps.bulletins.services.day_rating import (  # noqa: PLC0415 — services.day_rating imports this module at load time
            target_day_for_valid_from,
        )

        day = self.target_date or target_day_for_valid_from(self.valid_from)
        return f"{self.source or '-'} {day} {self.bulletin_id}"

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

    def to_string(self) -> str:
        """Return a concise human-readable description of this link.

        Format: ``<bulletin_id> ↔ <region_id>``
        """
        return f"{self.bulletin.bulletin_id} ↔ {self.region.region_id}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


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

    def earliest_date(self, region: Any) -> _date | None:
        """
        Return the earliest rated day for *region*, or None if it has none.

        The lower bound of the bulletin page's backward day navigation
        (``_get_nav_dates``): one aggregate over this table, with no join
        through the bulletin M2M (SNOW-1344).

        Args:
            region: The MicroRegion to look up.

        Returns:
            The earliest ``date`` among the region's rows, or None.

        """
        earliest: _date | None = self.filter(region=region).aggregate(
            earliest=models.Min("date")
        )["earliest"]
        return earliest


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

    ``request`` is ``CASCADE``, not ``PROTECT`` (SNOW-774). Every field
    this row would be read for lives on the ``RequestLog`` — the class
    docstring above says so — and ``visitor_hash`` is derived from that
    request's IP and user agent, so a click whose request context has gone
    is both empty and, for the person it described, undeletable evidence.
    ``PROTECT`` made two things impossible: an account holder who had
    followed any share link could not delete their account (the cascade
    raised ``ProtectedError`` and the request 500'd), and
    ``purge_request_logs`` would abort the moment a click row aged past the
    retention window. Both failed closed on the privacy-preserving action,
    which is the wrong way round.
    """

    share = models.ForeignKey(
        BulletinShare,
        on_delete=models.CASCADE,
        related_name="clicks",
        help_text="The share link that was followed.",
    )
    request = models.ForeignKey(
        "core.RequestLog",
        on_delete=models.CASCADE,
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


# A grouping only says something the micro-region layer does not when it
# dissolves two or more boundaried regions (SNOW-1001; see
# docs/decisions/a-grouping-outline-asserts-an-aggregation.md). This is the
# single definition of "worth a row" — the ingest-time guard in
# ``apps.bulletins.services.grouping``, ``BulletinGroupingQuerySet.degenerate``
# below, and ``candidate_bulletins`` in ``backfill_bulletin_groupings`` all
# read it, so writer, backfill and purge cannot drift apart.
MIN_GROUPED_REGIONS = 2


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

    def degenerate(self) -> "BulletinGroupingQuerySet":
        """
        Return groupings that draw nothing the micro-region layer does not.

        Annotates each row with the number of BOUNDARIED micro-regions linked
        to its bulletin — the same population
        ``compute_bulletin_grouping_boundary`` dissolves — and keeps those
        below ``MIN_GROUPED_REGIONS``. A bulletin carrying three regions of
        which one has a boundary dissolves to that one polygon and is just as
        degenerate as a single-region bulletin, which is why the count is
        filtered rather than taken over every link.

        Rows selected here were written before the ingest-time guard landed;
        ``purge_degenerate_bulletin_groupings`` deletes them. The exact
        complement of ``candidate_bulletins`` in
        ``backfill_bulletin_groupings``, which
        ``tests/bulletins/test_bulletin_grouping_model.py`` pins.

        Returns:
            A filtered queryset of degenerate BulletinGrouping rows.

        """
        return self.annotate(
            boundaried_region_count=Count(
                "bulletin__regions",
                filter=Q(bulletin__regions__boundary__isnull=False),
            )
        ).filter(boundaried_region_count__lt=MIN_GROUPED_REGIONS)


class BulletinGrouping(BaseModel):
    """
    Dissolved outer boundary of the micro-regions sharing a bulletin.

    At most one row per bulletin, and only where the provider aggregated:
    a row exists when the bulletin links ``MIN_GROUPED_REGIONS`` or more
    micro-regions carrying a ``boundary``. Computed at ingest time by
    ``apps.bulletins.services.grouping.compute_bulletin_grouping_boundary``,
    which dissolves those boundaries into a single GeoJSON
    Polygon/MultiPolygon using Shapely's ``unary_union``. The result is
    stored here so the ``/api/bulletin-groupings.geojson`` endpoint can
    serve a date-keyed FeatureCollection without touching Shapely at
    request time.

    A bulletin covering one boundaried region gets no row (SNOW-1001): the
    dissolve would be a union of one polygon, so the layer would draw a
    line directly on top of ``regions-line`` and claim an aggregation that
    did not happen. Météo-France is 1:1 across its whole archive and writes
    none at all; SLF will join it once SNOW-998 lands. Full rationale:
    docs/decisions/a-grouping-outline-asserts-an-aggregation.md.

    ``countries`` is a sorted JSON list of ISO-2 country codes (e.g.
    ``["AT", "IT"]``) derived from the linked regions' parent
    ``MajorRegion.country``. A bulletin that spans two countries produces
    a two-element list; a single-country bulletin produces a one-element
    list.

    The relationship is ``OneToOne`` because each bulletin dissolves to
    exactly one polygon (or is absent when it links fewer than
    ``MIN_GROUPED_REGIONS`` boundaried regions). Re-ingest is idempotent
    via ``update_or_create``.
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
# BulletinAlert
# ---------------------------------------------------------------------------


class BulletinAlertQuerySet(models.QuerySet["BulletinAlert"]):
    """Custom queryset for BulletinAlert."""

    def for_day(
        self, account_id: int, region_id: int, target_date: _date
    ) -> "BulletinAlertQuerySet":
        """
        Return the rows for one account, region and day, latest first.

        Ordered by ``updated_at``: a state that recurs (A, then B, then A
        again) rewrites its original row, which must then read as the
        latest, or every run would compare A against B and resend it.

        Args:
            account_id: The account's pk.
            region_id: The MicroRegion's pk.
            target_date: The day the bulletin forecasts.

        Returns:
            The filtered queryset.

        """
        return self.filter(
            account_id=account_id, region_id=region_id, target_date=target_date
        ).order_by("-updated_at", "-id")

    def sent(self) -> "BulletinAlertQuerySet":
        """Return the rows that reached the account (push or email)."""
        return self.filter(outcome=BulletinAlert.Outcome.SENT)


class BulletinAlert(BaseModel):
    """
    The send log for bulletin alerts (SNOW-942).

    One row per state an account was told about, or deliberately not told
    about, for one region and day.

    The row's ``state`` is the "previous" side of the next comparison (A1's
    ``BulletinState.to_json``), so the send log and the change signal cannot
    disagree. ``fingerprint`` is the idempotency key: the same state for the
    same account, region and day is never written, or sent, twice.

    ``outcome`` records why the row exists:

    - ``PENDING``: queued; the delivery worker has not settled it yet.
      Counts as told, so a second run does not queue it again.
    - ``SENT``: the account was told, by push, email or both.
    - ``SKIPPED``: a CHANGES-mode account's establishing issue that moved
      nothing; logged so the next issue compares against it.
    - ``BASELINE``: a day already under way when alerts were turned on;
      recorded silently so the first message is about the next change.
    - ``FAILED``: every channel failed (an expired device, say, with email
      off); not counted as told, so the next run tries again.
    """

    class Outcome(models.TextChoices):
        """Why the row exists."""

        PENDING = "PENDING", "Queued"
        SENT = "SENT", "Sent"
        SKIPPED = "SKIPPED", "Skipped (no change)"
        BASELINE = "BASELINE", "Baseline (day under way)"
        FAILED = "FAILED", "Failed"

    class Kind(models.TextChoices):
        """Which kind of message the state was."""

        NEW = "NEW", "New day"
        UPDATE = "UPDATE", "Update to a day"
        SEASON_START = "SEASON_START", "First bulletin of the season"

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=CASCADE,
        related_name="bulletin_alerts",
    )
    region = models.ForeignKey(
        "regions.MicroRegion",
        on_delete=CASCADE,
        related_name="bulletin_alerts",
    )
    target_date = models.DateField(help_text="The day the bulletin forecasts.")
    bulletin = models.ForeignKey(
        Bulletin,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alerts",
        help_text="The authoritative bulletin whose state this row records.",
    )
    fingerprint = models.CharField(max_length=32)
    state = models.JSONField(help_text="BulletinState.to_json() at send time.")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.NEW)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    sent_push = models.BooleanField(default=False)
    sent_email = models.BooleanField(default=False)
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=255, blank=True)

    objects = BulletinAlertQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "region", "target_date", "fingerprint"],
                name="bulletin_alert_unique_state",
            ),
        ]
        indexes = [
            models.Index(
                fields=["account", "region", "target_date"],
                name="bulletin_alert_day",
            ),
        ]

    def to_string(self) -> str:
        """Return ``BulletinAlert(<account pk>, <region_id>, <day>, <outcome>)``."""
        return (
            f"BulletinAlert({self.account_id}, {self.region_id},"
            f" {self.target_date}, {self.outcome})"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
