"""
tests/observations/test_admin.py — Admin registration smoke test for observations.

Verifies that FieldObservation is registered with Django admin and that the
admin class has the expected configuration.
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

    def test_list_display_includes_observation_types(self) -> None:
        """list_display includes observation_types."""
        registered = admin.site._registry[FieldObservation]
        assert "observation_types" in registered.list_display

    def test_readonly_fields_includes_subscriber(self) -> None:
        """subscriber is read-only (no editing user-generated content)."""
        registered = admin.site._registry[FieldObservation]
        assert "subscriber" in registered.readonly_fields

    def test_readonly_fields_includes_coordinates(self) -> None:
        """latitude and longitude are read-only."""
        registered = admin.site._registry[FieldObservation]
        assert "latitude" in registered.readonly_fields
        assert "longitude" in registered.readonly_fields
