"""
tests/favourites/test_services.py — Tests for favourites.services.

Covers:
  create_favourite — happy path (resolves ForecastPoint, sets elevation
    from the point, resolves region); region_for_point called with
    latitude first (SNOW-415 regression guard for the SNOW-426 coord-order
    change); region-null when the point falls outside every known
    boundary; per-user cap enforcement at exactly
    ``settings.FAVOURITES_MAX_PER_USER``.
  create_resort_favourite (SNOW-499) — a geocoded resort snapshots
    coords/region/name and sets the resort FK; an ungeocoded resort raises
    ResortNotGeocoded; a second call for the same (user, resort) is
    idempotent; the cap is shared with plain-pin favourites.
  delete_favourite — owner-checked; the linked ForecastPoint row survives
    (PROTECT).

All Open-Meteo network calls are avoided by patching
``favourites.services.resolve_forecast_point``.
"""

from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from pytest_django.fixtures import SettingsWrapper

from bulletins.models import ForecastPoint
from favourites.models import Favourite
from favourites.services import (
    FavouriteLimitReached,
    ResortNotGeocoded,
    create_favourite,
    create_resort_favourite,
    delete_favourite,
)
from tests.factories import (
    ForecastPointFactory,
    MicroRegionFactory,
    ResortFactory,
    UserFactory,
)


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

    def test_region_for_point_called_with_latitude_first(self) -> None:
        """region_for_point is called (latitude, longitude) — a SNOW-426 regression guard.

        ``region_for_point``'s signature became ``(lat, lon)`` in SNOW-426;
        ``create_favourite`` previously called it with the arguments
        transposed, silently resolving every new favourite's region from
        swapped coordinates.
        """
        user = UserFactory.create()
        point = ForecastPointFactory.create()

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch(
                "favourites.services.region_for_point", return_value=None
            ) as mock_rfp,
        ):
            create_favourite(user, 46.1, 7.4)

        mock_rfp.assert_called_once_with(46.1, 7.4)

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
class TestCreateResortFavourite:
    """create_resort_favourite (SNOW-499) — snapshot + idempotent + cap-shared."""

    def test_geocoded_resort_snapshots_coords_region_and_name(self) -> None:
        """A geocoded resort's coords/region/name are snapshotted onto the row."""
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            name="Verbier", region=region, latitude=46.1, longitude=7.4
        )
        point = ForecastPointFactory.create(
            latitude=46.1, longitude=7.4, elevation=1500.0
        )

        with patch(
            "favourites.services.resolve_forecast_point", return_value=point
        ) as mock_resolve:
            favourite = create_resort_favourite(user, resort)

        mock_resolve.assert_called_once_with(46.1, 7.4)
        assert favourite.resort == resort
        assert favourite.region == region
        assert favourite.name == "Verbier"
        assert favourite.latitude == 46.1
        assert favourite.longitude == 7.4
        assert favourite.elevation == 1500.0
        assert favourite.forecast_point == point
        assert favourite.user == user

    def test_ungeocoded_resort_raises(self) -> None:
        """A resort with no latitude/longitude cannot be favourited."""
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=None, longitude=None)

        with pytest.raises(ResortNotGeocoded):
            create_resort_favourite(user, resort)

        assert not Favourite.objects.filter(user=user).exists()

    def test_second_call_is_idempotent(self) -> None:
        """A second create_resort_favourite call for the same pair returns the same row."""
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        with patch("favourites.services.resolve_forecast_point", return_value=point):
            first = create_resort_favourite(user, resort)
            second = create_resort_favourite(user, resort)

        assert first.pk == second.pk
        assert Favourite.objects.filter(user=user, resort=resort).count() == 1

    def test_cap_is_shared_with_plain_pin_favourites(
        self, settings: SettingsWrapper
    ) -> None:
        """A user already at the cap cannot favourite a resort either."""
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            create_favourite(user, 46.1, 7.4)

        resort = ResortFactory.create(latitude=47.0, longitude=8.0)
        resort_point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        with patch(
            "favourites.services.resolve_forecast_point", return_value=resort_point
        ):
            with pytest.raises(FavouriteLimitReached):
                create_resort_favourite(user, resort)

        assert Favourite.objects.for_user(user).count() == 1

    def test_in_transaction_recheck_raises(self, settings: SettingsWrapper) -> None:
        """The in-transaction re-check also raises, even if the first check passed.

        Mirrors ``TestCreateFavouriteCap.test_race_narrows_via_in_transaction_recheck``
        for the resort path — simulates another request creating a favourite
        between the first (pre-HTTP-call) cap check and the write.
        """
        settings.FAVOURITES_MAX_PER_USER = 1
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.Favourite.objects.for_user") as mock_for_user,
        ):
            mock_for_user.return_value.count.side_effect = [0, 1]
            with pytest.raises(FavouriteLimitReached):
                create_resort_favourite(user, resort)

    def test_integrity_error_race_backstop_returns_existing_row(self) -> None:
        """A race where a concurrent request wins is caught and its row returned.

        Simulates the window between this function's own pre-check (patched
        to report "not found") and its write: another request's favourite for
        the same (user, resort) already exists, so the real insert raises
        IntegrityError (the constraint is already violated) and the except
        block's own re-check finds and returns that row.
        """
        user = UserFactory.create()
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)

        winning_favourite = Favourite.objects.create(
            user=user,
            name=resort.name,
            latitude=46.1,
            longitude=7.4,
            elevation=point.elevation,
            forecast_point=point,
            region=resort.region,
            resort=resort,
        )

        empty_queryset = Mock()
        empty_queryset.first.return_value = None
        hit_queryset = Mock()
        hit_queryset.first.return_value = winning_favourite

        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch(
                "favourites.services.Favourite.objects.filter",
                side_effect=[empty_queryset, hit_queryset],
            ),
        ):
            result = create_resort_favourite(user, resort)

        assert result.pk == winning_favourite.pk
        # No second row was created — the real DB insert failed and the
        # backstop returned the pre-existing one.
        assert Favourite.objects.filter(user=user, resort=resort).count() == 1


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

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason=(
            "select_for_update row-locking is a no-op on SQLite; this race is "
            "only reproducible on PostgreSQL — enabled once SNOW-470 adds a "
            "Postgres test database."
        ),
    )
    def test_concurrent_creates_cannot_exceed_the_cap(
        self, settings: SettingsWrapper
    ) -> None:
        """Two concurrent creates at cap-1 → exactly one success, one rejected.

        Without the user-row lock, both transactions could count below the cap
        under READ COMMITTED and both insert. select_for_update() serialises
        them, so the second sees the first's insert and raises.
        """
        settings.FAVOURITES_MAX_PER_USER = 2
        user = UserFactory.create()
        # Seed one existing favourite — one slot left under the cap of 2.
        existing_point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        with (
            patch(
                "favourites.services.resolve_forecast_point",
                return_value=existing_point,
            ),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            create_favourite(user, 46.1, 7.4)

        point = ForecastPointFactory.create(latitude=48.0, longitude=9.0)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcomes_lock = threading.Lock()

        def worker(offset: float) -> None:
            """Attempt one concurrent create, recording the outcome."""
            try:
                barrier.wait(timeout=5)
                create_favourite(user, 48.0 + offset, 9.0 + offset)
                with outcomes_lock:
                    outcomes.append("created")
            except FavouriteLimitReached:
                with outcomes_lock:
                    outcomes.append("rejected")
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(offset,)) for offset in (0.1, 0.2)
        ]
        with (
            patch("favourites.services.resolve_forecast_point", return_value=point),
            patch("favourites.services.region_for_point", return_value=None),
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        assert sorted(outcomes) == ["created", "rejected"]
        assert Favourite.objects.for_user(user).count() == 2


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
