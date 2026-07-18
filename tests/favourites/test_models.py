"""
tests/favourites/test_models.py — Tests for favourites.models.

Covers:
  Favourite to_string / __str__ format (with and without a name).
  FavouriteQuerySet.for_user — isolates favourites by owner.
  Meta.ordering — newest-first (inherited from BaseModel).
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from favourites.models import Favourite
from tests.factories import FavouriteFactory, UserFactory


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
