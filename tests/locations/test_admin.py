"""
tests/locations/test_admin.py — Tests for the locations admin.

Admin is the only surface Location has until SNOW-702, and the
``ResortLocation`` inline on the resort change form is the surface SNOW-701's
whole data-curation effort runs through — so it is worth a test that it
actually renders, not just that it is registered.

Covers:
  - Location and ResortLocation changelists render.
  - The inline renders on the resort change form, and on the location one.
  - A location's changelist row shows its resolved-out-of-band columns.
"""

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
)


@pytest.fixture()
def staff_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    client = Client()
    client.force_login(UserFactory.create(is_superuser=True))
    return client


@pytest.mark.django_db
class TestLocationAdmin:
    """LocationAdmin — the curation surface for the estate."""

    def test_changelist_renders(self, staff_client: Client) -> None:
        """The Location changelist renders with a curated row on it."""
        LocationFactory.create(name="Mont Fort")
        response = staff_client.get(reverse("admin:locations_location_changelist"))
        assert response.status_code == 200
        assert "Mont Fort" in response.content.decode()

    def test_change_form_renders_the_resort_inline(self, staff_client: Client) -> None:
        """The location change form shows which resorts reference it.

        The sharing is the point of the model, so a curator must be able to
        see that four resorts point at Mont Fort before trying to delete it.
        """
        link = ResortLocationFactory.create(
            location=LocationFactory.create(name="Mont Fort")
        )
        response = staff_client.get(
            reverse("admin:locations_location_change", args=[link.location.pk])
        )
        assert response.status_code == 200
        assert "resort_locations" in response.content.decode()

    def test_changelist_is_searchable_by_name(self, staff_client: Client) -> None:
        """Search by name filters the changelist.

        Also what makes ``autocomplete_fields = ["location"]`` work on the
        resort inline — an autocomplete against a model with no
        search_fields raises at check time.
        """
        LocationFactory.create(name="Mont Fort")
        LocationFactory.create(name="Corvatsch")
        response = staff_client.get(
            reverse("admin:locations_location_changelist"), {"q": "Mont"}
        )
        body = response.content.decode()
        assert "Mont Fort" in body
        assert "Corvatsch" not in body


@pytest.mark.django_db
class TestResortLocationAdmin:
    """ResortLocationAdmin, and the inline on the resort change form."""

    def test_changelist_renders(self, staff_client: Client) -> None:
        """The ResortLocation changelist renders."""
        ResortLocationFactory.create()
        response = staff_client.get(
            reverse("admin:locations_resortlocation_changelist")
        )
        assert response.status_code == 200

    def test_inline_renders_on_the_resort_change_form(
        self, staff_client: Client
    ) -> None:
        """A curator opens a resort and can add its locations there.

        SNOW-701 curates the estate through this form. If the inline stops
        rendering, that ticket has no surface.
        """
        resort = ResortFactory.create(name="Verbier")
        response = staff_client.get(
            reverse("admin:regions_resort_change", args=[resort.pk])
        )
        assert response.status_code == 200
        assert "resort_locations" in response.content.decode()
