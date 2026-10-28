"""
apps/ingest/models.py — What ingest keeps of what the providers published.

Five models:

* ``SourceBulletin`` (SNOW-1250): one row per bulletin a provider
  published, holding its CAAML as normalised, the link to its PDF and a
  hash of the CAAML. It is the record the data feed's ``bulletins``
  endpoint serves and the source the app's ``Bulletin`` rows are applied
  from. Since SNOW-1270 it also holds the bulletin's render model, built
  by the service whenever the row is written, so a client copies the
  service's reading of the bulletin rather than making its own.
* ``SourceLocation`` (SNOW-1253): one reference place — a resort's
  village, mid-station, peak or pin, a region centre — that the service
  fetches weather for. Its ``uuid`` is the identity every instance keys its
  own ``Location`` on (``Location.source_uuid``). A removed place is
  tombstoned (``deleted_at``), never deleted, so a client learns of it.
* ``SourceWeather`` (SNOW-1253): what was known about one source location
  on one day — the same columns and the same rule as the app's
  ``Weather``: one row per (location, day), never rewritten once that day
  is past.
* ``SourcePass`` and ``SourceResort`` (SNOW-1271): the season passes and
  resorts, each one JSON ``document`` in the shape a client applies, keyed
  by ``uuid``. Ingest stores them and never reads inside them, as it treats
  a bulletin's CAAML: what a resort is belongs to whoever curates it. Both
  share ``SourceDocument``.

**Not a ``BaseModel``.** ``apps/ingest`` imports no other ``apps.*`` package
(``docs/decisions/ingestion-is-a-rehostable-unit.md``), so the id, uuid and
timestamps ``BaseModel`` would give are declared here, with the same types
and defaults. The rest of the model kit is as CLAUDE.md asks: an admin
class, ``to_string()``, ``Meta.ordering`` and a custom queryset.

**``updated_at`` moves only when the content does.** ``store_bulletin``
writes a row only when its hash changes, so the feed's keyset on
``(updated_at, id)`` reads as "what changed since", and a re-fetch of the
same bulletin costs a client nothing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.db import models
from django.utils import timezone


def content_hash(caaml: dict[str, Any]) -> str:
    """Return the sha256 of a bulletin's CAAML in canonical JSON.

    Keys are sorted and separators fixed, so two dicts that compare equal
    always hash equal, whatever order a provider sent their keys in.

    Args:
        caaml: The bulletin's CAAML properties.

    Returns:
        The hex digest.

    """
    canonical = json.dumps(
        caaml, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class SourceBulletinQuerySet(models.QuerySet["SourceBulletin"]):
    """Queryset for ``SourceBulletin``."""

    def for_provider(self, provider: str) -> SourceBulletinQuerySet:
        """Return the bulletins one provider published.

        Args:
            provider: ``SLF``, ``ALBINA`` or ``METEOFRANCE``.

        Returns:
            The filtered queryset.

        """
        return self.filter(provider=provider)

    def stale_render_model(self, version: int) -> SourceBulletinQuerySet:
        """Return the bulletins whose render model predates ``version``.

        Args:
            version: The builder's current ``RENDER_MODEL_VERSION``.

        Returns:
            The filtered queryset. A failed build (version 0) counts as
            stale, so a builder fix reaches it on the next rebuild.

        """
        return self.filter(render_model_version__lt=version)


class SourceBulletin(models.Model):
    """One bulletin as a provider published it, normalised to CAAML."""

    class Provider(models.TextChoices):
        """The provider a bulletin came from."""

        SLF = "SLF", "SLF (Switzerland)"
        ALBINA = "ALBINA", "ALBINA (Austria/South Tyrol/Trentino)"
        METEOFRANCE = "METEOFRANCE", "Météo-France (France)"

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    bulletin_id = models.CharField(max_length=255, unique=True)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    caaml = models.JSONField(
        help_text="The bulletin's CAAML properties, as normalised at fetch time."
    )
    pdf_url = models.URLField(blank=True, default="", max_length=500)
    content_hash = models.CharField(
        max_length=64,
        help_text="sha256 of the CAAML in canonical JSON; a re-fetch that "
        "matches it writes nothing.",
    )
    fetched_at = models.DateTimeField(
        help_text="When this content was fetched — the last time it changed."
    )
    render_model = models.JSONField(
        default=dict,
        blank=True,
        help_text="The service's reading of the CAAML (SNOW-1270), rebuilt "
        "whenever the row is written; {'version': 0, 'error': …} when the "
        "build failed, {} when never built.",
    )
    render_model_version = models.PositiveSmallIntegerField(
        default=0,
        help_text="RENDER_MODEL_VERSION the render model was built at; 0 "
        "when it failed or was never built.",
    )

    objects = SourceBulletinQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            # The feed's keyset (apps/ingest/api/paging.py).
            models.Index(fields=["updated_at", "id"], name="ingest_srcb_keyset"),
        ]

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()

    def to_string(self) -> str:
        """Return the provider and the bulletin id, e.g. ``SLF abc-123``."""
        return f"{self.provider} {self.bulletin_id}"


class ImmutableSourceWeatherError(Exception):
    """A write tried to rewrite a ``SourceWeather`` row whose day has passed.

    Ingest's own copy of the app's ``ImmutableWeatherRowError``: ingest
    imports no other ``apps.*`` package, so it cannot raise that one.
    """


class SourceLocationQuerySet(models.QuerySet["SourceLocation"]):
    """Queryset for ``SourceLocation``."""

    def live(self) -> SourceLocationQuerySet:
        """Return the places not tombstoned — the set the fetch walks.

        Returns:
            The filtered queryset.

        """
        return self.filter(deleted_at__isnull=True)

    def tombstoned(self) -> SourceLocationQuerySet:
        """Return the places that were removed, kept so a client learns of it.

        Returns:
            The filtered queryset.

        """
        return self.filter(deleted_at__isnull=False)


class SourceLocation(models.Model):
    """One reference place the service fetches weather for.

    ``latitude``/``longitude`` are exact, as given: never rounded, never
    snapped to a grid. ``elevation_m`` is passed to Open-Meteo so the
    forecast is downscaled to the place's own height; null fetches the
    model cell's mean.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="The service's id for this place — Location.source_uuid "
        "on every instance.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="The place's curated name; empty for an unnamed region centre.",
    )
    latitude = models.FloatField(help_text="Exact WGS-84 latitude.")
    longitude = models.FloatField(help_text="Exact WGS-84 longitude.")
    elevation_m = models.FloatField(
        null=True,
        blank=True,
        help_text="Elevation in metres, passed to the forecast request.",
    )
    document = models.JSONField(
        default=dict,
        blank=True,
        help_text="What else the host curates about the place (its kind, "
        "what3words address, the regions it is the centre of), stored as "
        "handed over and never read here (SNOW-1271).",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the place was removed. A tombstone is kept, not "
        "deleted, so a client syncing since then learns of it.",
    )

    objects = SourceLocationQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["updated_at", "id"], name="ingest_srcl_keyset"),
        ]

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()

    def to_string(self) -> str:
        """Return the name (or the coordinate) and the height.

        Format: ``Mont Fort 46.10361,7.29889 @3328m``.

        Returns:
            The description.

        """
        label = self.name or "Unnamed"
        height = "" if self.elevation_m is None else f" @{self.elevation_m:.0f}m"
        gone = " (deleted)" if self.deleted_at is not None else ""
        return f"{label} {self.latitude},{self.longitude}{height}{gone}"


