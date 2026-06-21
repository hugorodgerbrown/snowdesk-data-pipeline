"""
observations/admin.py — Django admin registration for the observations app.

Provides a read-mostly admin view for ``FieldObservation`` rows — field
reports are user-generated content and should not be edited by staff
except to delete spam or test data.
"""

import logging

from django.contrib import admin

from .models import FieldObservation

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
