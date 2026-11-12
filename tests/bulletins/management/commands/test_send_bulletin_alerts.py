"""
tests/bulletins/management/commands/test_send_bulletin_alerts.py — The send command.

Read-only without --commit, the night hold, the register tally, a
failed account making the run exit non-zero, and the gate (SNOW-1491).
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from freezegun import freeze_time

from apps.accounts.models import Account
from apps.bulletins.models import BulletinAlert, RegionDayRating
from apps.bulletins.services.alerts import ALERT_TZ, evaluate_account
from apps.core.models import CommandRun
from tests.factories import (
    AccountFactory,
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
)

COMMAND = "apps.bulletins.management.commands.send_bulletin_alerts"
EVENING = datetime.datetime(2026, 1, 16, 18, 0, tzinfo=ALERT_TZ)
NIGHT = datetime.datetime(2026, 1, 16, 23, 0, tzinfo=ALERT_TZ)


def make_follower(name: str = "Verbier") -> Account:
    """Return an account following a region with tomorrow's bulletin out."""
    account = AccountFactory.create(
        notify_when=Account.NotifyWhen.NEW_BULLETIN,
        alerts_enabled_at=EVENING - datetime.timedelta(days=3),
    )
    region = MicroRegionFactory.create(name=name)
    FavouriteFactory.create(user=account.user, region=region, region_pin=True)
    bulletin = BulletinFactory.create(render_model={"danger": {"key": "moderate"}})
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    RegionDayRatingFactory.create(
        region=region, date=datetime.date(2026, 1, 17), source_bulletin=bulletin
    )
    return account


@pytest.fixture()
def follower(db: Any) -> Account:
    """Return an account following a region with tomorrow's bulletin out."""
    account = make_follower()
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
    assert "Verbier – Saturday bulletin" in out
    assert not BulletinAlert.objects.exists()
    tally = CommandRun.objects.latest("id").counts
    assert tally["accounts"] == 1
    assert tally["messages"] == 1


def test_commit_sends_and_logs(follower: Account) -> None:
    """--commit delivers and writes the send log."""
    run("--commit")
    assert len(mail.outbox) == 1
    assert BulletinAlert.objects.filter(account=follower).exists()
    assert CommandRun.objects.latest("id").counts["emails"] == 1


def test_night_hold(follower: Account) -> None:
    """Overnight nothing is evaluated, unless the hold is overridden."""
    assert "Night hold" in run("--commit", now=NIGHT)
    assert not BulletinAlert.objects.exists()
    run("--commit", "--ignore-night-hold", now=NIGHT)
    assert BulletinAlert.objects.exists()


def test_failed_account_exits_non_zero(follower: Account) -> None:
    """One account raising fails the run, after the others are tried."""
    with (
        patch(f"{COMMAND}.evaluate_account", side_effect=RuntimeError("boom")),
        pytest.raises(CommandError),
    ):
        run("--commit")


# ---------------------------------------------------------------------------
# The gate (SNOW-1491)
# ---------------------------------------------------------------------------

# The fixtures are written here, the afternoon before EVENING's run.
SETUP = EVENING - datetime.timedelta(hours=2)
LATER = EVENING + datetime.timedelta(hours=1)


def run_at(now: datetime.datetime, *args: str) -> dict[str, int]:
    """Call the command with the whole clock at ``now``; return its tally."""
    with freeze_time(now):
        call_command("send_bulletin_alerts", *args, stdout=StringIO())
    return CommandRun.objects.latest("id").counts


def publish(account: Account, key: str) -> None:
    """Replace tomorrow's bulletin for the account's pinned region."""
    region = account.user.favourites.get().region
    bulletin = BulletinFactory.create(render_model={"danger": {"key": key}})
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    RegionDayRating.objects.filter(region=region).update(source_bulletin=bulletin)


@pytest.fixture()
def followers(db: Any) -> list[Account]:
    """Return three followers of three regions, written at SETUP."""
    with freeze_time(SETUP):
        return [make_follower(name) for name in ("Verbier", "Arolla", "Zinal")]


def test_no_previous_run_evaluates_everyone(followers: list[Account]) -> None:
    """With no watermark yet every alerting account is evaluated."""
    tally = run_at(EVENING, "--commit")
    assert tally["accounts"] == 3
    assert tally["messages"] == 3
    assert (tally["held"], tally["gated"]) == (0, 0)


def test_nothing_changed_is_skipped(followers: list[Account]) -> None:
    """After a committed run with nothing new, nobody is looked at."""
    run_at(EVENING, "--commit")
    out = StringIO()
    with (
        freeze_time(LATER),
        patch(f"{COMMAND}.evaluate_account") as evaluate,
    ):
        call_command("send_bulletin_alerts", "--commit", stdout=out)
    evaluate.assert_not_called()
    assert "Nothing changed since 2026-01-16 18:00: skipped." in out.getvalue()
    tally = CommandRun.objects.latest("id").counts
    assert tally == {
        "held": 0,
        "gated": 1,
        "accounts": 0,
        "messages": 0,
        "pushes": 0,
        "emails": 0,
        "failed": 0,
    }