class SourceDocumentQuerySet(models.QuerySet[Any]):
    """Queryset shared by the document tables, ``SourcePass`` and ``SourceResort``."""

    def live(self) -> SourceDocumentQuerySet:
        """Return the documents not tombstoned.

        Returns:
            The filtered queryset.

        """
        return self.filter(deleted_at__isnull=True)

    def tombstoned(self) -> SourceDocumentQuerySet:
        """Return the documents that were removed, kept so a client learns of it.

        Returns:
            The filtered queryset.

        """
        return self.filter(deleted_at__isnull=False)


class SourceDocument(models.Model):
    """One curated record, held as the JSON document a client applies.

    Abstract: ``SourcePass`` and ``SourceResort`` are the two tables. The
    ``uuid`` is the record's identity on every instance (its
    ``source_uuid``); ``slug`` is a readable label, not a key, so it is
    indexed but not unique. ``content_hash`` is the document's
    ``content_hash()``, and a row is written only when it changes, so
    ``updated_at`` reads as "changed since" for the feed.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="The service's id for this record — source_uuid on every instance.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    slug = models.SlugField(
        max_length=255, help_text="The record's slug, for reading; not a key."
    )
    document = models.JSONField(help_text="The record, in the shape a client applies.")
    content_hash = models.CharField(
        max_length=64, help_text="sha256 of the document in canonical JSON."
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the record was removed. A tombstone is kept, not "
        "deleted, so a client syncing since then learns of it.",
    )

    objects = SourceDocumentQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()

    def to_string(self) -> str:
        """Return the slug, marked when tombstoned.

        Format: ``verbier`` or ``verbier (deleted)``.

        Returns:
            The description.

        """
        gone = " (deleted)" if self.deleted_at is not None else ""
        return f"{self.slug}{gone}"


class SourcePass(SourceDocument):
    """A season pass: ``{"name": …, "website": …}``."""

    class Meta(SourceDocument.Meta):
        """Model metadata."""

        indexes = [
            models.Index(fields=["updated_at", "id"], name="ingest_srcp_keyset"),
            models.Index(fields=["slug"], name="ingest_srcp_slug"),
        ]
        verbose_name_plural = "source passes"


class SourceResort(SourceDocument):
    """A resort: its fields, region, pin, passes and location roles.

    The document names its places by ``SourceLocation.uuid`` and its passes
    by ``SourcePass.uuid``, so a client resolves every relation through
    ``source_uuid`` and never needs the host's own ids.
    """

    class Meta(SourceDocument.Meta):
        """Model metadata."""

        indexes = [
            models.Index(fields=["updated_at", "id"], name="ingest_srcr_keyset"),
            models.Index(fields=["slug"], name="ingest_srcr_slug"),
        ]


class SourceWeatherQuerySet(models.QuerySet["SourceWeather"]):
    """Queryset for ``SourceWeather``."""

    def on_date(self, observed_on: Any) -> SourceWeatherQuerySet:
        """Return the rows recorded for one calendar day.

        Args:
            observed_on: The day.

        Returns:
            The filtered queryset.

        """
        return self.filter(observed_on=observed_on)


class SourceWeather(models.Model):
    """What was known about one source location on one day.

    The columns are the app's ``Weather`` columns, one for one
    (``WEATHER_COLUMNS``), so applying a row into an instance is a copy.
    ``hourly`` and ``forecast`` hold the same shapes. A past day is never
    rewritten (``store_source_weather``), and ``save()`` re-checks it.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    location = models.ForeignKey(
        SourceLocation,
        on_delete=models.CASCADE,
        related_name="weather",
        help_text="The place this row describes.",
    )
    observed_on = models.DateField(
        db_index=True, help_text="The calendar day this row is OF."
    )
    fetched_at = models.DateTimeField(help_text="When this row was last written.")

    weather_code = models.PositiveSmallIntegerField()
    sunrise = models.DateTimeField()
    sunset = models.DateTimeField()
    temperature_2m_max = models.FloatField(null=True, blank=True)
    temperature_2m_min = models.FloatField(null=True, blank=True)
    apparent_temperature_max = models.FloatField(null=True, blank=True)
    apparent_temperature_min = models.FloatField(null=True, blank=True)
    precipitation_sum = models.FloatField(null=True, blank=True)
    snowfall_sum = models.FloatField(null=True, blank=True)
    precipitation_probability_max = models.PositiveSmallIntegerField(
        null=True, blank=True
    )
    precipitation_hours = models.FloatField(null=True, blank=True)
    wind_speed_10m_max = models.FloatField(null=True, blank=True)
    wind_gusts_10m_max = models.FloatField(null=True, blank=True)
    wind_direction_10m_dominant = models.FloatField(null=True, blank=True)
    uv_index_max = models.FloatField(null=True, blank=True)
    daylight_duration = models.FloatField(null=True, blank=True)
    sunshine_duration = models.FloatField(null=True, blank=True)
    freezing_level_height = models.FloatField(null=True, blank=True)
    hourly = models.JSONField(null=True, blank=True)
    forecast = models.JSONField(null=True, blank=True)

    objects = SourceWeatherQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-observed_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "observed_on"], name="ingest_srcw_one_per_day"
            ),
        ]
        indexes = [
            models.Index(fields=["updated_at", "id"], name="ingest_srcw_keyset"),
        ]
        verbose_name_plural = "source weather"

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()

    def to_string(self) -> str:
        """Return the place, the day and the weather code.

        Returns:
            The description.

        """
        return f"{self.location} {self.observed_on} code={self.weather_code}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse to rewrite a stored row whose day has passed.

        The backstop for a write that never came through
        ``store_source_weather`` — an admin form, a shell session.

        Raises:
            ImmutableSourceWeatherError: The row exists and its day is past.

        """
        if (
            self.pk is not None
            and self.observed_on < timezone.localdate()
            and SourceWeather.objects.filter(pk=self.pk).exists()
        ):
            raise ImmutableSourceWeatherError(
                f"SourceWeather({self.location_id}, {self.observed_on}) is past "
                "and cannot be rewritten."
            )
        super().save(*args, **kwargs)


# The columns a weather row carries beyond its key — identical on
# ``SourceWeather`` and the app's ``Weather``, so an apply is a copy of these.
WEATHER_COLUMNS: tuple[str, ...] = (
    "fetched_at",
    "weather_code",
    "sunrise",
    "sunset",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "uv_index_max",
    "daylight_duration",
    "sunshine_duration",
    "freezing_level_height",
    "hourly",
    "forecast",
)
