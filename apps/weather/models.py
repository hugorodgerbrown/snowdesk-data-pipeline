"""
apps/weather/models.py — Database models for the weather application.

Defines the one model the Open-Meteo domain needs:

* ``Weather`` — what is known about one ``Location`` on one day. One row
  per ``(location, observed_on)``, never rewritten once that day is past.

**One row per day, created ahead and refined** (SNOW-1337). Each fetch
covers today and the six days after it and writes one row for each: a day's
row is created when it first enters the window, six days out, refined by
every fetch until its day arrives, and frozen once it has passed — its last
write the one made on the day itself. ``forecast_on`` is the day of the
fetch that last wrote the row, so its lead time is
``observed_on - forecast_on``: six on creation, zero on the day, and zero
on every past row. No row holds a list of later days: the outlook from any
day is simply the next rows for that place. What the forecast for a day
said at six days out is not retained (Hugo, 2026-11-01).

**A past row is immutable.** Rewriting it would silently replace what we
said on the day with something else. The rule is enforced in
``apps.weather.services.upsert`` and backstopped by ``save()`` below.

A row is **updated in place**, not appended, so a read path is
``.filter(location=…, observed_on=…).first()`` on the unique constraint —
never ``.order_by("-fetched_at").first()``.

Business logic stays out of this module: the fetch lives in
``services/fetch.py`` and the write rule in ``services/upsert.py``. The
``save()`` override below is the one exception, and it is a data-integrity
guard rather than a save-time side effect — it rejects a write, it does not
cause one. See ``docs/decisions/no-signals-for-side-effects.md``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel
from apps.weather.exceptions import ImmutableWeatherRowError

if TYPE_CHECKING:
    from datetime import date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class WeatherQuerySet(models.QuerySet["Weather"]):
    """Custom queryset for Weather."""

    def for_location(self, location: Any) -> "WeatherQuerySet":
        """Return every row recorded for one location.

        Args:
            location: The ``locations.Location`` to filter by.

        Returns:
            Filtered queryset, newest day first.

        """
        return self.filter(location=location)

    def on_date(self, observed_on: "date") -> "WeatherQuerySet":
        """Return the rows recorded for one calendar day.

        Args:
            observed_on: The day to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(observed_on=observed_on)

    def past(self) -> "WeatherQuerySet":
        """Return the rows whose day has passed — the immutable ones.

        The boundary is ``timezone.localdate()``, matching the rule
        ``upsert_weather`` and ``save()`` enforce, so "what this returns"
        and "what cannot be rewritten" are the same set by construction.

        Returns:
            Filtered queryset of rows with ``observed_on`` before today.

        """
        return self.filter(observed_on__lt=timezone.localdate())

    def current(self) -> "WeatherQuerySet":
        """Return today's and the forward-dated rows — the writable ones.

        The complement of ``past()``.

        Returns:
            Filtered queryset of rows with ``observed_on`` today or later.

        """
        return self.filter(observed_on__gte=timezone.localdate())

    def window(self, first_day: "date", days: int) -> "WeatherQuerySet":
        """Return the rows for ``days`` consecutive days from ``first_day``.

        The outlook from a day is the rows that follow it (SNOW-1337), so
        this is the one query behind every multi-day surface.

        Args:
            first_day: The first day of the window.
            days: How many days the window spans, ``first_day`` included.

        Returns:
            Filtered queryset, oldest day first.

        """
        return self.filter(
            observed_on__gte=first_day,
            observed_on__lt=first_day + timedelta(days=days),
        ).order_by("observed_on")


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


