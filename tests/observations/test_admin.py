"""
tests/observations/test_admin.py — Admin registration smoke test for observations.

Verifies that FieldObservation is registered with Django admin and that the
admin class has the expected configuration, including the SNOW-330 fields
(location_source, gps_latitude, gps_longitude).
"""

from __future__ import annotations

from django.contrib import admin

from observations.admin import FieldObservationAdmin
from observations.models import FieldObservation


class TestFieldObservationAdminRegistration:
    """FieldObservationAdmin is registered and configured correctly."""

    def test_field_observation_is_registered(self) -> None:
        """FieldObservation is registered with the default admin site."""
        assert FieldObservation in admin.site._registry

    def test_admin_class_is_field_observation_admin(self) -> None:
        """The registered admin class is FieldObservationAdmin."""
        registered = admin.site._registry[FieldObservation]
        assert isinstance(registered, FieldObservationAdmin)

    def test_list_display_includes_subscriber(self) -> None:
        """list_display includes subscriber for easy identification."""
        registered = admin.site._registry[FieldObservation]
        assert "subscriber" in registered.list_display

    def test_list_display_includes_observation_type(self) -> None:
        """list_display includes observation_type (singular)."""
        registered = admin.site._registry[FieldObservation]
        assert "observation_type" in registered.list_display

    def test_list_display_includes_location_source(self) -> None:
        """list_display includes location_source (SNOW-330)."""
        registered = admin.site._registry[FieldObservation]
        assert "location_source" in registered.list_display

    def test_list_display_includes_gps_coords(self) -> None:
        """list_display includes gps_latitude and gps_longitude (SNOW-330)."""
        registered = admin.site._registry[FieldObservation]
        assert "gps_latitude" in registered.list_display
        assert "gps_longitude" in registered.list_display

    def test_list_filter_includes_location_source(self) -> None:
        """list_filter includes location_source (SNOW-330)."""
        registered = admin.site._registry[FieldObservation]
        assert "location_source" in registered.list_filter

    def test_readonly_fields_includes_subscriber(self) -> None:
        """subscriber is read-only (no editing user-generated content)."""
        registered = admin.site._registry[FieldObservation]
        assert "subscriber" in registered.readonly_fields

    def test_readonly_fields_includes_coordinates(self) -> None:
        """latitude and longitude are read-only."""
        registered = admin.site._registry[FieldObservation]
        assert "latitude" in registered.readonly_fields
        assert "longitude" in registered.readonly_fields

    def test_readonly_fields_includes_location_source(self) -> None:
        """location_source is read-only (SNOW-330)."""
        registered = admin.site._registry[FieldObservation]
        assert "location_source" in registered.readonly_fields

    def test_readonly_fields_includes_gps_coords(self) -> None:
        """gps_latitude and gps_longitude are read-only (SNOW-330)."""
        registered = admin.site._registry[FieldObservation]
        assert "gps_latitude" in registered.readonly_fields
        assert "gps_longitude" in registered.readonly_fields
