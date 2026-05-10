"""
regions/admin.py — Django admin registrations for the regions app.

Covers the geographic hierarchy (MajorRegion, SubRegion, MicroRegion)
and Resort. Bulletin-related admins live in ``bulletins/admin.py``.
"""

import logging

from django.contrib import admin

from .models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)

logger = logging.getLogger(__name__)


@admin.register(MajorRegion)
class MajorRegionAdmin(admin.ModelAdmin):
    """Admin view for MajorRegion (L1)."""

    list_display = ["prefix", "name_native", "name_en", "country", "updated_at"]
    list_filter = ["country"]
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
    ]
    list_filter = ["canton", "geocode_source", "needs_review"]
    search_fields = ["name", "name_alt", "region__region_id"]
    ordering = ["name"]
    readonly_fields = ["id", "uuid", "geocoded_at", "created_at", "updated_at"]
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
                    "set lat/lon is the in-map editor at /map/?edit=resorts. "
                    "Access is gated by the ``edit_map`` waffle flag — manage "
                    "it at /admin/waffle/flag/ (seeded with superusers=True)."
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
