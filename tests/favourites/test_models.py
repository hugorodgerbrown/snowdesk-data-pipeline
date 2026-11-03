"""
tests/favourites/test_models.py — Tests for apps.favourites.models.

Covers:
  Favourite to_string / __str__ format (with and without a name).
  FavouriteQuerySet.for_user — isolates favourites by owner.
  Meta.ordering — newest-first (inherited from BaseModel).
  resort FK (SNOW-499) — partial-unique (user, resort) constraint; two
    different users may each favourite the same resort; two NULL-resort
    favourites never collide; deleting a Resort sets favourite.resort to
    NULL (SET_NULL) leaving the row intact.
  pin_elevation — read from the Location; None without one or its height.
  placed / region_pins — partition placed pins and region pins on
    ``location`` alone (SNOW-714).
"""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.favourites.models import Favourite
from apps.locations.models import Location
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    UserFactory,
)


class TestFavouriteToString:
    """to_string and __str__ coverage."""

    def test_to_string_with_name(self) -> None:
        """to_string includes the user-supplied name when set."""
        favourite = FavouriteFactory.build(name="Home run")
        result = favourite.to_string()
        assert "Home run" in result

    def test_to_string_without_name_uses_coordinates(self) -> None:
        """to_string falls back to formatted coordinates when name is blank."""
        favourite = FavouriteFactory.build(name="", latitude=46.1, longitude=7.4)
        result = favourite.to_string()
        assert "46.10000,7.40000" in result

    def test_to_string_reads_the_location(self) -> None:
        """The coordinate shown is the Location's, the pin's one coordinate."""
        location = LocationFactory.build(latitude=45.9, longitude=6.8)
        favourite = FavouriteFactory.build(name="", location=location)
        assert "45.90000,6.80000" in favourite.to_string()

    def test_to_string_without_a_location_or_region(self) -> None:
        """A row with neither still renders rather than raising."""
        favourite = FavouriteFactory.build(name="", location=None, region=None)
        assert favourite.to_string().endswith("@ unplaced pin")

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        favourite = FavouriteFactory.build()
        assert str(favourite) == favourite.to_string()


@pytest.mark.django_db
class TestFavouriteQuerySetForUser:
    """FavouriteQuerySet.for_user — isolates rows by owner."""

    def test_for_user_returns_only_that_users_favourites(self) -> None:
        """for_user excludes rows belonging to a different user."""
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        mine = FavouriteFactory.create(user=user_a)
        FavouriteFactory.create(user=user_b)

        result = list(Favourite.objects.for_user(user_a))
        assert result == [mine]

    def test_for_user_returns_empty_for_user_with_no_favourites(self) -> None:
        """for_user returns an empty queryset when the user has no rows."""
        user = UserFactory.create()
        assert list(Favourite.objects.for_user(user)) == []


@pytest.mark.django_db
class TestFavouriteOrdering:
    """Meta.ordering — newest-first (inherited from BaseModel)."""

    def test_ordering_is_newest_first(self) -> None:
        """Queryset is ordered -created_at (newest first)."""
        user = UserFactory.create()
        early = FavouriteFactory.create(user=user)
        early.created_at = timezone.now() - datetime.timedelta(hours=1)
        early.save(update_fields=["created_at"])
        late = FavouriteFactory.create(user=user)

        result = list(Favourite.objects.for_user(user))
        assert result == [late, early]


@pytest.mark.django_db
class TestFavouritePinElevation:
    """Favourite.pin_elevation — the height the card shows."""

    def test_the_location_height(self) -> None:
        """A pin whose Location was re-measured reports the new height."""
        favourite = FavouriteFactory.create(elevation=1500.0)
        Location.objects.filter(favourites=favourite).update(elevation_m=2213.0)

        assert Favourite.objects.get(pk=favourite.pk).pin_elevation == 2213.0

    def test_none_when_the_location_has_no_height(self) -> None:
        """A Location with no height yet gives the pin none."""
        favourite = FavouriteFactory.create(elevation=1500.0)
        Location.objects.filter(favourites=favourite).update(elevation_m=None)

        assert Favourite.objects.get(pk=favourite.pk).pin_elevation is None

    def test_region_pin_has_none(self) -> None:
        """A region pin (SNOW-802) has no height at all."""
        favourite = FavouriteFactory.create(
            region_pin=True, region=MicroRegionFactory.create()
        )

        assert favourite.pin_elevation is None


