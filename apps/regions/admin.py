"""
apps/regions/admin.py — Django admin registrations for the regions app.

Covers the geographic hierarchy (MajorRegion, SubRegion, MicroRegion),
Resort, and RegionAlias. Bulletin-related admins live in
``apps/bulletins/admin.py``.
"""

import logging

from django.contrib import admin

from apps.locations.admin import ResortLocationInline

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
    readonly_fields = [
        "id",
        "slug",
        "centre",
        "boundary",
        "basemap_download",
        "created_at",
        "updated_at",
    ]


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
    """Admin view for Resort.

    Carries ``ResortLocationInline`` so a curator opens Verbier and adds its
    village and its top without leaving the page — the surface SNOW-701's
    data work is done through. The inline is defined in
    ``apps/locations/admin.py``, beside the model it edits.
    """

    inlines = [ResortLocationInline]

    list_display = [
        "name",
        "kind",
        "tier",
        "name_alt",
        "region",
        "canton",
        "num_lifts",
        "latitude",
        "longitude",
        "geocode_source",
        "needs_review",
    ]
    list_filter = ["kind", "tier", "canton", "geocode_source", "needs_review"]
    search_fields = ["name", "slug", "name_alt", "region__region_id"]
    ordering = ["name"]
    readonly_fields = [
        "id",
        "uuid",
        "geocoded_at",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        # ``slug`` is shown and editable, but leave it alone on a rename:
        # it is the resort's indexed URL (SNOW-796). Blank on a new row
        # and ``Resort.save()`` mints it from the name.
        (None, {"fields": ("name", "slug", "name_alt", "region", "canton", "notes")}),
        (
            "Resort details",
            {
                "fields": (
                    "tier",
                    "operator_name",
                    "website",
                    "why_it_matters",
                    "num_lifts",
                    "num_runs",
                    "total_piste_km",
                    "base_elevation_m",
                    "top_elevation_m",
                    "typical_season_open",
                    "typical_season_close",
                ),
                "description": (
                    "Manually-curated descriptive fields — not sourced from "
                    "any bulletin feed. Edit here directly."
                ),
            },
        ),
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
                    "set lat/lon is the in-map editor at /?edit=resorts, "
                    "which is open to superusers only (SNOW-724 — previously "
                    "the ``edit_map`` waffle flag, seeded superusers=True)."
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
