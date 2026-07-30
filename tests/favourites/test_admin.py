"""
tests/favourites/test_admin.py — Admin registration smoke test for favourites.

Verifies that Favourite is registered with Django admin and that the admin
class is read-mostly, mirroring FieldObservationAdmin.
"""

from __future__ import annotations

from django.contrib import admin

from apps.favourites.admin import FavouriteAdmin
from apps.favourites.models import Favourite


class TestFavouriteAdminRegistration:
    """FavouriteAdmin is registered and configured correctly."""

    def test_favourite_is_registered(self) -> None:
        """Favourite is registered with the default admin site."""
        assert Favourite in admin.site._registry

    def test_admin_class_is_favourite_admin(self) -> None:
        """The registered admin class is FavouriteAdmin."""
        registered = admin.site._registry[Favourite]
        assert isinstance(registered, FavouriteAdmin)

    def test_list_display_includes_user_and_name(self) -> None:
        """list_display includes user and name for easy identification."""
        registered = admin.site._registry[Favourite]
        assert "user" in registered.list_display
        assert "name" in registered.list_display

    def test_list_display_includes_coordinates(self) -> None:
        """list_display includes latitude, longitude, and elevation."""
        registered = admin.site._registry[Favourite]
        assert "latitude" in registered.list_display
        assert "longitude" in registered.list_display
        assert "elevation" in registered.list_display

    def test_readonly_fields_includes_user(self) -> None:
        """user is read-only (no editing user-generated content)."""
        registered = admin.site._registry[Favourite]
        assert "user" in registered.readonly_fields

    def test_readonly_fields_includes_coordinates(self) -> None:
        """latitude and longitude are read-only."""
        registered = admin.site._registry[Favourite]
        assert "latitude" in registered.readonly_fields
        assert "longitude" in registered.readonly_fields

    def test_search_fields_includes_user_email(self) -> None:
        """search_fields includes user__email for lookup by email."""
        registered = admin.site._registry[Favourite]
        assert "user__email" in registered.search_fields
