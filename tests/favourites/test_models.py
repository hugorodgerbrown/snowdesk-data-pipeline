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
"""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.favourites.models import Favourite
from tests.factories import (
    FavouriteFactory,
    ResortFactory,
    UserFactory,
)


class TestFavouriteToString:
    """to_string and __str__ coverage."""

    def test_to_string_with_name(self) -> None:
        """to_string includes the user-supplied name when set."""
        favourite = FavouriteFactory.build(
            name="Home run", latitude=46.1, longitude=7.4
        )
        result = favourite.to_string()
        assert "Home run" in result

    def test_to_string_without_name_uses_coordinates(self) -> None:
        """to_string falls back to formatted coordinates when name is blank."""
        favourite = FavouriteFactory.build(name="", latitude=46.1, longitude=7.4)
        result = favourite.to_string()
        assert "46.10000,7.40000" in result

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
class TestFavouriteCoordinateConstraints:
    """SNOW-464: DB check constraints reject out-of-range coordinates.

    Belt-and-braces for a write path that bypasses the view-layer validator.
    """

    def test_latitude_out_of_range_raises_integrity_error(self) -> None:
        """A latitude beyond WGS-84 bounds is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FavouriteFactory.create(latitude=999.0)

    def test_longitude_out_of_range_raises_integrity_error(self) -> None:
        """A longitude beyond WGS-84 bounds is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FavouriteFactory.create(longitude=999.0)


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
