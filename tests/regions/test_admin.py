"""
tests/regions/test_admin.py — ResortAdmin's pin handling (SNOW-1144).

Covers:
  ResortAdmin.save_model — routes the edited coordinate through
                           set_resort_pin: creates, moves in place, clears.
  ResortAdminForm.clean  — refuses half a coordinate pair.
  change page            — renders with the pin Location read-only.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.regions.admin import ResortAdmin, ResortAdminForm
from apps.regions.models import Resort
from tests.factories import MicroRegionFactory, ResortFactory, UserFactory

pytestmark = pytest.mark.django_db


def _save(resort: Resort, **changes: Any) -> None:
    """Apply ``changes`` to ``resort`` and save it as the admin would."""
    for field, value in changes.items():
        setattr(resort, field, value)
    request = RequestFactory().post("/admin/")
    request.user = UserFactory.create(is_superuser=True)
    ResortAdmin(Resort, site).save_model(request, resort, form=None, change=True)


class TestSaveModel:
    """The admin's coordinate edits reach the pin Location."""

    def test_setting_coordinates_creates_the_pin(self) -> None:
        """An unpinned resort given a pair gets a Location there."""
        resort = ResortFactory.create()

        _save(resort, latitude=46.2, longitude=7.3)

        resort.refresh_from_db()
        assert resort.location is not None
        assert (resort.location.latitude, resort.location.longitude) == (46.2, 7.3)

    def test_editing_coordinates_moves_the_same_location(self) -> None:
        """A corrected coordinate moves the existing row in place."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        original_pk = resort.location_id

        _save(resort, latitude=46.3, longitude=7.5)

        resort.refresh_from_db()
        assert resort.location_id == original_pk
        assert resort.location is not None
        assert (resort.location.latitude, resort.location.longitude) == (46.3, 7.5)

    def test_clearing_coordinates_clears_the_pin(self) -> None:
        """Blanking both fields clears the FK."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)

        _save(resort, latitude=None, longitude=None)

        resort.refresh_from_db()
        assert resort.location is None


class TestResortAdminForm:
    """Half a pair is a form error, not a save-time exception."""

    def _data(self, **overrides: Any) -> dict[str, Any]:
        """Return a minimal valid form payload."""
        region = MicroRegionFactory.create()
        data: dict[str, Any] = {
            "name": "Verbier",
            "kind": Resort.Kind.RESORT,
            "tier": Resort.Tier.STANDARD,
            "region": region.pk,
            "canton": "VS",
        }
        data.update(overrides)
        return data

    def test_a_latitude_alone_is_refused(self) -> None:
        """Only one half of the pair is an error."""
        form = ResortAdminForm(data=self._data(latitude="46.1"))
        assert not form.is_valid()
        assert "both latitude and longitude" in str(form.errors)

    def test_a_full_pair_is_accepted(self) -> None:
        """Both halves validate."""
        form = ResortAdminForm(data=self._data(latitude="46.1", longitude="7.4"))
        assert form.is_valid(), form.errors


def test_change_page_shows_the_pin_location() -> None:
    """The change page renders, with the pin Location among its fields."""
    client = Client()
    client.force_login(UserFactory.create(is_superuser=True, is_staff=True))
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    response = client.get(reverse("admin:regions_resort_change", args=[resort.pk]))

    assert response.status_code == 200
    assert "field-location" in response.content.decode()
