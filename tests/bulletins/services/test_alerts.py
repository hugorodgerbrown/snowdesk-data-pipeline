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
    forecast_new_snow,
    had_bulletin_in_season,
    in_night_hold,
    places_on,
    recent_alerts,
    since_previous_day,
    trip_places,
)
from apps.bulletins.services.bulletin_change import (
    BulletinState,
    ProblemState,
    bulletin_state,
)
from apps.ingest.public import build_derived
from tests.factories import (
    AccountFactory,
    BulletinAlertFactory,
    BulletinFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    PushSubscriptionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    TripFactory,
    TripParticipantFactory,
    WeatherFactory,
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

    def test_resumption_inside_the_season(self) -> None:
        """No bulletin yesterday but one earlier this season: a new bulletin."""
        d = decide(**self.kwargs(previous_day=None, rated_in_season=True))
        assert d.sends and d.kind == "new" and d.change.previous is None

    def test_changes_skips_a_shifted_problem(self) -> None:
        """A problem moving its aspects is not a change worth telling."""
        north = ProblemState("wind_slab", ("N",))
        south = ProblemState("wind_slab", ("S",))
        d = decide(
            **self.kwargs(
                mode="CHANGES",
                previous_day=state(problems=(north,)),
                current=state(problems=(south,)),
            )
        )
        assert d.outcome == BulletinAlert.Outcome.SKIPPED

    def test_update_with_a_shifted_problem_is_skipped(self) -> None:
        """An update that only shifts a problem's band is logged, not told."""
        low = ProblemState("wind_slab", ("N",), lower=2000)
        high = ProblemState("wind_slab", ("N",), lower=2400)
        d = decide(
            **self.kwargs(
                has_row=True,
                logged=state(problems=(low,)),
                current=state(problems=(high,)),
            )
        )
        assert d.kind == "update" and d.outcome == BulletinAlert.Outcome.SKIPPED

    def test_update_with_an_added_problem_sends(self) -> None:
        """An update that adds a problem is told."""
        wind = ProblemState("wind_slab", ("N",))
        d = decide(
            **self.kwargs(has_row=True, logged=state(), current=state(problems=(wind,)))
        )
        assert d.sends

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
        assert run.message.title == "Verbier – Saturday bulletin"
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
        assert run.message.title == "Verbier – Saturday bulletin update"

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

    def test_worker_stamps_one_send_time_on_every_row(self, follower: tuple) -> None:
        """A bundle's rows share one updated_at, so the bell folds them."""
        account, region = follower
        rows = [
            BulletinAlertFactory.create(
                account=account,
                region=region,
                target_date=TOMORROW,
                outcome=BulletinAlert.Outcome.PENDING,
            )
            for _ in range(3)
        ]
        PushSubscriptionFactory.create(account=account)
        with patch.object(alerts, "dispatch_push", return_value=OK):
            alerts._worker_deliver_alert.call(
                account.pk, [r.pk for r in rows], {}, "t", "b", "/"
            )
        stamps = set(
            BulletinAlert.objects.filter(pk__in=[r.pk for r in rows]).values_list(
                "updated_at", flat=True
            )
        )
        assert len(stamps) == 1

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
        assert run.message.title == "Bulletins for your 2 places"
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
            patch.object(alerts, "track_regions", return_value=[region]),
            patch.object(alerts, "dispatch_push", return_value=OK),
        ):
            run = deliver(evaluate_account(account, NOW))
        assert run.message is not None
        assert run.message.title.startswith("Mont Fort loop")
        assert run.message.url == f"/trips/{trip.uuid}/"
        row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert row.outcome == BulletinAlert.Outcome.SENT

    def test_trip_saved_after_a_quiet_skip_still_sends(self, traveller: tuple) -> None:
        """A pin's SKIPPED quiet day does not swallow a trip saved after it."""
        account, region = traveller
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        for day in (TODAY, TOMORROW):
            BulletinAlertFactory.create(
                account=account,
                region=region,
                target_date=day,
                fingerprint=state("considerable").fingerprint(),
                state=state("considerable").to_json(),
                outcome=(
                    BulletinAlert.Outcome.SENT
                    if day == TODAY
                    else BulletinAlert.Outcome.SKIPPED
                ),
            )
        publish(region, TODAY, render("considerable"))
        publish(region, TOMORROW, render("considerable"))
        assert evaluate_account(account, NOW).message is None

        TripFactory.create(created_by=account.user, date=TOMORROW, name="Mont Fort")
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "track_regions", return_value=[region]),
            patch.object(alerts, "dispatch_push", return_value=OK),
        ):
            run = deliver(evaluate_account(account, NOW))
            assert run.message is not None
            row = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
            assert row.outcome == BulletinAlert.Outcome.SENT
            # Told now: the next run is quiet.
            assert evaluate_account(account, NOW).message is None

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
        with patch.object(alerts, "track_regions", return_value=[region]):
            assert evaluate_account(account, NOW).rows == []

    def test_trip_and_pin_in_one_region_are_one_place(self, traveller: tuple) -> None:
        """A pin and a trip in one region make one row, both named."""
        account, region = traveller
        FavouriteFactory.create(
            user=account.user, region=region, region_pin=True, name="Verbier"
        )
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Mont Fort")
        with patch.object(alerts, "track_regions", return_value=[region]):
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
        with patch.object(alerts, "track_regions", return_value=[region]):
            (place,) = trip_places(account.user, TOMORROW)
        assert place.region == region

    def test_track_outside_every_region_adds_nothing(self, traveller: tuple) -> None:
        """A track in no region is no place, and no error."""
        account, _region = traveller
        TripFactory.create(created_by=account.user, date=TOMORROW)
        with patch.object(alerts, "track_regions", return_value=[]):
            assert trip_places(account.user, TOMORROW) == []

    def test_someone_elses_trip_is_not_a_place(self, traveller: tuple) -> None:
        """A trip the account never saved is not its place."""
        account, region = traveller
        TripFactory.create(date=TOMORROW)
        with patch.object(alerts, "track_regions", return_value=[region]):
            assert trip_places(account.user, TOMORROW) == []


