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
        "subscriber",
        "region",
        "observation_types",
        "observed_at",
        "latitude",
        "longitude",
        "accuracy_radius_km",
        "created_at",
    ]
    list_filter = ["region", "observed_at"]
    search_fields = ["subscriber__user__email", "region__name", "region__region_id"]
    ordering = ["-observed_at"]
    readonly_fields = [
        "id",
        "uuid",
        "subscriber",
        "region",
        "latitude",
        "longitude",
        "accuracy_radius_km",
        "observed_at",
        "observation_types",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "observed_at"
