"""
tests/observations/test_admin.py — Admin registration smoke test for observations.

Verifies that FieldObservation is registered with Django admin and that the
admin class has the expected configuration, including the SNOW-330 fields
(location_source, gps_latitude, gps_longitude) and the SNOW-333 user FK
(replacing the old subscriber FK), and that both admin deletes erase the
report's minted anonymous Location (SNOW-1170).
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.locations.models import Location
from apps.observations.admin import FieldObservationAdmin
from apps.observations.models import FieldObservation
from tests.factories import FieldObservationFactory, LocationFactory


class TestFieldObservationAdminRegistration:
    """FieldObservationAdmin is registered and configured correctly."""

    def test_field_observation_is_registered(self) -> None:
        """FieldObservation is registered with the default admin site."""
        assert FieldObservation in admin.site._registry

    def test_admin_class_is_field_observation_admin(self) -> None:
        """The registered admin class is FieldObservationAdmin."""
        registered = admin.site._registry[FieldObservation]
        assert isinstance(registered, FieldObservationAdmin)

    def test_list_display_includes_user(self) -> None:
        """list_display includes user for easy identification (SNOW-333)."""
        registered = admin.site._registry[FieldObservation]
        assert "user" in registered.list_display

    def test_list_display_excludes_subscriber(self) -> None:
        """list_display no longer includes the old subscriber field (SNOW-333)."""
        registered = admin.site._registry[FieldObservation]
        assert "subscriber" not in registered.list_display

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

    def test_readonly_fields_includes_user(self) -> None:
        """user is read-only (no editing user-generated content) (SNOW-333)."""
        registered = admin.site._registry[FieldObservation]
        assert "user" in registered.readonly_fields

    def test_readonly_fields_excludes_subscriber(self) -> None:
        """readonly_fields no longer references the old subscriber field (SNOW-333)."""
        registered = admin.site._registry[FieldObservation]
        assert "subscriber" not in registered.readonly_fields

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

    def test_search_fields_includes_user_email(self) -> None:
        """search_fields includes user__email for lookup by email (SNOW-333)."""
        registered = admin.site._registry[FieldObservation]
        assert "user__email" in registered.search_fields


def _admin() -> FieldObservationAdmin:
    """Return the registered FieldObservationAdmin instance."""
    registered = admin.site._registry[FieldObservation]
    assert isinstance(registered, FieldObservationAdmin)
    return registered


@pytest.mark.django_db
class TestAdminDeletesEraseTheLocation:
    """Staff deletes sweep the coordinate a report minted (SNOW-1170)."""

    def test_delete_model_erases_the_anonymous_location(self) -> None:
        """The single-object delete goes through the service."""
        location = LocationFactory.create(anonymous=True)
        observation = FieldObservationFactory.create(location=location)
        observation_pk = observation.pk

        _admin().delete_model(RequestFactory().post("/"), observation)

        assert not FieldObservation.objects.filter(pk=observation_pk).exists()
        assert not Location.objects.filter(pk=location.pk).exists()

    def test_delete_queryset_erases_every_anonymous_location(self) -> None:
        """The bulk delete sweeps each location, leaving a named one."""
        anonymous = [LocationFactory.create(anonymous=True) for _ in range(2)]
        mont_fort = LocationFactory.create(name="Mont Fort")
        for location in [*anonymous, mont_fort]:
            FieldObservationFactory.create(location=location)

        _admin().delete_queryset(
            RequestFactory().post("/"), FieldObservation.objects.all()
        )

        assert not FieldObservation.objects.exists()
        assert not Location.objects.filter(
            pk__in=[location.pk for location in anonymous]
        ).exists()
        assert Location.objects.filter(pk=mont_fort.pk).exists()

    def test_the_delete_selected_action_erases_the_locations(
        self, admin_client: Client
    ) -> None:
        """The changelist's "delete selected" action reaches delete_queryset."""
        locations = [LocationFactory.create(anonymous=True) for _ in range(2)]
        observations = [
            FieldObservationFactory.create(location=location) for location in locations
        ]

        response = admin_client.post(
            reverse("admin:observations_fieldobservation_changelist"),
            {
                "action": "delete_selected",
                ACTION_CHECKBOX_NAME: [str(o.pk) for o in observations],
                "post": "yes",
            },
        )

        assert response.status_code == 302
        assert not FieldObservation.objects.exists()
        assert not Location.objects.filter(
            pk__in=[location.pk for location in locations]
        ).exists()