class TestTripAcrossRegions:
    """A trip crossing several regions is one place covering all (SNOW-945)."""

    @pytest.fixture()
    def crossing(self, db: Any) -> tuple[Account, Any, Any]:
        """Return an account with alerts on, and two neighbouring regions."""
        account = AccountFactory.create(
            notify_when=Account.NotifyWhen.CHANGES,
            alerts_enabled_at=NOW - datetime.timedelta(days=7),
        )
        first = MicroRegionFactory.create(region_id="CH-4115", name="Verbier")
        second = MicroRegionFactory.create(region_id="CH-4116", name="Arolla")
        return account, first, second

    def _logged(self, account: Account, region: Any, day: datetime.date) -> None:
        """Log a considerable state for (region, day) as already told."""
        BulletinAlertFactory.create(
            account=account,
            region=region,
            target_date=day,
            fingerprint=state("considerable").fingerprint(),
            state=state("considerable").to_json(),
        )

    def test_two_region_trip_is_one_place(self, crossing: tuple) -> None:
        """Both regions, one place, the trip's name and link."""
        account, first, second = crossing
        trip = TripFactory.create(
            created_by=account.user, date=TOMORROW, name="Haute Route"
        )
        with patch.object(alerts, "track_regions", return_value=[first, second]):
            (place,) = trip_places(account.user, TOMORROW)
        assert place.covered == [first, second]
        assert place.label == "Haute Route"
        assert place.trip_url == f"/trips/{trip.uuid}/"

    def test_one_alert_names_both_bulletins(self, crossing: tuple) -> None:
        """One message for the trip, one log row per region."""
        account, first, second = crossing
        trip = TripFactory.create(
            created_by=account.user, date=TOMORROW, name="Haute Route"
        )
        for region in (first, second):
            publish(region, TOMORROW, render("considerable"))
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "track_regions", return_value=[first, second]),
            patch.object(alerts, "dispatch_push", return_value=OK),
        ):
            run = deliver(evaluate_account(account, NOW))
        assert run.message is not None
        assert run.message.title.startswith("Haute Route")
        assert run.message.body == "For your trip. 2 bulletins."
        assert run.message.url == f"/trips/{trip.uuid}/"
        logged = BulletinAlert.objects.filter(account=account, target_date=TOMORROW)
        assert {row.region_id for row in logged} == {first.pk, second.pk}

    def test_a_change_in_either_region_fires(self, crossing: tuple) -> None:
        """Only the second region moved: the trip is still told."""
        account, first, second = crossing
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Haute Route")
        for region in (first, second):
            self._logged(account, region, TODAY)
            publish(region, TODAY, render("considerable"))
            self._logged(account, region, TOMORROW)
        publish(first, TOMORROW, render("considerable"))
        publish(second, TOMORROW, render("high"))
        with patch.object(alerts, "track_regions", return_value=[first, second]):
            run = evaluate_account(account, NOW)
        assert run.message is not None
        assert run.message.body == "For your trip. 2 bulletins."

    def test_a_region_yet_to_publish_is_not_counted(self, crossing: tuple) -> None:
        """Only one region has published: the trip names one bulletin."""
        account, first, _second = crossing
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Haute Route")
        publish(first, TOMORROW, render("considerable"))
        with patch.object(alerts, "track_regions", return_value=[first, _second]):
            run = evaluate_account(account, NOW)
        assert run.message is not None
        assert run.message.title == "Haute Route – Saturday bulletin"
        assert run.message.body == "For your trip. 1 bulletin."
        assert "Considerable" not in run.message.body

    def test_one_region_trip_folds_into_a_pin(self, crossing: tuple) -> None:
        """A trip inside a pinned region is that pin's place, as before."""
        account, first, _second = crossing
        FavouriteFactory.create(
            user=account.user, region=first, region_pin=True, name="Verbier"
        )
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Mont Fort")
        with patch.object(alerts, "track_regions", return_value=[first]):
            (place,) = places_on(
                account_places(account.user), trip_places(account.user, TOMORROW)
            )
        assert place.label == "Verbier, Mont Fort"

    def test_multi_region_trip_beside_a_pin_stays_apart(self, crossing: tuple) -> None:
        """A pin in the trip's first region is still its own place."""
        account, first, second = crossing
        FavouriteFactory.create(
            user=account.user, region=first, region_pin=True, name="Verbier"
        )
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Haute Route")
        with patch.object(alerts, "track_regions", return_value=[first, second]):
            places = places_on(
                account_places(account.user), trip_places(account.user, TOMORROW)
            )
        assert [p.label for p in places] == ["Haute Route", "Verbier"]
        assert places[0].covered == [first, second]
        assert places[1].covered == [first]

    def test_a_quiet_pin_does_not_overwrite_the_trips_send(
        self, crossing: tuple
    ) -> None:
        """A pin and a trip sharing a region write one row, the trip's."""
        account, first, second = crossing
        FavouriteFactory.create(
            user=account.user, region=first, region_pin=True, name="Verbier"
        )
        TripFactory.create(created_by=account.user, date=TOMORROW, name="Haute Route")
        for region in (first, second):
            self._logged(account, region, TODAY)
            publish(region, TODAY, render("considerable"))
            publish(region, TOMORROW, render("considerable"))
        PushSubscriptionFactory.create(account=account)
        with (
            patch.object(alerts, "track_regions", return_value=[first, second]),
            patch.object(alerts, "dispatch_push", return_value=OK),
        ):
            run = deliver(evaluate_account(account, NOW))
            assert run.message is not None
            (row,) = BulletinAlert.objects.filter(
                account=account, region=first, target_date=TOMORROW
            )
            assert row.outcome == BulletinAlert.Outcome.SENT
            assert row.title.startswith("Haute Route")
            # Told now: the next run is quiet.
            assert evaluate_account(account, NOW).message is None

    def test_stored_regions_are_read_without_resolving(self, crossing: tuple) -> None:
        """A trip carrying its regions is placed from them, in route order."""
        account, first, second = crossing
        TripFactory.create(
            created_by=account.user,
            date=TOMORROW,
            name="Haute Route",
            crossed_regions=[second.region_id, first.region_id],
        )
        with patch(
            "apps.regions.services.track_regions.resolve_track_regions"
        ) as resolve:
            (place,) = trip_places(account.user, TOMORROW)
        resolve.assert_not_called()
        assert place.covered == [second, first]


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


