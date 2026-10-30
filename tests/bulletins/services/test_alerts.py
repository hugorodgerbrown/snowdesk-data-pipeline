"""
tests/bulletins/services/test_alerts.py — Who is told what, and when (SNOW-942).

The decision rules as a pure function, then evaluate_account / deliver
against real rows: one message per account, the send log as the previous
state, push to every device, email only as the account asks.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.accounts.models import Account
from apps.bulletins.models import BulletinAlert
from apps.bulletins.services import alerts
from apps.bulletins.services.alerts import (
    ALERT_TZ,
    account_places,
    decide,
    deliver,
    evaluate_account,
    in_night_hold,
    places_on,
    since_previous_day,
    trip_places,
)
from apps.bulletins.services.bulletin_change import BulletinState, ProblemState
from tests.factories import (
    AccountFactory,
    BulletinAlertFactory,
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    PushSubscriptionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    TripFactory,
    TripParticipantFactory,
)

TODAY = datetime.date(2026, 1, 16)
TOMORROW = TODAY + datetime.timedelta(days=1)
# 18:00 local on TODAY: inside the evening window, outside the night hold.
NOW = datetime.datetime(2026, 1, 16, 18, 0, tzinfo=ALERT_TZ)

WIND = ProblemState("wind_slab", ("N",), lower=2400)

# dispatch_push's result for an accepted push, and for a refused one.
OK = {"ok": True, "status": 201}
GONE = {"ok": False, "status": 410, "error": "Gone"}


def render(key: str = "moderate", problems: list[dict[str, Any]] | None = None) -> dict:
    """Return a minimal render model."""
    return {"danger": {"key": key}, "traits": [{"problems": problems or []}]}


WIND_RM = {
    "problem_type": "wind_slab",
    "aspects": ["N"],
    "elevation": {"lower": 2400, "upper": None},
}


def publish(region: Any, day: datetime.date, rm: dict) -> Any:
    """Make ``rm`` the authoritative bulletin for (region, day)."""
    bulletin = BulletinFactory.create(render_model=rm)
    RegionDayRatingFactory.create(region=region, date=day, source_bulletin=bulletin)
    return bulletin


def state(key: str = "moderate", problems: tuple = ()) -> BulletinState:
    """Return a state."""
    return BulletinState(rating=key, problems=problems)


class TestNightHold:
    """The overnight hold."""

    @pytest.mark.parametrize(
        ("hour", "minute", "held"),
        [(21, 59, False), (22, 0, True), (3, 0, True), (6, 29, True), (6, 30, False)],
    )
    def test_window(self, hour: int, minute: int, held: bool) -> None:
        """22:00–06:30 Zurich time holds."""
        now = datetime.datetime(2026, 1, 16, hour, minute, tzinfo=ALERT_TZ)
        assert in_night_hold(now) is held


class TestDecide:
    """The pure decision."""

    def kwargs(self, **over: Any) -> dict[str, Any]:
        """Return decide() kwargs for an establishing issue."""
        base: dict[str, Any] = {
            "mode": "NEW_BULLETIN",
            "day": TOMORROW,
            "today": TODAY,
            "current": state(),
            "logged": None,
            "has_row": False,
            "previous_day": state(),
            "enabled_before_day": True,
        }
        return base | over

    def test_new_bulletin_sends_a_restatement(self) -> None:
        """Every-new-bulletin sends the establishing issue, changed or not."""
        d = decide(**self.kwargs())
        assert d.sends and d.kind == "new" and d.compared_to == TODAY

    def test_changes_skips_an_unchanged_day(self) -> None:
        """Only-changes logs an unchanged establishing issue as skipped."""
        d = decide(**self.kwargs(mode="CHANGES"))
        assert d.outcome == BulletinAlert.Outcome.SKIPPED

    def test_changes_sends_a_changed_day(self) -> None:
        """Only-changes sends when something moved."""
        d = decide(**self.kwargs(mode="CHANGES", current=state("considerable")))
        assert d.sends

    def test_season_start(self) -> None:
        """No bulletin the day before: bulletins are back."""
        d = decide(**self.kwargs(previous_day=None))
        assert d.sends and d.kind == "season_start"

    def test_today_baseline_when_enabled_mid_day(self) -> None:
        """A day under way when alerts were turned on is recorded silently."""
        d = decide(**self.kwargs(day=TODAY, enabled_before_day=False))
        assert d.outcome == BulletinAlert.Outcome.BASELINE

    def test_today_sends_when_enabled_before(self) -> None:
        """A day that began with alerts on is announced."""
        assert decide(**self.kwargs(day=TODAY)).sends

    def test_update_sends_in_both_modes(self) -> None:
        """A changed update to a logged day sends whatever the mode."""
        for mode in ("NEW_BULLETIN", "CHANGES"):
            d = decide(
                **self.kwargs(
                    mode=mode, has_row=True, logged=state(), current=state("high")
                )
            )
            assert d.sends and d.kind == "update"

    def test_update_that_moves_nothing_is_skipped(self) -> None:
        """A reissue a reader could not tell apart is skipped."""
        d = decide(**self.kwargs(has_row=True, logged=None, current=state("high")))
        assert d.outcome == BulletinAlert.Outcome.SKIPPED


@pytest.mark.django_db
class TestAccountPlaces:
    """account_places groups favourites by region."""

    def test_region_pins_and_resorts(self) -> None:
        """A region pin and a resort in one region are one place, both named."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        resort = ResortFactory.create(name="Verbier", region=region)
        FavouriteFactory.create(user=account.user, resort=resort, region=region)
        # A dropped pin never alerts.
        FavouriteFactory.create(user=account.user, region=MicroRegionFactory.create())
        (place,) = account_places(account.user)
        assert place.region == region
        assert place.label == "Martigny-Verbier, Verbier"

    def test_switched_off_place_is_left_out(self) -> None:
        """A place with alerts off is not a place."""
        account = AccountFactory.create()
        FavouriteFactory.create(
            user=account.user,
            region=MicroRegionFactory.create(),
            region_pin=True,
            alerts=False,
        )
        assert account_places(account.user) == []


