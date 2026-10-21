"""
apps/observations/admin.py — Django admin registration for the observations app.

Provides a read-mostly admin view for ``FieldObservation`` rows — field
reports are user-generated content and should not be edited by staff
except to delete spam or test data.

Both admin deletes — the single-object delete and the "delete selected"
bulk action — route through ``delete_observation_instance`` so a report's
minted anonymous ``Location`` is erased with it (SNOW-1170). The default
bulk action issues one SQL delete and would strand every coordinate.
"""

import logging

from django.contrib import admin
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import FieldObservation
from .services.observations import delete_observation_instance

logger = logging.getLogger(__name__)


@admin.register(FieldObservation)
class FieldObservationAdmin(admin.ModelAdmin):
    """Read-mostly admin view for FieldObservation.

    Staff can search and filter reports, but all meaningful fields are
    read-only to preserve the integrity of user-generated content.
    """

    list_display = [
        "user",
        "region",
        "observation_type",
        "location_source",
        "observed_at",
        "latitude",
        "longitude",
        "gps_latitude",
        "gps_longitude",
        "accuracy_radius_km",
        "created_at",
    ]
    list_filter = ["region", "observation_type", "location_source", "observed_at"]
    search_fields = ["user__email", "region__name", "region__region_id"]
    ordering = ["-observed_at"]
    readonly_fields = [
        "id",
        "uuid",
        "user",
        "region",
        "latitude",
        "longitude",
        "accuracy_radius_km",
        "gps_latitude",
        "gps_longitude",
        "location_source",
        "observed_at",
        "observation_type",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "observed_at"

    def delete_model(self, request: HttpRequest, obj: FieldObservation) -> None:
        """Delete one observation through the service, sweeping its location.

        Args:
            request: The admin request.
            obj: The observation being deleted.

        """
        delete_observation_instance(obj)

    def delete_queryset(
        self, request: HttpRequest, queryset: QuerySet[FieldObservation]
    ) -> None:
        """Delete the selected observations one at a time, sweeping each location.

        Per-row rather than one SQL delete, so each anonymous location is
        probed as its last referent goes; all rows commit together.

        Args:
            request: The admin request.
            queryset: The observations selected for deletion.

        """
        with transaction.atomic():
            for observation in queryset.select_related("location"):
                delete_observation_instance(observation)
