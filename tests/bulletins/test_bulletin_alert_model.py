"""
tests/bulletins/test_bulletin_alert_model.py — The bulletin alert send log
and the querysets alerts read (SNOW-942, SNOW-939).
"""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError

from apps.accounts.models import Account
from apps.bulletins.models import BulletinAlert
from apps.favourites.models import Favourite
from tests.factories import (
    AccountFactory,
    BulletinAlertFactory,
    FavouriteFactory,
    MicroRegionFactory,
    ResortFactory,
)

DAY = datetime.date(2026, 1, 17)


@pytest.mark.django_db
class TestBulletinAlert:
    """The send log row."""

    def test_to_string(self) -> None:
        """__str__ delegates to to_string."""
        row = BulletinAlertFactory.create(target_date=DAY)
        assert str(row) == row.to_string()
        assert "2026-01-17" in str(row) and "SENT" in str(row)

    def test_one_row_per_state(self) -> None:
        """The same state for the same account, region and day is unique."""
        row = BulletinAlertFactory.create(target_date=DAY, fingerprint="abc")
        with pytest.raises(IntegrityError):
            BulletinAlertFactory.create(
                account=row.account,
                region=row.region,
                target_date=DAY,
                fingerprint="abc",
            )

    def test_for_day_newest_first_and_sent(self) -> None:
        """for_day orders newest first; sent() keeps only SENT rows."""
        first = BulletinAlertFactory.create(target_date=DAY)
        second = BulletinAlertFactory.create(
            account=first.account,
            region=first.region,
            target_date=DAY,
            outcome=BulletinAlert.Outcome.SKIPPED,
        )
        rows = BulletinAlert.objects.for_day(first.account_id, first.region_id, DAY)
        assert list(rows) == [second, first]
        assert list(rows.sent()) == [first]


@pytest.mark.django_db
def test_account_alerting() -> None:
    """Only active accounts with alerts on are walked."""
    on = AccountFactory.create(notify_when=Account.NotifyWhen.CHANGES)
    AccountFactory.create()
    inactive = AccountFactory.create(notify_when=Account.NotifyWhen.NEW_BULLETIN)
    inactive.user.is_active = False
    inactive.user.save()
    assert list(Account.objects.alerting()) == [on]


@pytest.mark.django_db
def test_favourite_alertable_and_alert_places() -> None:
    """Region pins and resorts can alert; dropped pins cannot; the switch filters."""
    account = AccountFactory.create()
    region = MicroRegionFactory.create()
    pin = FavouriteFactory.create(user=account.user, region=region, region_pin=True)
    resort = FavouriteFactory.create(
        user=account.user, resort=ResortFactory.create(region=region)
    )
    off = FavouriteFactory.create(
        user=account.user,
        region=MicroRegionFactory.create(),
        region_pin=True,
        alerts=False,
    )
    FavouriteFactory.create(user=account.user, region=region)  # dropped pin
    mine = Favourite.objects.for_user(account.user)
    assert set(mine.alertable()) == {pin, resort, off}
    assert set(mine.alert_places()) == {pin, resort}
