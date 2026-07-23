"""
regions/admin.py — Django admin registrations for the regions app.

Covers the geographic hierarchy (MajorRegion, SubRegion, MicroRegion),
Resort, and RegionAlias. Bulletin-related admins live in
``bulletins/admin.py``.
"""

import logging

from django.contrib import admin

from .models import (
    MajorRegion,
    MicroRegion,
    RegionAlias,
    Resort,
    SubRegion,
)

logger = logging.getLogger(__name__)


@admin.register(MajorRegion)
class MajorRegionAdmin(admin.ModelAdmin):
    """Admin view for MajorRegion (L1)."""

    list_display = [
        "prefix",
        "name_native",
        "name_en",
        "country",
        "display_on_map",
        "updated_at",
    ]
    list_filter = ["country", "display_on_map"]
    search_fields = ["prefix", "name_native", "name_en"]
    ordering = ["prefix"]
    readonly_fields = [
        "id",
        "uuid",
        "centre",
        "bbox",
        "boundary",
        "created_at",
        "updated_at",
    ]


@admin.register(SubRegion)
class SubRegionAdmin(admin.ModelAdmin):
    """Admin view for SubRegion (L2)."""

    list_display = ["prefix", "name_native", "name_en", "major", "updated_at"]
    list_filter = ["major"]
    search_fields = ["prefix", "name_native", "name_en"]
    ordering = ["prefix"]
    readonly_fields = [
        "id",
        "uuid",
        "centre",
        "bbox",
        "boundary",
        "created_at",
        "updated_at",
    ]


@admin.register(MicroRegion)
class MicroRegionAdmin(admin.ModelAdmin):
    """Admin view for MicroRegion (L4 EAWS micro-region)."""

    list_display = ["region_id", "name", "subregion", "slug", "updated_at"]
    list_filter = ["subregion__major", "subregion"]
    search_fields = ["region_id", "name"]
    ordering = ["region_id"]
    readonly_fields = ["id", "slug", "centre", "boundary", "created_at", "updated_at"]


@admin.register(RegionAlias)
class RegionAliasAdmin(admin.ModelAdmin):
    """Admin view for RegionAlias."""

    list_display = ["alias_text", "region", "updated_at"]
    list_filter = ["region__subregion__major"]
    search_fields = ["alias_text", "region__region_id", "region__name"]
    ordering = ["alias_text"]
    readonly_fields = ["id", "uuid", "created_at", "updated_at"]


@admin.register(Resort)
class ResortAdmin(admin.ModelAdmin):
    """Admin view for Resort."""

    list_display = [
        "name",
        "name_alt",
        "region",
        "canton",
        "latitude",
        "longitude",
        "geocode_source",
        "needs_review",
        "forecast_point",
    ]
    list_filter = ["canton", "geocode_source", "needs_review"]
    search_fields = ["name", "name_alt", "region__region_id"]
    ordering = ["name"]
    raw_id_fields = ["forecast_point"]
    readonly_fields = [
        "id",
        "uuid",
        "geocoded_at",
        "created_at",
        "updated_at",
        "forecast_point",
    ]
    fieldsets = (
        (None, {"fields": ("name", "name_alt", "region", "canton", "notes")}),
        (
            "Geocoding",
            {
                "fields": (
                    "latitude",
                    "longitude",
                    "geocode_source",
                    "geocode_confidence",
                    "geocoded_at",
                    "needs_review",
                ),
                "description": (
                    "Edit coordinates here as a fallback. The preferred way to "
                    "set lat/lon is the in-map editor at /?edit=resorts. "
                    "Access is gated by the ``edit_map`` waffle flag — manage "
                    "it at /admin/waffle/flag/ (seeded with superusers=True)."
                ),
            },
        ),
        (
            "Weather",
            {
                "fields": ("forecast_point",),
                "description": (
                    "Machine-resolved by "
                    "`manage.py link_resort_forecast_points --commit` — not "
                    "hand-edited."
                ),
            },
        ),
        (
            "Audit",
            {
                "classes": ("collapse",),
                "fields": ("id", "uuid", "created_at", "updated_at"),
            },
        ),
    )