class Weather(BaseModel):
    """What is known about one location on one day.

    The daily scalars describe ``observed_on`` itself and ``hourly`` breaks
    that same day down hour by hour. ``forecast_on`` is the day of the fetch
    that last wrote it; once ``observed_on`` has passed the row is final.

    Every scalar except ``weather_code``, ``sunrise`` and ``sunset`` is
    nullable: Open-Meteo drops variables depending on which model backs the
    coordinates, and a missing variable must degrade to ``None`` rather
    than reject the day.

    ``location`` cascades — weather is dependent data with no meaning once
    the place it describes is gone.
    """

    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.CASCADE,
        related_name="weather",
        help_text="The location this row describes.",
    )
    observed_on = models.DateField(
        db_index=True,
        help_text=(
            "The calendar day this row is OF. Immutable once past — see "
            "apps.weather.services.upsert."
        ),
    )
    fetched_at = models.DateTimeField(
        help_text="When this row was last written (updated on every write).",
    )
    forecast_on = models.DateField(
        help_text=(
            "The day of the fetch that last wrote this row. The lead time is "
            "observed_on - forecast_on: six when the day enters the window, "
            "zero on the day itself."
        ),
    )

    # --- Daily scalars, describing ``observed_on`` -------------------------

    weather_code = models.PositiveSmallIntegerField(
        help_text="WMO weather interpretation code (0–99).",
    )
    sunrise = models.DateTimeField(
        help_text=(
            "Sunrise, in the location's own local time — the offset the "
            "provider returned is preserved rather than normalised to UTC."
        ),
    )
    sunset = models.DateTimeField(
        help_text=(
            "Sunset, in the location's own local time — the offset the "
            "provider returned is preserved rather than normalised to UTC."
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
    wind_direction_10m_dominant = models.FloatField(
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
            "Freezing level height in metres, derived as the day's maximum "
            "hourly value — Open-Meteo publishes no daily aggregate."
        ),
    )

    # --- JSON column -------------------------------------------------------

    hourly = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "This day, hour by hour: a list of HourlyRow dicts. Null when "
            "the response carried no hourly block. See apps.weather.types."
        ),
    )

    objects = WeatherQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-observed_on"]
        unique_together = [("location", "observed_on")]
        indexes = [
            # The data feed's keyset (SNOW-1197) — see Bulletin.Meta.
            models.Index(fields=["updated_at", "id"], name="weather_feed_keyset"),
        ]
        verbose_name_plural = "weather"

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``Mont Fort (Peak) 46.10361,7.29889 @3328m 2026-08-30 code=3``

        The location renders through its own ``__str__``, so the line
        carries whatever identity the location has — a name when curated, a
        bare coordinate when not.

        Returns:
            The description.

        """
        return f"{self.location} {self.observed_on} code={self.weather_code}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    @property
    def lead_days(self) -> int:
        """How many days ahead of its day this row was last written.

        Returns:
            ``observed_on - forecast_on`` in days: zero on the day itself
            and on every past row.

        """
        return (self.observed_on - self.forecast_on).days

    @property
    def is_immutable(self) -> bool:
        """Whether this row's day has passed, making it read-only.

        Returns:
            True when ``observed_on`` is before today.

        """
        return self.observed_on < timezone.localdate()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row, refusing to rewrite one whose day has passed.

        The backstop behind ``apps.weather.services.upsert.upsert_weather``.
        The service is the path every caller should take; this catches the
        ones that do not — an admin edit, a shell session, a data-migration
        script — so the rule holds at the model rather than only at the one
        function that remembers it.

        **Creating a past-dated row stays legal.** The guard fires only when
        a row already exists for this ``(location, observed_on)`` and its day
        is past, so backfilling a day that was never recorded (SNOW-731) is
        unaffected. It is *rewriting* an existing account that is refused.

        Args:
            *args: Forwarded to ``models.Model.save``.
            **kwargs: Forwarded to ``models.Model.save``.

        Raises:
            ImmutableWeatherRowError: The row exists and its day has passed.

        """
        if self.pk is not None and self.is_immutable:
            # Re-read rather than trusting the in-memory instance: an admin
            # form or a shell session may have already mutated observed_on,
            # and the question is whether the row AS STORED is past.
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("observed_on", flat=True)
                .first()
            )
            if stored is not None and stored < timezone.localdate():
                raise ImmutableWeatherRowError(
                    f"Weather(location={self.location_id}, observed_on={stored}) "
                    f"is past and cannot be rewritten. A row records what was "
                    f"known on its day; write today's row instead."
                )
        super().save(*args, **kwargs)