class TestRecentAlerts:
    """SNOW-1333: what the map's bell lists."""

    def _row(self, account: Account, **kwargs: Any) -> BulletinAlert:
        """Write one sent row with a message, then backdate it if asked."""
        when = kwargs.pop("when", None)
        row = BulletinAlertFactory.create(
            account=account,
            title=kwargs.pop("title", "Verbier – Saturday bulletin"),
            body=kwargs.pop("body", "Considerable, no change."),
            url=kwargs.pop("url", "/alerts/2026-10-31/"),
            **kwargs,
        )
        if when is not None:
            BulletinAlert.objects.filter(pk=row.pk).update(updated_at=when)
        return row

    def test_newest_first_within_the_week(self, db: Any) -> None:
        """A week back, newest first; older rows drop off."""
        account = AccountFactory.create()
        now = datetime.datetime(2026, 10, 30, 18, tzinfo=datetime.UTC)
        self._row(account, title="Old", when=now - datetime.timedelta(days=8))
        self._row(account, title="Thursday", when=now - datetime.timedelta(days=1))
        self._row(account, title="Friday", when=now - datetime.timedelta(hours=1))
        assert [a.title for a in recent_alerts(account, now)] == ["Friday", "Thursday"]

    def test_a_bundle_is_one_message(self, db: Any) -> None:
        """A bundle's one row per place collapses into its one message."""
        account = AccountFactory.create()
        now = datetime.datetime(2026, 10, 30, 18, tzinfo=datetime.UTC)
        for _ in range(3):
            self._row(account, title="Bulletins for your 3 places", when=now)
        assert len(recent_alerts(account, now)) == 1

    def test_two_sends_with_the_same_words_stay_two(self, db: Any) -> None:
        """A second update in one validity window is its own entry."""
        account = AccountFactory.create()
        now = datetime.datetime(2026, 10, 30, 18, tzinfo=datetime.UTC)
        self._row(account, when=now - datetime.timedelta(hours=3))
        self._row(account, when=now)
        assert len(recent_alerts(account, now)) == 2

    def test_only_sent_messages_of_this_account(self, db: Any) -> None:
        """Skipped rows, wordless rows and other accounts' rows are left out."""
        account = AccountFactory.create()
        now = datetime.datetime(2026, 10, 30, 18, tzinfo=datetime.UTC)
        self._row(account, outcome=BulletinAlert.Outcome.SKIPPED, when=now)
        self._row(account, title="", when=now)
        self._row(AccountFactory.create(), when=now)
        assert recent_alerts(account, now) == []


