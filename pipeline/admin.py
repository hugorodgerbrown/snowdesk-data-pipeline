"""
pipeline/admin.py — Django admin registrations for pipeline models.

Provides list views with key fields and read-only display of timing and
error information so that operators can inspect pipeline runs, regions,
and bulletins without needing direct database access.
"""

import json

from django.contrib import admin
from django.utils.html import format_html

from .models import Bulletin, PipelineRun, Region, RegionBulletin


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    """Admin view for PipelineRun."""

    list_display = [
        "id",
        "status",
        "triggered_by",
        "started_at",
        "finished_at",
        "records_created",
        "records_updated",
    ]
    list_filter = ["status", "triggered_by"]
    readonly_fields = [
        "started_at",
        "finished_at",
        "status",
        "records_created",
        "records_updated",
        "error_message",
    ]
    ordering = ["-started_at"]


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    """Admin view for Region."""

    list_display = ["region_id", "name", "slug", "updated_at"]
    list_filter = []
    search_fields = ["region_id", "name"]
    ordering = ["region_id"]
    readonly_fields = ["id", "slug", "created_at", "updated_at"]


class RegionBulletinInline(admin.TabularInline):
    """Inline display of regions on the Bulletin admin page."""

    model = RegionBulletin
    extra = 0
    readonly_fields = ["region", "region_name_at_time", "created_at"]
    verbose_name = "Bulletin Region"


@admin.register(Bulletin)
class BulletinAdmin(admin.ModelAdmin):
    """Admin view for Bulletin."""

    list_display = [
        "bulletin_id",
        "issued_at",
        "valid_from",
        "valid_to",
        "lang",
        "unscheduled",
        "updated_at",
    ]
    list_filter = ["lang", "unscheduled"]
    search_fields = ["bulletin_id"]
    ordering = ["-issued_at"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "raw_data_pretty",
        "next_update",
        "bulletin_id",
        "issued_at",
        "valid_from",
        "valid_to",
        "lang",
        "unscheduled",
        "updated_at",
    ]
    inlines = [RegionBulletinInline]
    exclude = ["raw_data"]

    @admin.display(description="Raw data")
    def raw_data_pretty(self, obj: Bulletin) -> str:
        """Render raw_data as syntax-highlighted, indented JSON."""
        formatted = json.dumps(obj.raw_data, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f5f5f5;'
            "padding:0.75rem;border-radius:4px;font-size:0.8rem;"
            'white-space:pre-wrap;word-break:break-word">{}</pre>',
            formatted,
        )
