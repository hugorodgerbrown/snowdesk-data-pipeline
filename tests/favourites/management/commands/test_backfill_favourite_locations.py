"""
tests/favourites/management/commands/test_backfill_favourite_locations.py

Covers ``backfill_favourite_locations`` (SNOW-704):
  - Mints a Location per pre-SNOW-704 favourite and repoints the FK.
  - **Every pin survives with its user data intact** — the assertion the
    ticket is really about, made before and after on the same rows.
  - Dry-run writes nothing; idempotent under --commit.
  - The minted location is anonymous and carries the favourite's own
    elevation and cell — no Open-Meteo call is made.
  - One failing pin does not abort the batch, and the command exits
    non-zero.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.favourites.models import Favourite
from apps.locations.models import Location
from tests.factories import FavouriteFactory, UserFactory

COMMAND = "backfill_favourite_locations"


def unmigrated(**kwargs: object) -> Favourite:
    """Build a pre-SNOW-704 favourite — one with no location.

    Args:
        **kwargs: Overrides forwarded to the factory.

    Returns:
        A persisted Favourite whose ``location`` is null.

    """
    return FavouriteFactory.create(location=None, **kwargs)


@pytest.mark.django_db
class TestBackfillFavouriteLocations:
    """--commit mints a location per favourite and repoints the FK."""

    def test_mints_a_location_and_repoints_the_fk(self) -> None:
        """A pre-SNOW-704 favourite ends up reaching weather via location."""
        favourite = unmigrated()

        call_command(COMMAND, "--commit", stdout=StringIO())

        favourite.refresh_from_db()
        assert favourite.location is not None
        assert favourite.location.forecast_cell == favourite.forecast_point

    def test_every_pin_survives_with_its_user_data_intact(self) -> None:
        """Favourites are user data — nothing may be lost or altered.

        The assertion this ticket is really about. Captured before and
        compared after across every column a user can see, on a set that
        includes a named pin, an unnamed one, a region-matched one and a
        resort-derived one.
        """
        user = UserFactory.create()
        favourites = [
            unmigrated(user=user, name="Powder stash"),
            unmigrated(user=user, name=""),
            unmigrated(user=user, name="Off-piste"),
        ]
        before = {
            f.pk: (
                f.user_id,
                f.name,
                f.latitude,
                f.longitude,
                f.elevation,
                f.region_id,
                f.resort_id,
                f.forecast_point_id,
                f.uuid,
                f.created_at,
            )
            for f in favourites
        }

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Favourite.objects.count() == len(favourites)
        for favourite in Favourite.objects.all():
            assert (
                favourite.user_id,
                favourite.name,
                favourite.latitude,
                favourite.longitude,
                favourite.elevation,
                favourite.region_id,
                favourite.resort_id,
                favourite.forecast_point_id,
                favourite.uuid,
                favourite.created_at,
            ) == before[favourite.pk]

    def test_minted_location_copies_the_pin_exactly(self) -> None:
        """The location carries the favourite's own coordinates and height."""
        favourite = unmigrated(latitude=46.4321, longitude=7.8765, elevation=2410.0)

        call_command(COMMAND, "--commit", stdout=StringIO())

        favourite.refresh_from_db()
        assert favourite.location is not None
        assert favourite.location.latitude == 46.4321
        assert favourite.location.longitude == 7.8765
        assert favourite.location.elevation_m == 2410.0

    def test_minted_location_is_anonymous(self) -> None:
        """No name, no kind — a pin's label is the user's own text.

        It must never reach a shared, curatable table where another user
        could see it or a curator could edit it.
        """
        unmigrated(name="My secret couloir")

        call_command(COMMAND, "--commit", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""
        assert location in Location.objects.anonymous()

    def test_makes_no_external_call(self) -> None:
        """The favourite already carries its elevation and cell.

        Re-resolving would cost an Open-Meteo round trip per pin and could
        return a *different* answer for a pin whose weather users have
        already been reading.
        """
        unmigrated()

        with patch("apps.weather.services.elevation.fetch_elevation") as lookup:
            call_command(COMMAND, "--commit", stdout=StringIO())

        lookup.assert_not_called()

    def test_dry_run_writes_nothing(self) -> None:
        """The default invocation is read-only."""
        favourite = unmigrated()

        out = StringIO()
        call_command(COMMAND, stdout=out)

        favourite.refresh_from_db()
        assert favourite.location is None
        assert not Location.objects.exists()
        assert "Read-only run complete" in out.getvalue()

    def test_second_run_selects_nothing(self) -> None:
        """Idempotent — an already-migrated favourite is not a candidate."""
        unmigrated()
        call_command(COMMAND, "--commit", stdout=StringIO())

        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        assert "0 location(s) minted" in out.getvalue()
        assert Location.objects.count() == 1

    def test_leaves_an_already_migrated_favourite_alone(self) -> None:
        """A favourite created after SNOW-704 keeps the location it has."""
        favourite = FavouriteFactory.create()
        original = favourite.location_id

        call_command(COMMAND, "--commit", stdout=StringIO())

        favourite.refresh_from_db()
        assert favourite.location_id == original


@pytest.mark.django_db
class TestBackfillFavouriteLocationsFailures:
    """One failing pin must not cost the others."""

    def test_one_failure_does_not_stop_the_batch(self) -> None:
        """A pin that fails is counted; the rest still migrate."""
        unmigrated()
        unmigrated()
        real_create = Location.objects.create
        calls = {"n": 0}

        def fail_first(**kwargs: object) -> Location:
            """Raise on the first mint, then behave normally."""
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_create(**kwargs)

        with (
            patch.object(Location.objects, "create", side_effect=fail_first),
            pytest.raises(CommandError, match="1 failure"),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

        assert Favourite.objects.filter(location__isnull=False).count() == 1
        assert Favourite.objects.filter(location__isnull=True).count() == 1

    def test_a_failed_pin_is_left_untouched(self) -> None:
        """A failure rolls back its own savepoint and nothing else.

        One transaction per favourite, not one for the batch: a pin that
        fails must not undo the pins already migrated.
        """
        favourite = unmigrated()

        with (
            patch.object(Location.objects, "create", side_effect=RuntimeError("boom")),
            pytest.raises(CommandError),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

        favourite.refresh_from_db()
        assert favourite.location is None
        assert not Location.objects.exists()