@pytest.mark.django_db
class TestStoredState:
    """SNOW-1341: the run reads the state the service stored with the model."""

    def test_the_stored_fingerprint_is_the_send_logs(self, follower: tuple) -> None:
        """The row records derived.state's fingerprint, not a recomputed one."""
        account, region = follower
        rm = render("considerable")
        stored = state("considerable")
        rm["derived"] = {
            **build_derived(rm),
            "state": {**stored.to_json(), "fingerprint": "from-service"},
        }
        publish(region, TOMORROW, rm)
        run = evaluate_account(account, NOW)
        (row,) = [r for r in run.rows if r.day == TOMORROW]
        assert row.fingerprint == "from-service"
        assert row.state == stored
        with patch.object(alerts, "send_alert_email"):
            deliver(run)
        logged = BulletinAlert.objects.get(account=account, target_date=TOMORROW)
        assert logged.fingerprint == "from-service"
        # The next run compares on the same stored value: nothing new.
        assert [
            r for r in evaluate_account(account, NOW).rows if r.day == TOMORROW
        ] == []

    def test_day_state_reads_the_stored_state(self, follower: tuple) -> None:
        """The "since" strip's previous side comes from derived.state too."""
        _, region = follower
        rm = render("moderate")
        stored = state("high", (WIND,))
        rm["derived"] = {
            **build_derived(rm),
            "state": {**stored.to_json(), "fingerprint": "x"},
        }
        publish(region, TODAY, rm)
        assert alerts.day_state(region, TODAY) == stored

    def test_a_model_without_one_is_reduced_as_before(self, follower: tuple) -> None:
        """A pre-v10 model: the state is computed from it."""
        _, region = follower
        publish(region, TODAY, render("considerable", [WIND_RM]))
        assert alerts.day_state(region, TODAY) == bulletin_state(
            render("considerable", [WIND_RM])
        )


