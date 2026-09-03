"""
tests/favourites/test_region_pins.py — the region pin (SNOW-802).

A region pin is a Favourite whose subject is the MicroRegion: no location, no
coordinate, no elevation. Covers:
  - the model predicate, the queryset partitions and the partial unique
    constraint;
  - the services — idempotent create, the cap, delete;
  - the toggle endpoint — 403 anonymous, 400 non-HTMX, 404 unknown region,
    create then delete, 409 at the cap, the returned control's state;
  - a region pin never appears in favourites.geojson and renders as a
    region row in the list — rating, dated bulletin link, no pencil, no
    zoom control;
  - the region tooltip carries the control for a signed-in user and a
    sign-in path for a visitor.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import IntegrityError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.favourites.models import Favourite
from apps.favourites.services import (
    FavouriteLimitReached,
    create_region_favourite,
    delete_region_favourite,
)
from tests.factories import (
    FavouriteFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    UserFactory,
)

HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _toggle_url(region_id: str) -> str:
    """The region pin toggle endpoint for one region."""
    return reverse("favourites:region_toggle", kwargs={"region_id": region_id})


@pytest.mark.django_db
class TestRegionPinModel:
    """The predicate, the partitions and the constraint."""

    def test_region_pin_predicate(self) -> None:
        """No location, no coordinate, a region — and nothing else is one."""
        region = MicroRegionFactory.create()
        pin = FavouriteFactory.create(region=region, region_pin=True)
        placed = FavouriteFactory.create(region=region)
        assert pin.is_region_pin
        assert not placed.is_region_pin
        assert list(Favourite.objects.region_pins()) == [pin]
        assert list(Favourite.objects.placed()) == [placed]

    def test_to_string_names_the_region(self) -> None:
        """A row with no coordinate cannot print one."""
        region = MicroRegionFactory.create(name="Valais")
        pin = FavouriteFactory.create(region=region, region_pin=True)
        assert pin.to_string().endswith("@ Valais")

    def test_one_region_pin_per_user_and_region(self) -> None:
        """The partial unique constraint fires on a second pin."""
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=user, region=region, region_pin=True)
        with pytest.raises(IntegrityError):
            FavouriteFactory.create(user=user, region=region, region_pin=True)

    def test_a_placed_pin_in_the_same_region_does_not_collide(self) -> None:
        """A placed pin that merely resolved to the region is a different row."""
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=user, region=region, region_pin=True)
        FavouriteFactory.create(user=user, region=region)
        FavouriteFactory.create(user=user, region=region)
        assert Favourite.objects.for_user(user).count() == 3


@pytest.mark.django_db
class TestRegionPinServices:
    """create_region_favourite / delete_region_favourite."""

    def test_create_mints_no_location_and_snapshots_the_name(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create(name="Valais")
        pin = create_region_favourite(user, region)
        assert pin.is_region_pin
        assert pin.location is None
        assert pin.name == "Valais"

    def test_create_is_idempotent(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        first = create_region_favourite(user, region)
        second = create_region_favourite(user, region)
        assert first.pk == second.pk

    @override_settings(FAVOURITES_MAX_PER_USER=1)
    def test_create_respects_the_cap_unless_told_not_to(self) -> None:
        user = UserFactory.create()
        FavouriteFactory.create(user=user)
        region = MicroRegionFactory.create()
        with pytest.raises(FavouriteLimitReached):
            create_region_favourite(user, region)
        assert create_region_favourite(user, region, enforce_cap=False).is_region_pin

    def test_delete_reports_whether_anything_went(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        assert delete_region_favourite(user, region) is False
        create_region_favourite(user, region)
        assert delete_region_favourite(user, region) is True
        assert not Favourite.objects.for_user(user).exists()


@pytest.mark.django_db
class TestRegionPinToggle:
    """POST /favourites/partials/region/<region_id>/toggle/."""

    def test_anonymous_gets_403(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        assert client.post(_toggle_url(region.region_id), **HTMX).status_code == 403

    def test_non_htmx_gets_400(self, client: Client) -> None:
        client.force_login(UserFactory.create())
        region = MicroRegionFactory.create()
        assert client.post(_toggle_url(region.region_id)).status_code == 400

    def test_unknown_region_gets_404(self, client: Client) -> None:
        client.force_login(UserFactory.create())
        assert client.post(_toggle_url("XX-0000"), **HTMX).status_code == 404

    def test_first_post_pins_second_unpins(self, client: Client) -> None:
        """The response is the control in its new state each time."""
        user = UserFactory.create()
        client.force_login(user)
        MicroRegionFactory.create(region_id="CH-4115")

        first = client.post(_toggle_url("ch-4115"), **HTMX)
        assert first.status_code == 200
        assert 'data-pinned="true"' in first.content.decode()
        assert Favourite.objects.for_user(user).region_pins().count() == 1

        second = client.post(_toggle_url("CH-4115"), **HTMX)
        assert second.status_code == 200
        assert 'data-pinned="false"' in second.content.decode()
        assert not Favourite.objects.for_user(user).exists()

    @override_settings(FAVOURITES_MAX_PER_USER=1)
    def test_cap_returns_409_with_the_limit_partial(self, client: Client) -> None:
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user)
        region = MicroRegionFactory.create()
        response = client.post(_toggle_url(region.region_id), **HTMX)
        assert response.status_code == 409


@pytest.mark.django_db
class TestRegionPinSurfaces:
    """Where a region pin does and does not appear."""

    def test_never_in_the_geojson_feed(self, client: Client) -> None:
        """No coordinate, no feature — the sheet is its only surface."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(
            user=user, region=MicroRegionFactory.create(), region_pin=True
        )
        placed = FavouriteFactory.create(user=user)

        data = client.get(reverse("favourites:geojson")).json()

        assert [f["properties"]["uuid"] for f in data["features"]] == [str(placed.uuid)]

    def test_list_renders_a_region_row(self, client: Client) -> None:
        """Region name, today's rating, a dated bulletin link, a trash — no more."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        RegionDayRatingFactory.create(
            region=region, date=timezone.localdate(), max_rating="considerable"
        )
        pin = FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:list"), **HTMX).content.decode()

        assert "Valais" in content
        assert 'data-testid="region-pin-rating"' in content
        assert 'data-testid="region-pin-bulletin-link"' in content
        assert region.get_absolute_url() in content
        assert f'data-favourite-rename="{pin.uuid}"' not in content
        assert "Zoom to Valais" not in content
        assert reverse("favourites:delete", kwargs={"uuid": pin.uuid}) in content

    def test_list_region_row_without_a_rating(self, client: Client) -> None:
        """No rating today still links to the bulletin."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(name="Valais")
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:list"), **HTMX).content.decode()

        assert 'data-testid="region-pin-rating"' not in content
        assert "No rating today" in content

    def test_region_tooltip_carries_the_control(self, client: Client) -> None:
        """Signed in: the toggle, in its current state. Signed out: a sign-in path."""
        region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
        anonymous = client.get(reverse("api:region_summary", args=["ch-4115"])).json()
        assert 'data-testid="region-pin-signin"' in anonymous["html"]
        assert 'data-testid="region-pin-toggle"' not in anonymous["html"]

        user = UserFactory.create()
        client.force_login(user)
        unpinned = client.get(reverse("api:region_summary", args=["ch-4115"])).json()
        assert 'data-testid="region-pin-toggle"' in unpinned["html"]
        assert 'data-pinned="false"' in unpinned["html"]

        FavouriteFactory.create(user=user, region=region, region_pin=True)
        pinned = client.get(reverse("api:region_summary", args=["ch-4115"])).json()
        assert 'data-pinned="true"' in pinned["html"]

    def test_card_for_a_region_pin_does_not_500(self, client: Client) -> None:
        """No elevation means no altitude verdict — the card still renders."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create()
        pin = FavouriteFactory.create(user=user, region=region, region_pin=True)
        response = client.get(
            reverse("favourites:card", kwargs={"uuid": pin.uuid}), **HTMX
        )
        assert response.status_code == 200