@pytest.fixture()
def follower(db: Any) -> tuple[Account, Any]:
    """Return an account following one region, alerts on since last week."""
    account = AccountFactory.create(
        notify_when=Account.NotifyWhen.NEW_BULLETIN,
        alerts_enabled_at=NOW - datetime.timedelta(days=7),
    )
    region = MicroRegionFactory.create(region_id="CH-4115", name="Verbier")
    FavouriteFactory.create(user=account.user, region=region, region_pin=True)
    return account, region


@pytest.mark.django_db
class TestEvaluateAndDeliver:
    """evaluate_account and deliver against real rows."""

    def test_establishing_issue_is_sent_once(self, follower: tuple) -> None:
        """Tomorrow's first issue sends; the same state never sends twice."""
        account, region = follower
        publish(region, TODAY, render())
        publish(region, TOMORROW, render("considerable", [WIND_RM]))
        PushSubscriptionFactory.create(account=account)

        with patch.object(alerts, "dispatch_push", return_value=OK) as push:
            run = deliver(evaluate_account(account, NOW))
        assert run.message is not None
        assert run.message.title == "Verbier · Saturday: Considerable (3)"
        push.assert_called_once()
        # Today fired too (no row, alerts predate it) but one place is named
        # once: today is logged skipped, tomorrow is the message.
        today_row = BulletinAlert.objects.get(account=account, target_date=TODAY)
        assert today_row.outcome == BulletinAlert.Outcome.SKIPPED
        assert BulletinAlert.objects.get(
            account=account, target_date=TOMORROW
        ).sent_push

        again = evaluate_account(account, NOW)
        assert again.message is None and again.rows == []

    def test_update_to_a_logged_day(self, follower: tuple) -> None:
        """A new state for a logged day is an update."""
        account, region = follower
        publish(region, TOMORROW, render("considerable"))
        BulletinAlertFactory.create(
            account=account,
            region=region,
            target_date=TOMORROW,
            fingerprint="old",
            state=state("moderate").to_json(),
        )
        run = evaluate_account(account, NOW)
        (row,) = run.rows
        assert row.decision.kind == "update"
        assert run.message is not None
        assert "updated for Saturday" in run.message.title

    def test_a_state_that_recurs_is_told_once(self, follower: tuple) -> None:
        """A, then B, then A again: the return of A sends once, not every run."""
        account, region = follower
        PushSubscriptionFactory.create(account=account)
        bulletin = publish(region, TOMORROW, render())
        with patch.object(alerts, "dispatch_push", return_value=OK) as push:
            deliver(evaluate_account(account, NOW))  # A
            bulletin.render_model = render("considerable")
            bulletin.save()
            deliver(evaluate_account(account, NOW))  # B
            bulletin.render_model = render()
            bulletin.save()
            deliver(evaluate_account(account, NOW))  # A again
            assert push.call_count == 3
            again = evaluate_account(account, NOW)
        assert again.rows == []
        assert BulletinAlert.objects.filter(target_date=TOMORROW).count() == 2

    def test_email_fallback_without_push(self, follower: tuple) -> None:
        """No push device: the FALLBACK account is emailed."""
        account, region = follower
        publish(region, TOMORROW, render())
        with patch.object(alerts, "send_alert_email") as email:
            run = deliver(evaluate_account(account, NOW))
        email.assert_called_once()
        assert run.emailed
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.sent_email and not row.sent_push
        assert row.outcome == BulletinAlert.Outcome.SENT

    def test_failed_push_falls_back_to_email(self, follower: tuple) -> None:
        """Every push refused (an expired device): FALLBACK emails instead."""
        account, region = follower
        publish(region, TOMORROW, render())
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "dispatch_push", return_value=GONE),
            patch.object(alerts, "send_alert_email") as email,
        ):
            deliver(evaluate_account(account, NOW))
        email.assert_called_once()
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.outcome == BulletinAlert.Outcome.SENT
        assert row.sent_email and not row.sent_push

    def test_unreached_alert_is_retried(self, follower: tuple) -> None:
        """Push refused and email off: FAILED, and the next run sends again."""
        account, region = follower
        account.alert_email = Account.AlertEmail.NEVER
        account.save()
        publish(region, TOMORROW, render())
        PushSubscriptionFactory.create(account=account)
        with patch.object(alerts, "dispatch_push", return_value=GONE):
            deliver(evaluate_account(account, NOW))
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.outcome == BulletinAlert.Outcome.FAILED

        retry = evaluate_account(account, NOW)
        assert retry.message is not None
        with patch.object(alerts, "dispatch_push", return_value=OK):
            deliver(retry)
        row.refresh_from_db()
        assert row.outcome == BulletinAlert.Outcome.SENT and row.sent_push
        assert evaluate_account(account, NOW).rows == []

    def test_failed_enqueue_leaves_no_pending_row(self, follower: tuple) -> None:
        """An enqueue that raises rolls the rows back, so the day stays untold."""
        account, region = follower
        publish(region, TOMORROW, render())
        PushSubscriptionFactory.create(account=account)
        run = evaluate_account(account, NOW)
        worker = MagicMock()
        worker.enqueue.side_effect = RuntimeError
        with (
            patch.object(alerts, "_worker_deliver_alert", worker),
            pytest.raises(RuntimeError),
        ):
            deliver(run)
        assert not BulletinAlert.objects.filter(account=account).exists()
        assert evaluate_account(account, NOW).message is not None

    def test_worker_settles_only_pending_rows(self, follower: tuple) -> None:
        """A replayed delivery task never rewrites a row already settled."""
        account, region = follower
        settled = BulletinAlertFactory.create(
            account=account,
            region=region,
            target_date=TOMORROW,
            outcome=BulletinAlert.Outcome.SENT,
            sent_push=True,
        )
        with patch.object(alerts, "dispatch_push", return_value=GONE):
            alerts._worker_deliver_alert.call(
                account.pk, [settled.pk], {}, "t", "b", "/"
            )
        settled.refresh_from_db()
        assert settled.outcome == BulletinAlert.Outcome.SENT and settled.sent_push

    def test_no_email_when_push_reaches(self, follower: tuple) -> None:
        """FALLBACK with a live device sends push only."""
        account, region = follower
        publish(region, TOMORROW, render())
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "dispatch_push", return_value=OK),
            patch.object(alerts, "send_alert_email") as email,
        ):
            deliver(evaluate_account(account, NOW))
        email.assert_not_called()

    def test_always_emails_as_well(self, follower: tuple) -> None:
        """ALWAYS emails alongside push."""
        account, region = follower
        account.alert_email = Account.AlertEmail.ALWAYS
        account.save()
        publish(region, TOMORROW, render())
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "dispatch_push", return_value=OK),
            patch.object(alerts, "send_alert_email") as email,
        ):
            deliver(evaluate_account(account, NOW))
        email.assert_called_once()

    def test_unreachable_account_logs_skipped(self, follower: tuple) -> None:
        """NEVER email and no device: the state advances, nothing is sent."""
        account, region = follower
        account.alert_email = Account.AlertEmail.NEVER
        account.save()
        publish(region, TOMORROW, render())
        with patch.object(alerts, "send_alert_email") as email:
            deliver(evaluate_account(account, NOW))
        email.assert_not_called()
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.outcome == BulletinAlert.Outcome.SKIPPED

    def test_unverified_account_is_not_emailed(self, follower: tuple) -> None:
        """An unproven address is never emailed."""
        account, region = follower
        account.is_verified = False
        account.save()
        publish(region, TOMORROW, render())
        with patch.object(alerts, "send_alert_email") as email:
            deliver(evaluate_account(account, NOW))
        email.assert_not_called()

    def test_bundles_every_place(self, follower: tuple) -> None:
        """Two places that fire make one message."""
        account, region = follower
        other = MicroRegionFactory.create(name="Davos")
        FavouriteFactory.create(user=account.user, region=other, region_pin=True)
        publish(region, TOMORROW, render())
        publish(other, TOMORROW, render("considerable"))
        run = evaluate_account(account, NOW)
        assert run.message is not None
        assert run.message.title == "Tomorrow for your 2 places"
        assert run.message.url == "/alerts/2026-01-17/"

    def test_bulletin_without_render_model_is_ignored(self, follower: tuple) -> None:
        """A row with no render model is not compared."""
        account, region = follower
        publish(region, TOMORROW, {})
        assert evaluate_account(account, NOW).rows == []


