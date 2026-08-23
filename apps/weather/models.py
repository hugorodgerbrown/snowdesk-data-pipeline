"""
apps/weather/models.py — Open-Meteo weather database models.

Owns the four Open-Meteo-derived models, split out of
``apps.bulletins.models`` by SNOW-654 (see that module for the CAAML
bulletin models that stayed behind):
  - WeatherSnapshot: one row per (region, date) storing the WMO weather
    code, sunrise/sunset times, and daily temperature/snowfall totals
    fetched from Open-Meteo. Used by the render model (SNOW-98) to
    determine whether a day is daytime or night, and by the weather panel
    (SNOW-571) to show a hi/lo temperature and a snowfall total.
  - ForecastCell: canonical weather-sampling location that many nearby
    map pins snap to, keyed on a quantised lat/lon grid cell and an
    elevation band. Resolved by
    ``apps.weather.services.forecast_cells.resolve_forecast_cell``
    (SNOW-412, phase 1 of the Favourites feature).
  - ForecastCellWeather: one row per (ForecastCell, date) storing the
    comprehensive Open-Meteo daily forecast block for that point —
    the point analogue of WeatherSnapshot, but with a richer field set
    (temperature, precipitation, wind, UV) since a favourited point is a
    personal detail card rather than a bulletin-page header. Fetched by
    the ``fetch_weather`` management command's active-ForecastCell pass
    (SNOW-416).
  - ForecastCellWeatherHistory: one row per (ForecastCell, valid date,
    issue date) retaining how a forecast for a given day evolved as that
    day approached. ForecastCellWeather is upserted on (point, date) and
    so keeps only the final, day-of view; this table keeps the earlier
    ones, which are otherwise destroyed on every run (SNOW-575).

The CAAML avalanche bulletins (Bulletin, RegionBulletin, RegionDayRating,
…) live in ``apps.bulletins.models``; the region hierarchy (MicroRegion,
MajorRegion, SubRegion, Resort) lives in ``apps.regions.models``.

Every model here pins ``Meta.db_table`` to the ``bulletins_*`` name it
had before the split, so SNOW-654 moved code without touching the
database. Renaming the tables is a separate ticket — see
``docs/decisions/weather-is-its-own-app.md``.

Each model uses a custom Manager + QuerySet pair so that domain-specific
query methods live on the queryset and are accessible via both
``Model.objects`` and chained querysets.

Keep business logic out of models — put it in ``apps/weather/services/``
instead.
"""

from __future__ import annotations

import logging
from datetime import date as _date

from django.db import models
from django.db.models import CASCADE
from django.utils import timezone

from apps.core.models import BaseModel

logger = logging.getLogger(__name__)


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
    mirror ``ForecastCellWeather``'s fields of the same name (same units:
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

        # Pinned to the pre-SNOW-654 table name: the app split moved code,
        # not data. Renaming the table is a separate ticket.
        db_table = "bulletins_weathersnapshot"
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
# ForecastCell
# ---------------------------------------------------------------------------


class ForecastCellQuerySet(models.QuerySet["ForecastCell"]):
    """Custom queryset for ForecastCell."""

    def active(self) -> "ForecastCellQuerySet":
        """Return points referenced by a favourite, a resort or a location.

        Annotates over the reverse FKs created by ``favourites.Favourite``
        and ``regions.Resort`` (SNOW-503), plus ``locations.Location``
        (SNOW-700), and filters to rows with a non-zero count on any side —
        see ``docs/decisions/forecast-point-quantisation.md``. Each count is
        annotated with ``distinct=True`` so joining three reverse relations
        at once cannot inflate any of them; a point shared by a favourite, a
        resort and a location still appears exactly once in the result.

        The referent set is edited across several tickets and the ordering
        matters: SNOW-700 **adds** ``Location`` while SNOW-703 and SNOW-704
        remove ``Resort`` and ``Favourite`` once those have migrated. The
        removals come last, because a point that falls out of ``active()``
        stops being fetched and is then deleted by
        ``prune_forecast_points`` — taking its stored weather with it.

        Returns:
            Filtered queryset of ForecastCells with one or more
            favourites, resorts or locations.

        """
        return self.annotate(
            favourite_count=models.Count("favourites", distinct=True),
            resort_count=models.Count("resorts", distinct=True),
            location_count=models.Count("locations", distinct=True),
        ).filter(
            models.Q(favourite_count__gt=0)
            | models.Q(resort_count__gt=0)
            | models.Q(location_count__gt=0)
        )

    def inactive(self) -> "ForecastCellQuerySet":
        """Return points referenced by no favourite, resort or location.

        The exact complement of ``active()`` — a point lands here when the
        last referent holding it goes away (SNOW-633). Such a point is
        already excluded from the ``fetch_weather`` point pass, so its
        stored weather can only go stale; ``prune_forecast_points`` deletes
        it.

        Must be kept the exact complement: a referent added to ``active()``
        and forgotten here would put the same point in both, and
        ``prune_forecast_points`` would delete a point that is still being
        fetched for.

        Returns:
            Filtered queryset of ForecastCells with no favourites, no
            resorts and no locations.

        """
        return self.annotate(
            favourite_count=models.Count("favourites", distinct=True),
            resort_count=models.Count("resorts", distinct=True),
            location_count=models.Count("locations", distinct=True),
        ).filter(favourite_count=0, resort_count=0, location_count=0)