@pytest.mark.django_db
class TestForecastNewSnow:
    """The new snow line reads today's row for the region's centroid (SNOW-944)."""

    def centroid_region(self) -> Any:
        """Return a region with a centroid location."""
        location = LocationFactory.create()
        return MicroRegionFactory.create(centroid_location=location)

    def test_tomorrow_reads_the_forecast_entry(self) -> None:
        """A later day reads its entry in today's forecast[]."""
        region = self.centroid_region()
        WeatherFactory.create(
            location=region.centroid_location,
            observed_on=TODAY,
            snowfall_sum=1.0,
            forecast=[{"date": TOMORROW.isoformat(), "snowfall_sum": 22.4}],
        )
        assert forecast_new_snow(region, TOMORROW, TODAY) == 22.4

    def test_today_reads_the_row(self) -> None:
        """Today reads the row's own snowfall."""
        region = self.centroid_region()
        WeatherFactory.create(
            location=region.centroid_location, observed_on=TODAY, snowfall_sum=8.0
        )
        assert forecast_new_snow(region, TODAY, TODAY) == 8.0

    def test_nothing_known(self) -> None:
        """No centroid, no row, or no entry: None."""
        assert forecast_new_snow(MicroRegionFactory.create(), TODAY, TODAY) is None
        region = self.centroid_region()
        assert forecast_new_snow(region, TODAY, TODAY) is None
        WeatherFactory.create(
            location=region.centroid_location,
            observed_on=TODAY,
            forecast=[{"date": TOMORROW.isoformat(), "snowfall_sum": None}],
        )
        assert forecast_new_snow(region, TOMORROW, TODAY) is None
        later = TODAY + datetime.timedelta(days=3)
        assert forecast_new_snow(region, later, TODAY) is None

    def test_one_place_alert_names_it(self, follower: tuple) -> None:
        """A one-place alert carries the snow; the body names it."""
        account, region = follower
        region.centroid_location = LocationFactory.create()
        region.save(update_fields=["centroid_location"])
        WeatherFactory.create(
            location=region.centroid_location,
            observed_on=TODAY,
            forecast=[{"date": TOMORROW.isoformat(), "snowfall_sum": 20.0}],
        )
        publish(region, TODAY, render())
        publish(region, TOMORROW, render("considerable", [WIND_RM]))
        run = evaluate_account(account, NOW)
        assert run.message is not None
        assert run.message.body.endswith("20 cm new snow forecast.")


@pytest.mark.django_db
class TestSeasonGap:
    """A gap inside the season is a resumption, not a new season (SNOW-1356)."""

    def test_recent_bulletin_is_in_season(self) -> None:
        """A bulletin ten days back keeps the season going."""
        region = MicroRegionFactory.create()
        publish(region, TODAY - datetime.timedelta(days=10), render())
        assert had_bulletin_in_season(region, TODAY)

    def test_old_bulletin_is_last_season(self) -> None:
        """Nothing in the last sixty days: a new season."""
        region = MicroRegionFactory.create()
        publish(region, TODAY - datetime.timedelta(days=61), render())
        assert not had_bulletin_in_season(region, TODAY)

    def test_resumed_alert_says_bulletins_are_back(self, follower: tuple) -> None:
        """After a pause the alert says bulletins are back, not a season start."""
        account, region = follower
        publish(region, TODAY - datetime.timedelta(days=10), render())
        publish(region, TOMORROW, render("low"))
        run = evaluate_account(account, NOW)
        assert run.message is not None
        assert run.message.body == "Bulletins are back. Low."