@pytest.mark.parametrize("n", [1, 5])
def test_skipped_run_query_count_is_flat(
    db: Any, n: int, django_assert_max_num_queries: Any
) -> None:
    """A skipped run costs the same few queries however many follow."""
    with freeze_time(SETUP):
        for i in range(n):
            make_follower(f"Region {i}")
    run_at(EVENING, "--commit")
    with django_assert_max_num_queries(8):
        tally = run_at(LATER, "--commit")
    assert tally["gated"] == 1


def test_changed_bulletin_evaluates_only_its_followers(
    followers: list[Account],
) -> None:
    """A bulletin in one region reaches that region's follower alone."""
    run_at(EVENING, "--commit")
    with freeze_time(LATER - datetime.timedelta(minutes=10)):
        publish(followers[0], "high")
    mail.outbox.clear()
    tally = run_at(LATER, "--commit")
    assert (tally["accounts"], tally["messages"], tally["gated"]) == (1, 1, 0)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [followers[0].user.email]


def test_full_evaluates_everyone(followers: list[Account]) -> None:
    """--full passes the gate by."""
    run_at(EVENING, "--commit")
    tally = run_at(LATER, "--commit", "--full")
    assert (tally["accounts"], tally["messages"], tally["gated"]) == (3, 0, 0)


def test_night_hold_tallies_held(followers: list[Account]) -> None:
    """A held run says so, and is not the gate's watermark."""
    tally = run_at(NIGHT, "--commit")
    assert (tally["held"], tally["gated"], tally["accounts"]) == (1, 0, 0)
    # The held run is no watermark: the next one evaluates everyone.
    assert run_at(NIGHT + datetime.timedelta(hours=8), "--commit")["accounts"] == 3


def test_overnight_bulletin_is_announced_in_the_morning(
    followers: list[Account],
) -> None:
    """An issue landing during the hold goes out at 06:30."""
    run_at(datetime.datetime(2026, 1, 16, 21, 10, tzinfo=ALERT_TZ), "--commit")
    for hour in (23, 0, 1):
        day = 16 if hour == 23 else 17
        run_at(datetime.datetime(2026, 1, day, hour, 10, tzinfo=ALERT_TZ), "--commit")
    with freeze_time(datetime.datetime(2026, 1, 17, 3, 0, tzinfo=ALERT_TZ)):
        publish(followers[1], "considerable")
    for hour in (4, 5):
        run_at(datetime.datetime(2026, 1, 17, hour, 10, tzinfo=ALERT_TZ), "--commit")
    mail.outbox.clear()
    tally = run_at(datetime.datetime(2026, 1, 17, 6, 30, tzinfo=ALERT_TZ), "--commit")
    assert (tally["held"], tally["gated"], tally["messages"]) == (0, 0, 1)
    assert [m.to for m in mail.outbox] == [[followers[1].user.email]]


def test_dry_run_is_gated_but_never_the_watermark(followers: list[Account]) -> None:
    """A dry run uses the gate, and the next committed run ignores it."""
    run_at(EVENING, "--commit")
    assert run_at(LATER)["gated"] == 1
    with freeze_time(LATER + datetime.timedelta(minutes=5)):
        publish(followers[2], "high")
    run_at(LATER + datetime.timedelta(minutes=10))
    # The dry run that saw the change did not move the watermark.
    tally = run_at(LATER + datetime.timedelta(minutes=20), "--commit")
    assert (tally["accounts"], tally["messages"]) == (1, 1)


def test_gated_messages_equal_full_messages(followers: list[Account]) -> None:
    """For one scenario the gate sends exactly what --full would."""
    run_at(EVENING, "--commit")
    with freeze_time(LATER - datetime.timedelta(minutes=10)):
        publish(followers[0], "high")
        newcomer = make_follower("Saas")

    def sent(*args: str) -> set[tuple[int, str, str]]:
        """Return each (account, title, body) a dry run would send."""
        seen: set[tuple[int, str, str]] = set()

        def record(account: Account, now: datetime.datetime) -> Any:
            """Evaluate for real and note the message."""
            run = evaluate_account(account, now)
            if run.message is not None:
                seen.add((account.pk, run.message.title, run.message.body))
            return run

        with patch(f"{COMMAND}.evaluate_account", side_effect=record):
            run_at(LATER, *args)
        return seen

    gated, full = sent(), sent("--full")
    assert gated == full
    assert {pk for pk, _title, _body in gated} == {followers[0].pk, newcomer.pk}
