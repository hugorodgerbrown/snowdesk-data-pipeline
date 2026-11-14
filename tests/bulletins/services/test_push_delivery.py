"""
tests/bulletins/services/test_push_delivery.py — A push's urgency, TTL and topic.

SNOW-1495: one test per row of the matrix in docs/push-notifications.md,
then how a bundle combines its places, the TTL's bounds, ``expired`` and the
topic's shape.
"""

from __future__ import annotations

import datetime
import re

from apps.bulletins.services.alert_wording import AlertKind, PlaceAlert
from apps.bulletins.services.bulletin_change import (
    BulletinState,
    ProblemState,
    summarise_change,
)
from apps.bulletins.services.push_delivery import (
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    Delivery,
    delivery_for,
    highest_urgency,
)

TODAY = datetime.date(2026, 1, 16)
TOMORROW = TODAY + datetime.timedelta(days=1)
NOW = datetime.datetime(2026, 1, 16, 7, 0, tzinfo=datetime.UTC)
ENDS = datetime.datetime(2026, 1, 16, 16, 0, tzinfo=datetime.UTC)
WIND = ProblemState("wind_slab", ("N",), lower=2400)


def place(
    before: BulletinState | None,
    after: BulletinState,
    *,
    day: datetime.date = TODAY,
    kind: AlertKind = "update",
    valid_to: datetime.datetime | None = ENDS,
    on_trip: bool = False,
) -> PlaceAlert:
    """Return one place whose change runs from ``before`` to ``after``."""
    return PlaceAlert(
        region_id="CH-4115",
        label="Verbier",
        target_date=day,
        kind=kind,
        change=summarise_change(before, after),
        valid_to=valid_to,
        on_trip=on_trip,
    )


def level(
    rating: str, subdivision: str | None = None, *problems: ProblemState
) -> BulletinState:
    """Return a state at ``rating`` with ``subdivision`` and ``problems``."""
    return BulletinState(rating=rating, subdivision=subdivision, problems=problems)


MODERATE = level("moderate")
CONSIDERABLE = level("considerable")


class TestMatrix:
    """One test per row of the signed-off matrix."""

    def test_today_level_up_is_high(self) -> None:
        """Today's danger rising wakes the phone."""
        d = delivery_for([place(MODERATE, CONSIDERABLE)], TODAY, NOW)
        assert d.urgency == "high"

    def test_today_problem_added_is_high(self) -> None:
        """A problem added today counts as danger up."""
        d = delivery_for([place(MODERATE, level("moderate", None, WIND))], TODAY, NOW)
        assert d.urgency == "high"

    def test_today_sub_rating_up_is_high(self) -> None:
        """3− to 3 within the level counts as danger up."""
        before = level("considerable", "-")
        d = delivery_for([place(before, CONSIDERABLE)], TODAY, NOW)
        assert d.urgency == "high"

    def test_trip_day_is_high(self) -> None:
        """Any alert for a saved trip's day wakes the phone, even unchanged."""
        d = delivery_for(
            [place(MODERATE, MODERATE, day=TOMORROW, on_trip=True)], TODAY, NOW
        )
        assert d.urgency == "high"

    def test_tomorrow_is_normal(self) -> None:
        """Tomorrow's bulletin is normal, even with the danger up."""
        d = delivery_for([place(MODERATE, CONSIDERABLE, day=TOMORROW)], TODAY, NOW)
        assert d.urgency == "normal"

    def test_today_eased_is_normal(self) -> None:
        """Today's danger held or lower is normal."""
        assert delivery_for([place(CONSIDERABLE, MODERATE)], TODAY, NOW).urgency == (
            "normal"
        )
        down = place(CONSIDERABLE, level("considerable", "-"))
        assert delivery_for([down], TODAY, NOW).urgency == "normal"

    def test_season_start_is_low(self) -> None:
        """Bulletins starting or resuming go low, under the ``season`` topic."""
        d = delivery_for(
            [place(None, MODERATE, day=TOMORROW, kind="season_start")], TODAY, NOW
        )
        assert d.urgency == "low"
        assert d.topic == "season"


