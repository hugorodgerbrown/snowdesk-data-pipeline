"""
apps/favourites/admin.py — Django admin registration for the favourites app.

Provides a read-mostly admin view for ``Favourite`` rows — saved pins are
user-generated content and should not be edited by staff except to delete
spam or test data. Both admin deletes go through ``delete_favourite_instance``
so a deleted pin's anonymous ``Location`` goes with it (SNOW-1293).
"""

import logging

from django.contrib import admin
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import Favourite
from .services import delete_favourite_instance

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
        "created_at",
    ]
    list_filter = ["alerts", "region", "created_at"]
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
        "region",
        "resort",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"

    def delete_model(self, request: HttpRequest, obj: Favourite) -> None:
        """Delete one favourite through the service, sweeping its location.

        Args:
            request: The admin request.
            obj: The favourite being deleted.

        """
        delete_favourite_instance(obj)

    def delete_queryset(
        self, request: HttpRequest, queryset: QuerySet[Favourite]
    ) -> None:
        """Delete the selected favourites one at a time, sweeping each location.

        Per-row rather than one SQL delete, so each anonymous location is
        probed as its last referent goes; all rows commit together.

        Args:
            request: The admin request.
            queryset: The favourites selected for deletion.

        """
        with transaction.atomic():
            for favourite in queryset.select_related("location"):
                delete_favourite_instance(favourite)
