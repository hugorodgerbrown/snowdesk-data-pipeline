"""
tests/favourites/test_services.py — Tests for favourites.services.

Covers:
  create_favourite — happy path (resolves ForecastPoint, sets elevation
    from the point, resolves region); region-null when the point falls
    outside every known boundary; per-user cap enforcement at exactly
    ``settings.FAVOURITES_MAX_PER_USER``.
  delete_favourite — owner-checked; the linked ForecastPoint row survives
    (PROTECT).

All Open-Meteo network calls are avoided by patching
``favourites.services.resolve_forecast_point``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from pytest_django.fixtures import SettingsWrapper

from bulletins.models import ForecastPoint
from favourites.models import Favourite
from favourites.services import (
    FavouriteLimitReached,
    create_favourite,
    delete_favourite,
)
from tests.factories import ForecastPointFactory, MicroRegionFactory, UserFactory


@pytest.mark.django_db
class TestCreateFavouriteHappyPath:
    """create_favourite resolves a ForecastPoint and region, then saves."""

    def test_creates_favourite_with_elevation_from_forecast_point(self) -> None:
        """elevation is taken from the resolved ForecastPoint, not re-fetched."""
        user = UserFactory.create()
        point = ForecastPointFactory.create(elevation=1834.0)

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(user, 46.1, 7.4, name="My pin")

        assert favourite.pk is not None
        assert favourite.forecast_point == point
        assert favourite.elevation == 1834.0
        assert favourite.name == "My pin"
        assert favourite.user == user

    def test_resolves_region_when_point_is_inside_a_boundary(self) -> None:
        """favourite.region is set when region_for_point finds a match."""
        user = UserFactory.create()
        point = ForecastPointFactory.create()
        region = MicroRegionFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=region),
        ):
            favourite = create_favourite(user, 46.1, 7.4)

        assert favourite.region == region

    def test_name_defaults_to_blank(self) -> None:
        """name defaults to an empty string when not supplied."""
        user = UserFactory.create()
        point = ForecastPointFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(user, 46.1, 7.4)

        assert favourite.name == ""


@pytest.mark.django_db
class TestCreateFavouriteRegionNull:
    """region is None when the point falls outside every known boundary."""

    def test_region_is_none_when_no_match(self) -> None:
        """A pin outside all boundaries is accepted with region=None."""
        user = UserFactory.create()
        point = ForecastPointFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(user, 89.9, 179.9)

        assert favourite.region is None


@pytest.mark.django_db
class TestCreateFavouriteCap:
    """Per-user cap is enforced at exactly settings.FAVOURITES_MAX_PER_USER."""

    def _create(self, user: User, n: int) -> Favourite:
        """Create one favourite for ``user`` via the service, mocks intact.

        ``n`` varies the coordinates fed to ``resolve_forecast_point`` (and
        the ForecastPoint each call creates) so successive calls don't trip
        the ``unique_together`` constraint on (lat_cell, lon_cell,
        elevation_band).
        """
        latitude = 46.1 + n * 0.05
        longitude = 7.4 + n * 0.05
        point = ForecastPointFactory.create(latitude=latitude, longitude=longitude)
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            return create_favourite(user, latitude, longitude)

    def test_25th_favourite_is_allowed(self, settings: SettingsWrapper) -> None:
        """A user with 24 existing favourites can create a 25th."""
        settings.FAVOURITES_MAX_PER_USER = 25
        user = UserFactory.create()
        for n in range(24):
            self._create(user, n)
        favourite = self._create(user, 24)
        assert favourite.pk is not None
        assert Favourite.objects.for_user(user).count() == 25

    def test_26th_favourite_raises(self, settings: SettingsWrapper) -> None:
        """A user with 25 existing favourites cannot create a 26th."""
        settings.FAVOURITES_MAX_PER_USER = 25
        user = UserFactory.create()
        for n in range(25):
            self._create(user, n)
        with pytest.raises(FavouriteLimitReached):
            self._create(user, 25)
        assert Favourite.objects.for_user(user).count() == 25

    def test_race_narrows_via_in_transaction_recheck(
        self, settings: SettingsWrapper
    ) -> None:
        """The in-transaction re-check also raises, even if the first check passed.

        Simulates another request creating a favourite between the first
        (pre-HTTP-call) cap check and the write — the count() call inside
        ``transaction.atomic()`` sees the now-reached cap and raises.
        """
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        point = ForecastPointFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
            patch("favourites.services.Favourite.objects.for_user") as mock_for_user,
        ):
            mock_for_user.return_value.count.side_effect = [0, 1]
            with pytest.raises(FavouriteLimitReached):
                create_favourite(user, 46.1, 7.4)


@pytest.mark.django_db
class TestDeleteFavourite:
    """delete_favourite — owner-checked; the ForecastPoint row survives."""

    def test_deletes_owned_favourite(self) -> None:
        """A user can delete their own favourite."""
        user = UserFactory.create()
        point = ForecastPointFactory.create()
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(user, 46.1, 7.4)

        delete_favourite(user, favourite.uuid)

        assert not Favourite.objects.filter(pk=favourite.pk).exists()

    def test_forecast_point_survives_deletion(self) -> None:
        """The linked ForecastPoint row is not deleted (PROTECT)."""
        user = UserFactory.create()
        point = ForecastPointFactory.create()
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(user, 46.1, 7.4)

        delete_favourite(user, favourite.uuid)

        assert ForecastPoint.objects.filter(pk=point.pk).exists()

    def test_cannot_delete_another_users_favourite(self) -> None:
        """Deleting a favourite owned by a different user raises DoesNotExist."""
        owner = UserFactory.create()
        other_user = UserFactory.create()
        point = ForecastPointFactory.create()
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            favourite = create_favourite(owner, 46.1, 7.4)

        with pytest.raises(Favourite.DoesNotExist):
            delete_favourite(other_user, favourite.uuid)
