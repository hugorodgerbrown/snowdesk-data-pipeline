"""
apps/map/admin.py — Django admin registration for the map app.

``Map3dShare`` is listed so a token can be revoked: deleting the row stops
every /map/3d/ link that carries it. ``Map3dDeviceTest`` (SNOW-1456) is
listed read-only, filtered by verdict and probe, so the 3D frame budget can
be read across the devices readers sent results from.
"""

import logging

from django.contrib import admin
from django.http import HttpRequest

from .models import Map3dDeviceTest, Map3dShare

logger = logging.getLogger(__name__)


@admin.register(Map3dShare)
class Map3dShareAdmin(admin.ModelAdmin):
    """Admin for Map3dShare: read the token, delete it to revoke."""

    list_display = ["token", "created_by", "created_at"]
    readonly_fields = ["token", "created_by", "uuid", "created_at", "updated_at"]
    search_fields = ["token", "created_by__email"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Tokens are minted by the page, never by hand.

        Args:
            request: The admin request.

        Returns:
            False.

        """
        return False


@admin.register(Map3dDeviceTest)
class Map3dDeviceTestAdmin(admin.ModelAdmin):
    """Admin for Map3dDeviceTest: the 3D test results, read-only."""

    list_display = [
        "created_at",
        "verdict",
        "gpu",
        "device_pixel_ratio",
        "pixel_ratio",
        "probe",
    ]
    list_filter = ["verdict", "probe"]
    search_fields = ["gpu", "user_agent"]
    readonly_fields = [
        "verdict",
        "gpu",
        "device_pixel_ratio",
        "pixel_ratio",
        "canvas_width",
        "canvas_height",
        "user_agent",
        "client_version",
        "probe",
        "overlays",
        "passes",
        "uuid",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Results come from devices, never by hand.

        Args:
            request: The admin request.

        Returns:
            False.

        """
        return False