class ForecastCell(BaseModel):
    """
    A canonical weather-sampling location shared by nearby map pins.

    Many user pins that land close together (horizontally and in
    elevation) should reuse the same Open-Meteo forecast fetch rather than
    each triggering its own. To make "close together" a stable, indexable
    concept, every pin is quantised onto a coarse lat/lon grid cell plus
    an elevation band, and one ``ForecastCell`` row is kept per distinct
    (lat_cell, lon_cell, elevation_band) triple.

    Rows are created and reused by
    ``apps.weather.services.forecast_cells.resolve_forecast_cell``, which
    also decides whether a pin should reuse an existing nearby point (via
    haversine distance and elevation difference) or mint a new one. See
    ``docs/decisions/forecast-point-quantisation.md`` for the grid sizing
    rationale.

    ``latitude``, ``longitude``, and ``elevation`` are the *representative*
    values captured from the pin that created (or last matched) this row —
    not the cell's geometric centre.
    """

    lat_cell = models.IntegerField(
        help_text="floor(latitude / LAT_CELL_SIZE) — see forecast_cells.py.",
    )
    lon_cell = models.IntegerField(
        help_text="floor(longitude / LON_CELL_SIZE) — see forecast_cells.py.",
    )
    elevation_band = models.IntegerField(
        help_text="floor(elevation / ELEVATION_BAND_SIZE) — see forecast_cells.py.",
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

    objects = ForecastCellQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        # Pinned to the pre-SNOW-654 table name: the app split moved code,
        # not data. Renaming the table is a separate ticket.
        db_table = "bulletins_forecastpoint"
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
# ForecastCellWeather
# ---------------------------------------------------------------------------


class ForecastCellWeatherQuerySet(models.QuerySet["ForecastCellWeather"]):
    """Custom queryset for ForecastCellWeather."""

    def for_date(self, target_date: _date) -> "ForecastCellWeatherQuerySet":
        """
        Return all rows valid for a given calendar date.

        Args:
            target_date: The calendar date to filter by.

        Returns:
            A filtered queryset of ForecastCellWeather rows for that date.

        """
        return self.filter(valid_for_date=target_date)

    def forecast_for_point(
        self, point: "ForecastCell", start_date: _date
    ) -> "ForecastCellWeatherQuerySet":
        """
        Return the forward-looking forecast window for one point.

        Unlike the model's default ``-valid_for_date`` ordering, this is
        ascending — a multi-day forecast panel wants chronological order.

        Args:
            point: The ForecastCell to filter by.
            start_date: The earliest calendar date to include (inclusive).

        Returns:
            A queryset of ForecastCellWeather rows for that point, from
            start_date onwards, ordered by valid_for_date ascending.

        """
        return self.filter(
            forecast_cell=point, valid_for_date__gte=start_date
        ).order_by("valid_for_date")


class ForecastCellWeather(BaseModel):
    """
    Open-Meteo daily forecast data for one ForecastCell on one calendar day.

    One row per (forecast_cell, valid_for_date) pair. Fetched by the
    ``fetch_weather`` management command's active-ForecastCell pass —
    the point analogue of ``WeatherSnapshot``, storing the comprehensive
    daily Open-Meteo block rather than just the WMO code and sunrise/sunset,
    since a favourited point is rendered as a personal detail card (SNOW-416).

    Open-Meteo omits some daily variables depending on the backing weather
    model (e.g. ``precipitation_probability_max``, ``uv_index_max``), so
    every field beyond the core trio is nullable.
    """

    forecast_cell = models.ForeignKey(
        "weather.ForecastCell",
        on_delete=CASCADE,
        related_name="weather_snapshots",
        # Pinned to the pre-SNOW-703 column: the rename moved the field
        # name, not the data. Without this the migration would emit a real
        # ALTER and sqlmigrate would stop being a no-op.
        db_column="forecast_point_id",
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

    objects = ForecastCellWeatherQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        # Pinned to the pre-SNOW-654 table name: the app split moved code,
        # not data. Renaming the table is a separate ticket.
        db_table = "bulletins_forecastpointweather"
        unique_together = [("forecast_cell", "valid_for_date")]
        ordering = ["-valid_for_date", "forecast_cell__id"]
        indexes = [
            # Name pinned: without it the index name is derived from the
            # field names, so the SNOW-703 rename would drop and recreate
            # it — real DDL on a large table, and sqlmigrate would stop
            # being a no-op.
            models.Index(
                fields=["forecast_cell", "valid_for_date"],
                name="bulletins_f_forecas_e18a91_idx",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``46.80000,7.50000 @1500m 2026-05-01 wmo=1``
        """
        return (
            f"{self.forecast_cell.to_string()} "
            f"{self.valid_for_date} wmo={self.weather_code}"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# ForecastCellWeatherHistory
# ---------------------------------------------------------------------------


class ForecastCellWeatherHistoryQuerySet(models.QuerySet["ForecastCellWeatherHistory"]):
    """Custom queryset for ForecastCellWeatherHistory."""

    def convergence_for(
        self, point: "ForecastCell", valid_for_date: _date
    ) -> "ForecastCellWeatherHistoryQuerySet":
        """
        Return how the forecast for one date evolved, oldest issue first.

        Unlike the model's default ordering, this is ascending by
        ``issued_date`` — a convergence series wants to read from the
        earliest view of the day through to the day-of one, the same way
        ``ForecastCellWeather.objects.forecast_for_point`` inverts its
        model's default ordering for a forward-looking panel.

        Args:
            point: The ForecastCell to filter by.
            valid_for_date: The forecast day whose history is wanted.

        Returns:
            A queryset of ForecastCellWeatherHistory rows for that
            (point, day), ordered by issued_date ascending.

        """
        return self.filter(forecast_cell=point, valid_for_date=valid_for_date).order_by(
            "issued_date"
        )


class ForecastCellWeatherHistory(BaseModel):
    """
    How the forecast for one ForecastCell-day evolved as the day approached.

    ``ForecastCellWeather`` is upserted on ``(forecast_cell,
    valid_for_date)``, so a given day is overwritten on every run and only
    the final, day-of forecast survives. That final row is the most
    accurate one, but the earlier views of the same day — issued three or
    six days out — are what show whether a forecast was stable or swung
    late, and they are destroyed as a side effect of the upsert.

    This table retains them: one row per ``(forecast_cell,
    valid_for_date, issued_date)``. Because ``issued_date`` is part of the
    key, the four runs within a single day collapse to one row (the last
    run of that day wins), so a forecast day accrues one row per day of
    its window rather than one per run.

    Deliberately narrower than ``ForecastCellWeather``. ``hourly_series``
    is excluded — it is the bulk of the payload and is populated for only
    the first ``POINT_HOURLY_DAYS`` days of a window, so it cannot form a
    series across lead times. ``sunrise``/``sunset`` are excluded because
    they are astronomical: identical on every run for a given day, so they
    carry no convergence signal.

    Written by ``fetch_weather_for_point`` alongside its
    ``ForecastCellWeather`` upsert, inside the same transaction, so the
    pair lands together or not at all (SNOW-575).

    Rows accrue only from the moment this shipped — a past forecast cannot
    be reconstructed after the fact.
    """

    forecast_cell = models.ForeignKey(
        "weather.ForecastCell",
        on_delete=CASCADE,
        related_name="forecast_history",
        # See ForecastCellWeather.forecast_cell — column pinned, name moved.
        db_column="forecast_point_id",
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
    # required, mirroring ForecastCellWeather; the rest are nullable
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

    objects = ForecastCellWeatherHistoryQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        # Pinned to the pre-SNOW-654 table name: the app split moved code,
        # not data. Renaming the table is a separate ticket.
        db_table = "bulletins_forecastpointweatherhistory"
        verbose_name_plural = "forecast point weather history"
        unique_together = [("forecast_cell", "valid_for_date", "issued_date")]
        ordering = ["-valid_for_date", "issued_date"]
        indexes = [
            # Names pinned — see ForecastCellWeather.Meta.indexes.
            models.Index(
                fields=["forecast_cell", "valid_for_date"],
                name="bulletins_f_forecas_95f2d5_idx",
            ),
            models.Index(
                fields=["valid_for_date", "lead_days"],
                name="bulletins_f_valid_f_dd24eb_idx",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``46.80000,7.50000 @1500m 2026-05-01 issued 2026-04-28
        (+3d) wmo=1``
        """
        return (
            f"{self.forecast_cell.to_string()} "
            f"{self.valid_for_date} issued {self.issued_date} "
            f"({self.lead_days:+d}d) wmo={self.weather_code}"
        )

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
