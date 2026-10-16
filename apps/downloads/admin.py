"""
apps/downloads/admin.py — Django admin registration for the downloads app.

Provides a read-mostly admin view for ``DownloadArea`` rows. An area is
user-generated content — a record of what someone chose to take offline —
and should not be edited by staff except to delete test data. Mirrors
``RouteAdmin`` and ``FavouriteAdmin``.

``bbox`` is absent from ``list_display``: four floats in a changelist
column identify nothing a staff member could act on. ``region_id`` and
``name`` are what actually name a row, and one of the two is always
populated.

``terrain`` (SNOW-1072) is listed and filterable: whether a user opted in to
terrain data is a yes/no a staff member can read at a glance, and the
filter answers "how many areas carry it" without a query.
"""

import logging

from django.contrib import admin

from .models import DownloadArea

logger = logging.getLogger(__name__)


@admin.register(DownloadArea)
class DownloadAreaAdmin(admin.ModelAdmin):
    """Read-mostly admin view for DownloadArea.

    Staff can search and filter areas, but every meaningful field is
    read-only to preserve the integrity of user-generated content.
    """

    list_display = [
        "user",
        "kind",
        "area_id",
        "region_id",
        "name",
        "basemap_key",
        "terrain",
        "created_at",
    ]
    list_filter = ["kind", "basemap_key", "terrain", "created_at"]
    search_fields = [
        "user__email",
        "area_id",
        "region_id",
        "name",
    ]
    readonly_fields = [
        "user",
        "area_id",
        "kind",
        "region_id",
        "bbox",
        "basemap_key",
        "name",
        "terrain",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