@pytest.mark.django_db
class TestFavouriteResortConstraint:
    """SNOW-499: partial-unique (user, resort) constraint + SET_NULL."""

    def test_duplicate_user_resort_pair_raises_integrity_error(self) -> None:
        """A second (user, resort) row for the same pair is rejected at the DB layer."""
        user = UserFactory.create()
        resort = ResortFactory.create()
        FavouriteFactory.create(user=user, resort=resort)

        with pytest.raises(IntegrityError), transaction.atomic():
            FavouriteFactory.create(user=user, resort=resort)

    def test_two_different_users_can_favourite_the_same_resort(self) -> None:
        """The constraint is per-user, not global to the resort."""
        resort = ResortFactory.create()
        user_a = UserFactory.create()
        user_b = UserFactory.create()

        FavouriteFactory.create(user=user_a, resort=resort)
        FavouriteFactory.create(user=user_b, resort=resort)

        assert Favourite.objects.filter(resort=resort).count() == 2

    def test_two_null_resort_favourites_do_not_collide(self) -> None:
        """The partial condition excludes NULL resorts from the uniqueness check."""
        user = UserFactory.create()

        FavouriteFactory.create(user=user, resort=None)
        FavouriteFactory.create(user=user, resort=None)

        assert Favourite.objects.filter(user=user, resort__isnull=True).count() == 2

    def test_deleting_resort_sets_favourite_resort_to_null(self) -> None:
        """SET_NULL: deleting the Resort degrades the favourite to a plain pin."""
        user = UserFactory.create()
        resort = ResortFactory.create()
        favourite = FavouriteFactory.create(user=user, resort=resort)

        resort.delete()
        favourite.refresh_from_db()

        assert favourite.resort_id is None
        assert Favourite.objects.filter(pk=favourite.pk).exists()


@pytest.mark.django_db
class TestDroppedPins:
    """FavouriteQuerySet.dropped_pins — the Favourites panel's rows (SNOW-1229)."""

    def test_keeps_a_dropped_pin(self) -> None:
        """A placed favourite with no resort is a dropped pin."""
        pin = FavouriteFactory.create()

        assert list(Favourite.objects.dropped_pins()) == [pin]

    def test_drops_a_resort_favourite(self) -> None:
        """A favourited resort is listed by search, not the panel."""
        FavouriteFactory.create(resort=ResortFactory.create())

        assert not Favourite.objects.dropped_pins().exists()

    def test_drops_a_region_pin(self) -> None:
        """A region pin has no coordinate, so it is no dropped pin."""
        FavouriteFactory.create(region_pin=True, region=MicroRegionFactory.create())

        assert not Favourite.objects.dropped_pins().exists()


@pytest.mark.django_db
class TestRetiredResortQuerysets:
    """SNOW-1404: the querysets that read a favourite's resort's retirement."""

    def test_listed_panel_rows_and_alertable(self) -> None:
        """A retired resort's favourite is panel-only, never listed or alerted."""
        user = UserFactory.create()
        live = FavouriteFactory.create(
            user=user, resort=ResortFactory.create(), alerts=True
        )
        gone = FavouriteFactory.create(
            user=user, resort=ResortFactory.create(retired=True), alerts=True
        )
        pin = FavouriteFactory.create(user=user)
        for favourite in (live, gone):
            assert favourite.resort is not None
            favourite.region = favourite.resort.region
            favourite.save()
        mine = Favourite.objects.for_user(user)

        assert set(mine.listed()) == {live, pin}
        assert set(mine.panel_rows()) == {gone, pin}
        assert set(mine.alertable()) == {live}
        assert gone.is_retired_resort and not live.is_retired_resort
        assert not pin.is_retired_resort


@pytest.mark.django_db
class TestPlacedAndRegionPins:
    """SNOW-714: ``location`` alone tells a placed pin from a region pin."""

    def test_the_two_kinds_partition(self) -> None:
        """Every placed pin is in ``placed()``, every region pin in ``region_pins()``."""
        region = MicroRegionFactory.create()
        pin = FavouriteFactory.create(region=region)
        resort = FavouriteFactory.create(resort=ResortFactory.create())
        region_pin = FavouriteFactory.create(region_pin=True, region=region)

        assert set(Favourite.objects.placed()) == {pin, resort}
        assert list(Favourite.objects.dropped_pins()) == [pin]
        assert list(Favourite.objects.region_pins()) == [region_pin]
        assert region_pin.is_region_pin
        assert not pin.is_region_pin

    def test_no_location_and_no_region_is_neither(self) -> None:
        """A row with neither is not placed and not a region pin."""
        stray = FavouriteFactory.create(location=None, region=None)

        assert not Favourite.objects.placed().filter(pk=stray.pk).exists()
        assert not Favourite.objects.region_pins().filter(pk=stray.pk).exists()
        assert not stray.is_region_pin
