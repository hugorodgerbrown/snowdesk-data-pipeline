"""
pipeline/admin.py — Django admin registrations for pipeline models.

Provides list views with key fields and read-only display of timing and
error information so that operators can inspect pipeline runs without
needing direct database access.
"""

from django.contrib import admin

from .models import DataRecord, PipelineRun


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


@admin.register(DataRecord)
class DataRecordAdmin(admin.ModelAdmin):
    """Admin view for DataRecord."""

    list_display = ["id", "external_id", "date", "value", "label", "updated_at"]
    list_filter = ["date"]
    search_fields = ["external_id", "label"]
    ordering = ["-date", "external_id"]
    readonly_fields = ["created_at", "updated_at"]
