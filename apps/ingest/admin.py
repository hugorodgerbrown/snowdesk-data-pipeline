"""
apps/ingest/admin.py — Django admin registration for the ingest app.

Five registrations, all read-only windows: nothing can be added, edited
or deleted here.

* ``SourceBulletinAdmin`` — a ``SourceBulletin`` is what a provider
  published. A wrong row is corrected at the provider, or by re-fetching
  with ``fetch_bulletins --force``, never by hand — a hand edit would be
  served to every feed client as if the provider had said it.
* ``SourceLocationAdmin`` — the reference places (SNOW-1253). Until the
  service has its own editor they are mirrored from production's curated
  ``Location`` rows by ``fetch_weather`` and ``mirror_reference_data``,
  so a correction is made there.
* ``SourceWeatherAdmin`` — the service's account of each day, which is
  never rewritten once the day is past.
* ``SourcePassAdmin`` and ``SourceResortAdmin`` — the curated passes and
  resorts (SNOW-1271), mirrored from production's ``Pass`` and ``Resort``
  by ``mirror_reference_data``; corrected there, for the same reason.
* ``SourceDayRatingAdmin`` — the service's day ratings (SNOW-1270),
  computed from its bulletins; a wrong one is fixed by fixing the rule.
"""

import json
from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .models import (
    SourceBulletin,
    SourceDayRating,
    SourceLocation,
    SourcePass,
    SourceResort,
    SourceWeather,
)


@admin.register(SourceBulletin)
class SourceBulletinAdmin(admin.ModelAdmin[SourceBulletin]):
    """Read-only admin for stored provider bulletins."""

    list_display = (
        "bulletin_id",
        "provider",
        "target_date",
        "render_model_version",
        "fetched_at",
        "updated_at",
    )
    list_filter = ("provider", "render_model_version")
    search_fields = ("bulletin_id",)
    date_hierarchy = "fetched_at"
    ordering = ("-updated_at",)
    readonly_fields = (
        "uuid",
        "bulletin_id",
        "provider",
        "pdf_url",
        "content_hash",
        "fetched_at",
        "valid_from",
        "target_date",
        "region_codes",
        "render_model_version",
        "created_at",
        "updated_at",
        "caaml_pretty",
    )
    exclude = ("caaml", "render_model")

    @admin.display(description="CAAML")
    def caaml_pretty(self, obj: SourceBulletin) -> SafeString:
        """Return the CAAML as indented JSON, escaped by ``format_html``."""
        return format_html("<pre>{}</pre>", json.dumps(obj.caaml, indent=2))

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Refuse: rows come from a provider fetch only."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: SourceBulletin | None = None
    ) -> bool:
        """Refuse edits; the change view still renders, read-only."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: SourceBulletin | None = None
    ) -> bool:
        """Refuse: a row is the record of what a provider said."""
        return False


class _ReadOnly:
    """The three refusals every ingest admin shares."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Refuse: rows come from the service's own writes only."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        """Refuse edits; the change view still renders, read-only."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: object | None = None
    ) -> bool:
        """Refuse: a place is tombstoned, a day's row stands."""
        return False


@admin.register(SourceLocation)
class SourceLocationAdmin(_ReadOnly, admin.ModelAdmin[SourceLocation]):
    """Read-only admin for the weather service's reference places."""

    list_display = ("name", "latitude", "longitude", "elevation_m", "deleted_at")
    list_filter = (("deleted_at", admin.EmptyFieldListFilter),)
    search_fields = ("name", "uuid")
    ordering = ("name",)
    readonly_fields = (
        "uuid",
        "name",
        "latitude",
        "longitude",
        "elevation_m",
        "document",
        "deleted_at",
        "created_at",
        "updated_at",
    )


@admin.register(SourceWeather)
class SourceWeatherAdmin(_ReadOnly, admin.ModelAdmin[SourceWeather]):
    """Read-only admin for the weather service's place-days."""

    list_display = ("location", "observed_on", "weather_code", "fetched_at")
    list_select_related = ("location",)
    date_hierarchy = "observed_on"
    search_fields = ("location__name", "location__uuid")
    ordering = ("-observed_on",)
    raw_id_fields = ("location",)
    exclude = ("hourly", "forecast")

    def get_readonly_fields(
        self, request: HttpRequest, obj: SourceWeather | None = None
    ) -> list[str]:
        """Return every column but the two JSON ones, which are excluded."""
        return [
            field.name
            for field in SourceWeather._meta.concrete_fields
            if field.name not in ("id", "hourly", "forecast")
        ]


class _SourceDocumentAdmin(_ReadOnly, admin.ModelAdmin[Any]):
    """Read-only admin shared by the two document tables (SNOW-1271)."""

    list_display = ("slug", "updated_at", "deleted_at")
    list_filter = (("deleted_at", admin.EmptyFieldListFilter),)
    search_fields = ("slug", "uuid")
    ordering = ("slug",)
    readonly_fields = (
        "uuid",
        "slug",
        "document",
        "content_hash",
        "deleted_at",
        "created_at",
        "updated_at",
    )


@admin.register(SourcePass)
class SourcePassAdmin(_SourceDocumentAdmin):
    """Read-only admin for the service's season passes."""


@admin.register(SourceResort)
class SourceResortAdmin(_SourceDocumentAdmin):
    """Read-only admin for the service's resorts."""


@admin.register(SourceDayRating)
class SourceDayRatingAdmin(_ReadOnly, admin.ModelAdmin[SourceDayRating]):
    """Read-only admin for the service's day ratings (SNOW-1270)."""

    list_display = (
        "region_code",
        "date",
        "min_rating",
        "max_rating",
        "bulletin_id",
        "version",
        "updated_at",
    )
    list_filter = ("version", "source")
    search_fields = ("region_code", "bulletin_id")
    date_hierarchy = "date"
    ordering = ("-date", "region_code")
    readonly_fields = tuple(field.name for field in SourceDayRating._meta.get_fields())
