"""
apps/map/admin.py — Django admin registration for the map app.

``Map3dShare`` is listed so a token can be revoked: deleting the row stops
every /map/3d/ link that carries it.
"""

import logging

from django.contrib import admin
from django.http import HttpRequest

from .models import Map3dShare

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
