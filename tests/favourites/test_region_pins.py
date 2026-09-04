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

import datetime
from typing import Any

import pytest
from django.db import IntegrityError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

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
        """SNOW-814: the response is the resulting state, not a control."""
        user = UserFactory.create()
        client.force_login(user)
        MicroRegionFactory.create(region_id="CH-4115")

        first = client.post(_toggle_url("ch-4115"), **HTMX)
        assert first.status_code == 200
        assert first.json() == {"pinned": True}
        assert Favourite.objects.for_user(user).region_pins().count() == 1

        second = client.post(_toggle_url("CH-4115"), **HTMX)
        assert second.status_code == 200
        assert second.json() == {"pinned": False}
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
        """No coordinate, no feature — the region panel is its only surface."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(
            user=user, region=MicroRegionFactory.create(), region_pin=True
        )
        placed = FavouriteFactory.create(user=user)

        data = client.get(reverse("favourites:geojson")).json()

        assert [f["properties"]["uuid"] for f in data["features"]] == [str(placed.uuid)]

    def test_region_list_renders_a_region_row(self, client: Client) -> None:
        """The region's name and an unpin star. SNOW-814: nothing else."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        pin = FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:region_list"), **HTMX).content.decode()

        assert "Valais" in content
        assert f'data-favourite-rename="{pin.uuid}"' not in content
        assert reverse("favourites:delete", kwargs={"uuid": pin.uuid}) in content

    def test_region_list_row_carries_no_dated_metadata(self, client: Client) -> None:
        """SNOW-814: a date fetched once cannot follow the scrubber, so there is none.

        The row showed today's rating and a dated bulletin link. The summary
        directly above it repaints on ``snowdesk:date-changed`` and these rows
        do not, so scrubbing to another day put a live date and a dead one on
        the same surface. The list is navigation; the panel above answers for
        whichever date is showing.
        """
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        RegionDayRatingFactory.create(
            region=region, date=timezone.localdate(), max_rating="considerable"
        )
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:region_list"), **HTMX).content.decode()

        assert "data-row-meta" not in content
        assert 'data-testid="region-pin-rating"' not in content
        assert 'data-testid="region-pin-bulletin-link"' not in content
        assert region.get_absolute_url() not in content
        assert "No bulletin available" not in content

    def test_region_list_row_frames_the_region(self, client: Client) -> None:
        """SNOW-814: the name is a control carrying the region's id, not a coordinate."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(region_id="CH-4115", name="Valais")
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:region_list"), **HTMX).content.decode()

        assert 'data-row-focus-region="CH-4115"' in content
        assert "data-row-focus=" not in content
        assert "Show Valais on the map" in content

    def test_region_list_unpins_with_a_star_not_a_trash(self, client: Client) -> None:
        """SNOW-814: unpinning removes the pin, not the region — so it is not a trash."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(name="Valais")
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        content = client.get(reverse("favourites:region_list"), **HTMX).content.decode()

        assert 'data-testid="region-pin-unpin"' in content
        assert "Unpin Valais" in content
        assert 'aria-pressed="true"' in content
        assert "Remove Valais" not in content

    def test_region_list_empty_state_is_not_a_place_message(
        self, client: Client
    ) -> None:
        """Nothing pinned reads as nothing pinned, not as an empty favourites list."""
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user)

        content = client.get(reverse("favourites:region_list"), **HTMX).content.decode()

        assert 'data-testid="region-pin-list-empty"' in content
        assert "Pin a region and it will be listed here." in content

    def test_region_list_rejects_an_anonymous_request(self, client: Client) -> None:
        """It is a list of one user's own data."""
        response = client.get(reverse("favourites:region_list"), **HTMX)
        assert response.status_code == 403

    def test_region_pin_is_not_in_the_places_list(self, client: Client) -> None:
        """SNOW-814: the pins sheet lists places; a region is not one."""
        user = UserFactory.create()
        client.force_login(user)
        region = MicroRegionFactory.create(name="Valais")
        pin = FavouriteFactory.create(user=user, region=region, region_pin=True)
        placed = FavouriteFactory.create(user=user, name="Lac de Vaux")

        content = client.get(reverse("favourites:list"), **HTMX).content.decode()

        assert "Lac de Vaux" in content
        assert f"favourite-{pin.uuid}" not in content
        assert "Valais" not in content
        assert str(placed.uuid) in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_map_header_carries_the_pin_roundel(self, client: Client) -> None:
        """SNOW-814: signed in, a toggle; signed out, a way in. Never nothing.

        The roundel lives in the season ribbon's header, which only renders
        when the season has data — hence the frozen clock and the rating.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 2, 17), max_rating="high"
        )

        anonymous = client.get(reverse("public:home")).content.decode()
        assert 'id="map-region-pin-control"' in anonymous
        assert 'data-pin-state="signin"' in anonymous
        assert reverse("accounts:sign_in") in anonymous

        client.force_login(UserFactory.create())
        signed_in = client.get(reverse("public:home")).content.decode()
        assert 'data-pin-state="no-region"' in signed_in
        assert 'aria-pressed="false"' in signed_in
        # The state is derived client-side from the pinned list, so the
        # toggle endpoint arrives as a template with the region-id token in.
        assert "XX-0000/toggle/" in signed_in

    def test_region_tooltip_carries_no_pin_control(self, client: Client) -> None:
        """SNOW-814: the pin control left the summary for the ribbon header.

        It was a pill here from SNOW-802, which made it reachable only with the
        panel open and made "Pinned" a second statement of what the pinned list
        on the same surface was already showing. It is a roundel beside the chip
        now, and this summary is the region's facts again.
        """
        region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
        user = UserFactory.create()
        client.force_login(user)
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        html = client.get(reverse("api:region_summary", args=["ch-4115"])).json()[
            "html"
        ]

        assert "data-region-pin" not in html
        assert 'data-testid="region-tooltip-pin"' not in html
        assert "Pin this region" not in html

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
