"""
apps/routes/admin.py — Django admin registration for the routes app.

Provides a read-mostly admin view for ``Route`` rows — an imported route is
user-generated content and should not be edited by staff except to delete
spam or test data. Mirrors ``FavouriteAdmin``.

``points`` is deliberately absent from ``list_display``: it is the whole
geometry, up to a couple of thousand coordinates, and rendering it in a
changelist column would be unreadable and slow. The derived columns
(``distance_m``, ``ascent_m``, ``point_count``) are what a staff member
actually needs to identify a row.
"""

import logging

from django.contrib import admin

from .models import Route

logger = logging.getLogger(__name__)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    """Read-mostly admin view for Route.

    Staff can search and filter routes, but all meaningful fields are
    read-only to preserve the integrity of user-generated content.
    """

    list_display = [
        "user",
        "name",
        "source_filename",
        "distance_m",
        "ascent_m",
        "point_count",
        "created_at",
    ]
    list_filter = ["created_at"]
    search_fields = [
        "user__email",
        "name",
        "source_filename",
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "uuid",
        "user",
        "name",
        "source_filename",
        "points",
        "distance_m",
        "ascent_m",
        "point_count",
        "bounds",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
