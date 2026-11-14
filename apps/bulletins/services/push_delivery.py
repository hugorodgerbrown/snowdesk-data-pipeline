"""
apps/bulletins/services/push_delivery.py — How urgently, and for how long, a push goes.

An alert's words say what it is about; this module turns the same places
into the three RFC 8030 headers a push service acts on (SNOW-1495):

- **Urgency** (§5.3). ``high`` wakes a sleeping phone at once. An alert is
  ``high`` when any of its places is for today and the danger went up (the
  headline level rose, the sub-rating rose within it, or a problem was
  added), or when any is for a day with a saved trip. It is ``low`` when
  every place is a season start, and ``normal`` otherwise: tomorrow's
  bulletin, or today's with the danger held or lower. A bundle takes the
  most urgent of its places.
- **TTL** (§5.2). How long the push service holds the message for a phone
  it cannot reach: until the bulletin stops being valid (the latest
  ``valid_to`` of the message's bulletins), never under an hour and never
  over 28 hours.
- **Topic** (§5.4). A newer message with the same topic replaces one still
  waiting to be collected: ``alert-<YYYYMMDD>`` of the latest day the
  message is for, or ``season`` for a season start.

Pure: no I/O and no Django models, so ``alerts.deliver``, the resend
command and the tests build a :class:`Delivery` the same way. Only
primitives cross the task boundary (:meth:`Delivery.to_task` and
:meth:`Delivery.from_task`).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.bulletins.services.alert_wording import PlaceAlert

logger = logging.getLogger(__name__)

# RFC 8030 §5.3 urgencies an alert uses, least urgent first; a bundle takes
# the highest of its places'.
URGENCY_ORDER: tuple[str, ...] = ("low", "normal", "high")

# The bounds on a push's TTL: at least an hour, so a bulletin about to end
# still reaches a phone that wakes soon; at most 28 hours, the longest a
# day's bulletin plus its evening issue can matter.
MIN_TTL_SECONDS = 60 * 60
MAX_TTL_SECONDS = 28 * 60 * 60

# How long a message is held when none of its places says when its
# bulletin ends: the twelve hours every alert was held before SNOW-1495.
FALLBACK_HOLD = datetime.timedelta(hours=12)

SEASON_TOPIC = "season"


@dataclass(frozen=True)
class Delivery:
    """The urgency, expiry and topic of one push message."""

    urgency: str
    expires_at: datetime.datetime
    topic: str = ""
    # A TTL that is not clamped, for a push only worth seeing at once (the
    # test button's minute). None for an alert, whose TTL runs to
    # ``expires_at`` within the bounds.
    fixed_ttl: int | None = None

    def ttl(self, now: datetime.datetime) -> int:
        """
        Return the seconds the push service should hold the message.

        Args:
            now: The time of sending.

        Returns:
            ``fixed_ttl`` when set; otherwise the time left to
            ``expires_at``, clamped to an hour at least and 28 hours at most.

        """
        if self.fixed_ttl is not None:
            return self.fixed_ttl
        left = int((self.expires_at - now).total_seconds())
        return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, left))

    def expired(self, now: datetime.datetime) -> bool:
        """
        Return True once the message's bulletins have ended.

        Unclamped, unlike :meth:`ttl`: a message whose bulletin ended a
        minute ago still has an hour's TTL, and this tells the two apart.

        Args:
            now: The time to test.

        Returns:
            True at and after ``expires_at``.

        """
        return self.expires_at <= now

    def to_task(self) -> dict[str, str]:
        """Return the delivery as the primitives a task argument can carry."""
        return {
            "urgency": self.urgency,
            "expires_at": self.expires_at.isoformat(),
            "topic": self.topic,
        }

    @classmethod
    def from_task(
        cls, urgency: str, expires_at: str, topic: str, now: datetime.datetime
    ) -> Delivery:
        """
        Rebuild a delivery from :meth:`to_task`'s primitives.

        A task queued before SNOW-1495 carries none of them: it goes at
        ``normal`` urgency, held for the old twelve hours, with no topic.

        Args:
            urgency: The stored urgency, or "".
            expires_at: The stored expiry, ISO 8601, or "".
            topic: The stored topic, or "".
            now: The time of sending, for a task carrying no expiry.

        Returns:
            The delivery.

        """
        expiry = (
            datetime.datetime.fromisoformat(expires_at)
            if expires_at
            else now + FALLBACK_HOLD
        )
        return cls(urgency=urgency or "normal", expires_at=expiry, topic=topic)


def highest_urgency(urgencies: Iterable[str]) -> str:
    """
    Return the most urgent of ``urgencies``.

    Args:
        urgencies: RFC 8030 urgencies from ``URGENCY_ORDER``; an unknown or
            blank one counts as ``normal``.

    Returns:
        The highest, or ``normal`` when there are none.

    """
    ranks = [URGENCY_ORDER.index(u) if u in URGENCY_ORDER else 1 for u in urgencies]
    return URGENCY_ORDER[max(ranks)] if ranks else "normal"


def alert_topic(days: Iterable[datetime.date], season: bool) -> str:
    """
    Return the Topic header for a message about ``days``.

    Args:
        days: The days the message's places are for, at least one.
        season: Whether every place is a season start.

    Returns:
        ``season``, or ``alert-<YYYYMMDD>`` of the latest day: 14 URL-safe
        characters, within RFC 8030's 32.

    """
    if season:
        return SEASON_TOPIC
    return f"alert-{max(days):%Y%m%d}"


def _place_urgency(place: PlaceAlert, today: datetime.date) -> str:
    """
    Return one place's urgency.

    Args:
        place: The place.
        today: The local send date.

    Returns:
        ``high``, ``low`` or ``normal``.

    """
    change = place.change
    danger_up = (
        change.rating_direction > 0
        or bool(change.added)
        or change.subdivision_direction > 0
    )
    if place.on_trip or (place.target_date == today and danger_up):
        return "high"
    if place.kind == "season_start":
        return "low"
    return "normal"


def delivery_for(
    places: Sequence[PlaceAlert], today: datetime.date, now: datetime.datetime
) -> Delivery:
    """
    Return the push headers for a message about ``places``.

    Args:
        places: Every place in the message, at least one.
        today: The local send date.
        now: The time of sending, for a message none of whose places says
            when its bulletin ends.

    Returns:
        The most urgent place's urgency, the latest ``valid_to`` as the
        expiry, and the topic of the latest day.

    """
    urgency = highest_urgency(_place_urgency(p, today) for p in places)
    ends = [p.valid_to for p in places if p.valid_to is not None]
    expires_at = max(ends) if ends else now + FALLBACK_HOLD
    topic = alert_topic(
        (p.target_date for p in places),
        season=all(p.kind == "season_start" for p in places),
    )
    return Delivery(urgency=urgency, expires_at=expires_at, topic=topic)