@pytest.mark.django_db
class TestTripDay:
    """A saved trip alerts for its start region on its own day (SNOW-947)."""

    @pytest.fixture()
    def traveller(self, db: Any) -> tuple[Account, Any]:
        """Return an account with alerts on and no pins, and a region."""
        account = AccountFactory.create(
            notify_when=Account.NotifyWhen.CHANGES,
            alerts_enabled_at=NOW - datetime.timedelta(days=7),
        )
        return account, MicroRegionFactory.create(region_id="CH-4115", name="Verbier")

    def test_trip_tomorrow_sends_in_changes_mode(self, traveller: tuple) -> None:
        """The evening before, the trip's day sends even when nothing moved."""
        account, region = traveller
        trip = TripFactory.create(
            created_by=account.user, date=TOMORROW, name="Mont Fort loop"
        )
        BulletinAlertFactory.create(
            account=account,
            region=region,
            target_date=TODAY,
            fingerprint=state("considerable").fingerprint(),
            state=state("considerable").to_json(),
        )
        publish(region, TODAY, render("considerable"))
        publish(region, TOMORROW, render("considerable"))
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "start_region", return_value=region),
            patch.object(alerts, "dispatch_push", return_value=OK),
        ):
            run = deliver(evaluate_account(account, NOW))
        assert run.message is not None
        assert run.message.title.startswith("Mont Fort loop")
        assert run.message.url == f"/trips/{trip.uuid}/"
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.outcome == BulletinAlert.Outcome.SENT

    def test_pinned_place_in_changes_mode_stays_quiet(self, traveller: tuple) -> None:
        """Without the trip the same quiet day sends nothing."""
        account, region = traveller
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        # Today is already logged, so only tomorrow is in question.
        BulletinAlertFactory.create(
            account=account,
            region=region,
            target_date=TODAY,
            fingerprint=state("considerable").fingerprint(),
            state=state("considerable").to_json(),
        )
        publish(region, TODAY, render("considerable"))
        publish(region, TOMORROW, render("considerable"))
        assert evaluate_account(account, NOW).message is None

    def test_trip_on_another_day_is_not_a_place(self, traveller: tuple) -> None:
        """A trip dated next week adds nothing today or tomorrow."""
        account, region = traveller
        TripFactory.create(
            created_by=account.user, date=TODAY + datetime.timedelta(days=7)
        )
        publish(region, TOMORROW, render())
        with patch.object(alerts, "start_region", return_value=region):
            assert evaluate_account(account, NOW).rows == []

    def test_trip_and_pin_in_one_region_are_one_place(self, traveller: tuple) -> None:
        """A pin and a trip in one region make one row, both named."""
        account, region = traveller
        FavouriteFactory.create(
            user=account.user, region=region, region_pin=True, name="Verbier"
        )
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Mont Fort")
        with patch.object(alerts, "start_region", return_value=region):
            (place,) = places_on(
                account_places(account.user), trip_places(account.user, TOMORROW)
            )
        assert place.label == "Verbier, Mont Fort"
        assert place.trip_url

    def test_joined_trip_alerts_the_joiner(self, traveller: tuple) -> None:
        """Anyone who saved the trip is told, not only its organiser."""
        account, region = traveller
        trip = TripFactory.create(date=TOMORROW)
        TripParticipantFactory.create(trip=trip, user=account.user)
        with patch.object(alerts, "start_region", return_value=region):
            (place,) = trip_places(account.user, TOMORROW)
        assert place.region == region

    def test_start_outside_every_region_adds_nothing(self, traveller: tuple) -> None:
        """No region for the start is no place, and no error."""
        account, _region = traveller
        TripFactory.create(created_by=account.user, date=TOMORROW)
        with patch.object(alerts, "start_region", return_value=None):
            assert trip_places(account.user, TOMORROW) == []

    def test_someone_elses_trip_is_not_a_place(self, traveller: tuple) -> None:
        """A trip the account never saved is not its place."""
        account, region = traveller
        TripFactory.create(date=TOMORROW)
        with patch.object(alerts, "start_region", return_value=region):
            assert trip_places(account.user, TOMORROW) == []


@pytest.mark.django_db
def test_since_previous_day() -> None:
    """The change from the day before, or None without both days."""
    region = MicroRegionFactory.create()
    assert since_previous_day(region, TOMORROW) is None
    publish(region, TODAY, render())
    publish(region, TOMORROW, render("considerable"))
    result = since_previous_day(region, TOMORROW)
    assert result is not None
    change, previous = result
    assert change.rating_direction == 1
    assert previous == TODAY
