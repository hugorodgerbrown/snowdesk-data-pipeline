"""
tests/trips/test_services_shares.py — Tests for apps.trips.services.shares.

mint_trip_share:
  sets a token and an expiry, and is organiser-scoped;
  the expiry derives from the TRIP'S DATE, not the mint time — asserted
    against a trip far enough out that the two differ by months;
  minting again ROTATES, so the previous link stops working;
  the collision-retry loop exhausts into TripShareTokenCollision.

revoke_trip_share:
  nulls the token, is idempotent, and is organiser-scoped.

The whole file runs under a frozen clock. It is not only the expiry
assertions that need it: TripFactory's default date is fixed, so whether
any link is live at all depends on where "now" sits relative to it, and a
suite that passed in March and failed in September would be a suite about
the calendar.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.test import override_settings
from freezegun import freeze_time

from apps.trips.models import Trip
from apps.trips.services.shares import (
    TripShareTokenCollision,
    mint_trip_share,
    revoke_trip_share,
)
from tests.factories import TripFactory, UserFactory

# A fixed "now" for every expiry assertion. Well before the trip dates
# below, so a mint-time-derived expiry and a date-derived one land months
# apart rather than within rounding distance of each other.
_NOW = "2026-01-10T09:00:00+00:00"


@freeze_time(_NOW)
@pytest.mark.django_db
class TestMintTripShare:
    """Minting the one link a trip has."""

    def test_sets_a_token_and_an_expiry(self) -> None:
        """Both halves, or the link is not live."""
        trip = TripFactory.create()
        minted = mint_trip_share(trip.created_by, trip.uuid)
        assert minted.share_token
        assert minted.share_expires_at is not None
        assert minted.share_is_live

    def test_is_organiser_scoped(self) -> None:
        """A participant cannot mint a link for somebody else's trip."""
        trip = TripFactory.create()
        stranger = UserFactory.create()
        with pytest.raises(Trip.DoesNotExist):
            mint_trip_share(stranger, trip.uuid)

    @override_settings(TRIP_SHARE_MAX_AGE_DAYS=30)
    def test_the_expiry_derives_from_the_trips_date_not_the_mint_time(
        self,
    ) -> None:
        """A trip planned months out keeps a link until months after it.

        This is the whole difference from ROUTE_SHARE_MAX_AGE_DAYS. A
        mint-time window would put this link's expiry in February, two
        months before the trip it exists for.
        """
        trip = TripFactory.create(date=datetime.date(2026, 4, 18))
        minted = mint_trip_share(trip.created_by, trip.uuid)

        assert minted.share_expires_at is not None
        assert minted.share_expires_at.date() == datetime.date(2026, 5, 18)

    @override_settings(TRIP_SHARE_MAX_AGE_DAYS=30)
    def test_a_past_trips_link_is_already_dead(self) -> None:
        """The window closes relative to the day, so an old trip has none."""
        trip = TripFactory.create(date=datetime.date(2025, 12, 1))
        minted = mint_trip_share(trip.created_by, trip.uuid)

        assert not minted.share_is_live
        assert not Trip.objects.shared().filter(pk=trip.pk).exists()

    def test_minting_again_rotates_the_token(self) -> None:
        """The organiser's only revoke-and-reshare."""
        trip = TripFactory.create()
        first = mint_trip_share(trip.created_by, trip.uuid).share_token
        second = mint_trip_share(trip.created_by, trip.uuid).share_token

        assert first != second
        assert not Trip.objects.shared().filter(share_token=first).exists()
        assert Trip.objects.shared().filter(share_token=second).exists()

    def test_a_collision_is_retried_and_eventually_raises(self) -> None:
        """The ceiling is a runaway guard, and it is reachable.

        Forced by making every save collide. With ~66 bits of entropy this
        cannot happen in practice, which is exactly why it needs a test
        rather than a production incident to exercise it.
        """
        trip = TripFactory.create()
        with (
            patch.object(Trip, "save", side_effect=IntegrityError("token")),
            pytest.raises(TripShareTokenCollision),
        ):
            mint_trip_share(trip.created_by, trip.uuid)


@freeze_time(_NOW)
@pytest.mark.django_db
class TestRevokeTripShare:
    """Stopping the link."""

    def test_nulls_the_token(self) -> None:
        """Nulled, not expired — so the value is free for a later mint."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)

        revoked = revoke_trip_share(trip.created_by, trip.uuid)

        assert revoked.share_token is None
        assert revoked.share_expires_at is None
        assert not revoked.share_is_live

    def test_is_idempotent(self) -> None:
        """Revoking an unshared trip is not an error."""
        trip = TripFactory.create()
        assert revoke_trip_share(trip.created_by, trip.uuid).share_token is None
        assert revoke_trip_share(trip.created_by, trip.uuid).share_token is None

    def test_is_organiser_scoped(self) -> None:
        """A participant cannot kill the organiser's link."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        stranger = UserFactory.create()

        with pytest.raises(Trip.DoesNotExist):
            revoke_trip_share(stranger, trip.uuid)

        trip.refresh_from_db()
        assert trip.share_is_live
