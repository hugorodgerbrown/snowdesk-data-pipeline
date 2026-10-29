"""
tests/favourites/test_admin.py — Admin registration smoke test for favourites.

Verifies that Favourite is registered with Django admin and that the admin
class is read-mostly, mirroring FieldObservationAdmin, and that both admin
deletes erase the anonymous Location a favourite minted (SNOW-1293).
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.favourites.admin import FavouriteAdmin
from apps.favourites.models import Favourite
from apps.locations.models import Location
from tests.factories import FavouriteFactory, LocationFactory, MicroRegionFactory


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


def _admin() -> FavouriteAdmin:
    """Return the registered FavouriteAdmin instance."""
    registered = admin.site._registry[Favourite]
    assert isinstance(registered, FavouriteAdmin)
    return registered


@pytest.mark.django_db
class TestFavouriteAdminDeletion:
    """Staff deletes sweep the coordinate a favourite minted (SNOW-1293)."""

    def test_delete_model_erases_the_anonymous_location(self) -> None:
        """The single-object delete goes through the service."""
        favourite = FavouriteFactory.create()
        favourite_pk = favourite.pk
        location_pk = favourite.location_id
        assert location_pk is not None

        _admin().delete_model(RequestFactory().post("/"), favourite)

        assert not Favourite.objects.filter(pk=favourite_pk).exists()
        assert not Location.objects.filter(pk=location_pk).exists()

    def test_delete_queryset_erases_every_anonymous_location(self) -> None:
        """The bulk delete sweeps each location, leaving a named one."""
        anonymous = [LocationFactory.create(anonymous=True) for _ in range(2)]
        mont_fort = LocationFactory.create(name="Mont Fort")
        for location in [*anonymous, mont_fort]:
            FavouriteFactory.create(location=location)

        _admin().delete_queryset(RequestFactory().post("/"), Favourite.objects.all())

        assert not Favourite.objects.exists()
        assert not Location.objects.filter(
            pk__in=[location.pk for location in anonymous]
        ).exists()
        assert Location.objects.filter(pk=mont_fort.pk).exists()

    def test_a_region_pin_deletes_cleanly(self) -> None:
        """A favourite with no location deletes without a sweep."""
        favourite = FavouriteFactory.create(
            region_pin=True, region=MicroRegionFactory.create()
        )
        favourite_pk = favourite.pk

        _admin().delete_model(RequestFactory().post("/"), favourite)

        assert not Favourite.objects.filter(pk=favourite_pk).exists()

    def test_a_shared_location_outlives_all_but_the_last_favourite(self) -> None:
        """Two favourites on one location keep it until the last goes."""
        location = LocationFactory.create(anonymous=True)
        first = FavouriteFactory.create(location=location)
        second = FavouriteFactory.create(location=location)

        _admin().delete_model(RequestFactory().post("/"), first)
        assert Location.objects.filter(pk=location.pk).exists()

        _admin().delete_model(RequestFactory().post("/"), second)
        assert not Location.objects.filter(pk=location.pk).exists()

    def test_the_delete_selected_action_erases_the_locations(
        self, admin_client: Client
    ) -> None:
        """The changelist's "delete selected" action reaches delete_queryset."""
        favourites = [FavouriteFactory.create() for _ in range(2)]
        location_pks = [f.location_id for f in favourites if f.location_id]
        assert len(location_pks) == len(favourites)

        response = admin_client.post(
            reverse("admin:favourites_favourite_changelist"),
            {
                "action": "delete_selected",
                ACTION_CHECKBOX_NAME: [str(f.pk) for f in favourites],
                "post": "yes",
            },
        )

        assert response.status_code == 302
        assert not Favourite.objects.exists()
        assert not Location.objects.filter(pk__in=location_pks).exists()
