"""
tests/downloads/test_admin.py — Admin registration smoke test for downloads.

Verifies that DownloadArea is registered with Django admin and that the
admin class is read-mostly, mirroring RouteAdmin and FavouriteAdmin.
"""

from __future__ import annotations

from django.contrib import admin

from apps.downloads.admin import DownloadAreaAdmin
from apps.downloads.models import DownloadArea


class TestDownloadAreaAdminRegistration:
    """DownloadAreaAdmin is registered and configured correctly."""

    def test_download_area_is_registered(self) -> None:
        """DownloadArea is registered with the default admin site."""
        assert DownloadArea in admin.site._registry

    def test_admin_class_is_download_area_admin(self) -> None:
        """The registered admin class is DownloadAreaAdmin."""
        assert isinstance(admin.site._registry[DownloadArea], DownloadAreaAdmin)

    def test_list_display_names_the_row(self) -> None:
        """The columns that identify a row are all present."""
        registered = admin.site._registry[DownloadArea]
        for column in ("user", "kind", "area_id", "region_id", "name"):
            assert column in registered.list_display

    def test_list_display_excludes_the_bbox(self) -> None:
        """bbox is not a changelist column — four floats identify nothing."""
        registered = admin.site._registry[DownloadArea]
        assert "bbox" not in registered.list_display

    def test_every_field_is_read_only(self) -> None:
        """An area is user-generated content; staff delete it, never edit it."""
        registered = admin.site._registry[DownloadArea]
        for field in ("user", "area_id", "kind", "region_id", "bbox", "name"):
            assert field in registered.readonly_fields
