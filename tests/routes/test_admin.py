"""
tests/routes/test_admin.py — Admin registration smoke test for routes.

Verifies that Route is registered with Django admin and that the admin
class is read-mostly, mirroring FavouriteAdmin.
"""

from __future__ import annotations

from django.contrib import admin

from apps.routes.admin import RouteAdmin
from apps.routes.models import Route


class TestRouteAdminRegistration:
    """RouteAdmin is registered and configured correctly."""

    def test_route_is_registered(self) -> None:
        """Route is registered with the default admin site."""
        assert Route in admin.site._registry

    def test_admin_class_is_route_admin(self) -> None:
        """The registered admin class is RouteAdmin."""
        assert isinstance(admin.site._registry[Route], RouteAdmin)

    def test_list_display_includes_user_and_name(self) -> None:
        """list_display includes user and name for easy identification."""
        registered = admin.site._registry[Route]
        assert "user" in registered.list_display
        assert "name" in registered.list_display

    def test_list_display_includes_the_derived_figures(self) -> None:
        """list_display surfaces distance, ascent and point count."""
        registered = admin.site._registry[Route]
        assert "distance_m" in registered.list_display
        assert "ascent_m" in registered.list_display
        assert "point_count" in registered.list_display

    def test_list_display_excludes_the_geometry(self) -> None:
        """points is not a changelist column — it is the whole track."""
        registered = admin.site._registry[Route]
        assert "points" not in registered.list_display

    def test_readonly_fields_includes_user_and_geometry(self) -> None:
        """User-generated content is not staff-editable."""
        registered = admin.site._registry[Route]
        assert "user" in registered.readonly_fields
        assert "points" in registered.readonly_fields
        assert "bounds" in registered.readonly_fields

    def test_search_fields_includes_user_email(self) -> None:
        """search_fields includes user__email for lookup by email."""
        registered = admin.site._registry[Route]
        assert "user__email" in registered.search_fields