class TestBundle:
    """A message of several places."""

    def test_takes_the_highest_urgency(self) -> None:
        """One high place makes the bundle high."""
        places = [
            place(None, MODERATE, day=TOMORROW, kind="season_start"),
            place(MODERATE, MODERATE, day=TOMORROW),
            place(MODERATE, CONSIDERABLE),
        ]
        assert delivery_for(places, TODAY, NOW).urgency == "high"

    def test_takes_the_latest_valid_to(self) -> None:
        """The message is held until the last of its bulletins ends."""
        later = ENDS + datetime.timedelta(hours=3)
        places = [
            place(MODERATE, MODERATE, valid_to=later),
            place(MODERATE, MODERATE, valid_to=ENDS),
        ]
        assert delivery_for(places, TODAY, NOW).expires_at == later

    def test_topic_is_the_latest_day(self) -> None:
        """Differing days give the latest's topic; season only when all are."""
        places = [
            place(MODERATE, MODERATE, day=TOMORROW),
            place(None, MODERATE, kind="season_start"),
        ]
        assert delivery_for(places, TODAY, NOW).topic == "alert-20260117"

    def test_no_valid_to_falls_back_to_twelve_hours(self) -> None:
        """A place saying nothing of its end is held the old twelve hours."""
        d = delivery_for([place(MODERATE, MODERATE, valid_to=None)], TODAY, NOW)
        assert d.expires_at == NOW + datetime.timedelta(hours=12)


class TestDelivery:
    """The TTL's bounds, ``expired`` and the task primitives."""

    def test_ttl_runs_to_the_expiry(self) -> None:
        """Within the bounds, the TTL is the time left."""
        d = Delivery("normal", NOW + datetime.timedelta(hours=5))
        assert d.ttl(NOW) == 5 * 60 * 60

    def test_ttl_floor_is_an_hour(self) -> None:
        """A bulletin about to end is still held an hour."""
        d = Delivery("normal", NOW + datetime.timedelta(minutes=10))
        assert d.ttl(NOW) == MIN_TTL_SECONDS == 3600

    def test_ttl_ceiling_is_28_hours(self) -> None:
        """No message is held over 28 hours."""
        d = Delivery("normal", NOW + datetime.timedelta(days=3))
        assert d.ttl(NOW) == MAX_TTL_SECONDS == 28 * 3600

    def test_expired_at_and_past_the_expiry(self) -> None:
        """``expired`` is unclamped while ``ttl`` still reads an hour."""
        d = Delivery("normal", NOW)
        assert not d.expired(NOW - datetime.timedelta(seconds=1))
        assert d.expired(NOW)
        assert d.expired(NOW + datetime.timedelta(minutes=5))
        assert d.ttl(NOW + datetime.timedelta(minutes=5)) == MIN_TTL_SECONDS

    def test_fixed_ttl_is_not_clamped(self) -> None:
        """The test button's minute escapes the hour floor."""
        d = Delivery("high", NOW, topic="test", fixed_ttl=60)
        assert d.ttl(NOW) == 60

    def test_task_round_trip(self) -> None:
        """The primitives rebuild the same delivery."""
        d = Delivery("high", ENDS, topic="alert-20260116")
        assert Delivery.from_task(**d.to_task(), now=NOW) == d

    def test_task_without_primitives(self) -> None:
        """A task queued before SNOW-1495 goes normal, twelve hours, no topic."""
        d = Delivery.from_task("", "", "", NOW)
        assert d == Delivery("normal", NOW + datetime.timedelta(hours=12), "")

    def test_topic_is_url_safe_and_short(self) -> None:
        """RFC 8030 §5.4: at most 32 characters of the URL-safe alphabet."""
        for topic in (
            delivery_for([place(MODERATE, MODERATE)], TODAY, NOW).topic,
            delivery_for(
                [place(None, MODERATE, kind="season_start")], TODAY, NOW
            ).topic,
        ):
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", topic)
        assert delivery_for([place(MODERATE, MODERATE)], TODAY, NOW).topic == (
            "alert-20260116"
        )

    def test_highest_urgency_of_none_is_normal(self) -> None:
        """No urgencies, or an unknown one, count as normal."""
        assert highest_urgency([]) == "normal"
        assert highest_urgency(["", "low"]) == "normal"
