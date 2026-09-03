"""
tests/favourites/management/commands/test_backfill_subscriptions_to_region_pins.py

Covers ``backfill_subscriptions_to_region_pins`` (SNOW-802):
  - A dry run counts the rows and writes nothing.
  - ``--commit`` creates one region pin per Subscription row, leaving the
    Subscription row in place (SNOW-805 drops the table separately).
  - A second run finds every pin already present and creates nothing.
  - The favourites cap does not apply — a user's regions are never dropped.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import Subscription
from apps.favourites.models import Favourite
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    MicroRegionFactory,
    SubscriptionFactory,
)

COMMAND = "backfill_subscriptions_to_region_pins"


@pytest.mark.django_db
class TestBackfillSubscriptionsToRegionPins:
    """--commit converts; a bare run only reports."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit the rows are counted and no pin is created."""
        SubscriptionFactory.create()
        out = StringIO()

        call_command(COMMAND, stdout=out)

        assert not Favourite.objects.region_pins().exists()
        assert "1 region pin(s) would be created" in out.getvalue()
        assert "No data written" in out.getvalue()

    def test_commit_creates_a_region_pin_per_row(self) -> None:
        """Each (account, region) row becomes that user's region pin."""
        account = AccountFactory.create()
        first = MicroRegionFactory.create(name="Valais")
        second = MicroRegionFactory.create(name="Alpstein")
        SubscriptionFactory.create(account=account, region=first)
        SubscriptionFactory.create(account=account, region=second)
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        pins = Favourite.objects.for_user(account.user).region_pins()
        assert {pin.region for pin in pins} == {first, second}
        assert all(pin.is_region_pin for pin in pins)
        assert "2 region pin(s) created, 0 already present, 0 failed" in out.getvalue()
        # The Subscription rows stay: the table drop is its own deploy.
        assert Subscription.objects.count() == 2

    def test_second_run_creates_nothing(self) -> None:
        """Idempotent: an existing pin is reported, not duplicated."""
        SubscriptionFactory.create()
        call_command(COMMAND, "--commit", verbosity=0)
        out = StringIO()

        call_command(COMMAND, "--commit", stdout=out)

        assert Favourite.objects.region_pins().count() == 1
        assert "0 region pin(s) created, 1 already present" in out.getvalue()

    @override_settings(FAVOURITES_MAX_PER_USER=1)
    def test_the_cap_does_not_apply(self) -> None:
        """A user at the favourites cap still gets every region."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user)
        SubscriptionFactory.create(account=account)

        call_command(COMMAND, "--commit", verbosity=0)

        assert Favourite.objects.for_user(account.user).region_pins().count() == 1
