"""
tests/bulletins/management/commands/test_send_bulletin_alerts.py — The send command.

Read-only without --commit, the night hold, the register tally, and a
failed account making the run exit non-zero.
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Account
from apps.bulletins.models import BulletinAlert
from apps.bulletins.services.alerts import ALERT_TZ
from apps.core.models import CommandRun
from tests.factories import (
    AccountFactory,
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
)

COMMAND = "apps.bulletins.management.commands.send_bulletin_alerts"
EVENING = datetime.datetime(2026, 1, 16, 18, 0, tzinfo=ALERT_TZ)
NIGHT = datetime.datetime(2026, 1, 16, 23, 0, tzinfo=ALERT_TZ)


@pytest.fixture()
def follower(db: Any) -> Account:
    """Return an account following a region with tomorrow's bulletin out."""
    account = AccountFactory.create(
        notify_when=Account.NotifyWhen.NEW_BULLETIN,
        alerts_enabled_at=EVENING - datetime.timedelta(days=3),
    )
    region = MicroRegionFactory.create(name="Verbier")
    FavouriteFactory.create(user=account.user, region=region, region_pin=True)
    bulletin = BulletinFactory.create(render_model={"danger": {"key": "moderate"}})
    RegionDayRatingFactory.create(
        region=region, date=datetime.date(2026, 1, 17), source_bulletin=bulletin
    )
    # Not following anything, alerts off: never walked.
    AccountFactory.create()
    return account


def run(*args: str, now: datetime.datetime = EVENING) -> str:
    """Call the command at ``now`` and return stdout."""
    out = StringIO()
    with patch(f"{COMMAND}.timezone.now", return_value=now):
        call_command("send_bulletin_alerts", *args, stdout=out)
    return out.getvalue()


def test_dry_run_writes_nothing(follower: Account) -> None:
    """Without --commit the run reports and writes nothing."""
    out = run()
    assert "[READ-ONLY]" in out
    assert "Verbier · Saturday: Moderate (2)" in out
    assert not BulletinAlert.objects.exists()
    tally = CommandRun.objects.latest("id").counts
    assert tally["accounts"] == 1
    assert tally["messages"] == 1


def test_commit_sends_and_logs(follower: Account) -> None:
    """--commit delivers and writes the send log."""
    with patch("apps.bulletins.services.alerts.send_alert_email") as email:
        run("--commit")
    email.assert_called_once()
    assert BulletinAlert.objects.filter(account=follower).exists()
    assert CommandRun.objects.latest("id").counts["emails"] == 1


def test_night_hold(follower: Account) -> None:
    """Overnight nothing is evaluated, unless the hold is overridden."""
    assert "Night hold" in run("--commit", now=NIGHT)
    assert not BulletinAlert.objects.exists()
    with patch("apps.bulletins.services.alerts.send_alert_email"):
        run("--commit", "--ignore-night-hold", now=NIGHT)
    assert BulletinAlert.objects.exists()


def test_failed_account_exits_non_zero(follower: Account) -> None:
    """One account raising fails the run, after the others are tried."""
    with (
        patch(f"{COMMAND}.evaluate_account", side_effect=RuntimeError("boom")),
        pytest.raises(CommandError),
    ):
        run("--commit")
