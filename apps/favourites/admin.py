"""
apps/favourites/admin.py — Django admin registration for the favourites app.

Provides a read-mostly admin view for ``Favourite`` rows — saved pins are
user-generated content and should not be edited by staff except to delete
spam or test data.
"""

import logging

from django.contrib import admin

from .models import Favourite

logger = logging.getLogger(__name__)


@admin.register(Favourite)
class FavouriteAdmin(admin.ModelAdmin):
    """Read-mostly admin view for Favourite.

    Staff can search and filter favourites, but all meaningful fields are
    read-only to preserve the integrity of user-generated content.
    """

    list_display = [
        "user",
        "name",
        "region",
        "resort",
        "latitude",
        "longitude",
        "elevation",
        "location",
        "forecast_point",
        "created_at",
    ]
    list_filter = ["region", "created_at"]
    search_fields = [
        "user__email",
        "name",
        "region__name",
        "region__region_id",
        "resort__name",
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "uuid",
        "user",
        "name",
        "latitude",
        "longitude",
        "elevation",
        "location",
        "forecast_point",
        "region",
        "resort",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
