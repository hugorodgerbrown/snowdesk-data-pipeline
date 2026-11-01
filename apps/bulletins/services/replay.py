"""
apps/bulletins/services/replay.py — A season of bulletin alerts, replayed (SNOW-949).

Walks a provider's committed archive for one region in publication order
and returns every alert a subscriber to that region would have received:
the same state (A1), the same decision (``alerts.decide``) and the same
words (A3) as a live run, with no database, no ``PushSubscription`` and no
send. It exists so the copy can be read in sequence before anyone gets it
— whether a three-week wind slab goes stale, whether a run of restatements
reads as broken, what a week looks like on a phone.

The simulated subscriber has followed the region since before the season,
in the chosen mode. Each issue, as it is published, becomes authoritative
for its day when its validity starts no earlier than the one it replaces
(the ``RegionDayRating.source_bulletin`` rule); its day is then decided
against the replayed send log. An issue published in the night hold
(22:00–06:30 Europe/Zurich) is delivered at 06:30, as the scheduled job
would deliver it.

Pure apart from reading the archive file: ``replay_region`` takes the
records, so tests pass their own.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from apps.bulletins.services.alert_wording import PlaceAlert, alert_message
from apps.bulletins.services.alerts import (
    ALERT_TZ,
    NIGHT_ENDS,
    SEASON_GAP_DAYS,
    decide,
    in_night_hold,
)
from apps.bulletins.services.bulletin_change import BulletinState, bulletin_state
from apps.bulletins.services.golden_week import iter_archive_records, target_day
from apps.ingest.public import build_render_model

logger = logging.getLogger(__name__)

DEFAULT_REGION = "CH-4115"


@dataclass(frozen=True)
class ReplayedAlert:
    """One decision in a replayed season: what was, or was not, sent."""

    delivered_at: datetime.datetime
    published_at: datetime.datetime
    target_date: datetime.date
    kind: str
    outcome: str
    title: str
    body: str
    url: str

    @property
    def sent(self) -> bool:
        """Return True when a subscriber would have been told."""
        return self.outcome == "SENT"


def _parse(value: str) -> datetime.datetime:
    """Return an aware datetime from an ISO string ("Z" accepted)."""
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _valid_until(raw: dict[str, Any]) -> datetime.datetime | None:
    """Return the end of an issue's validity, or None when it gives none."""
    end = (raw.get("validTime") or {}).get("endTime")
    return _parse(end) if end else None


def _delivery_time(published: datetime.datetime) -> datetime.datetime:
    """Return when the scheduled job would deliver an issue published then."""
    if not in_night_hold(published):
        return published
    local = published.astimezone(ALERT_TZ)
    day = (
        local.date()
        if local.time() < NIGHT_ENDS
        else local.date() + datetime.timedelta(days=1)
    )
    return datetime.datetime.combine(day, NIGHT_ENDS, tzinfo=ALERT_TZ)


def region_issues(
    records: Iterable[dict[str, Any]], source: str, region_id: str
) -> list[tuple[datetime.datetime, datetime.datetime, datetime.date, dict[str, Any]]]:
    """
    Return a region's issues as ``(published, valid_from, day, raw)``, in order.

    Args:
        records: CAAML payloads from one provider's archive.
        source: The provider (``Bulletin.Source`` value).
        region_id: The micro-region, e.g. ``CH-4115``.

    Returns:
        The issues covering the region, ordered by publication time.

    """
    out = []
    for raw in records:
        if not any(r.get("regionID") == region_id for r in raw.get("regions") or []):
            continue
        day = target_day(source, raw)
        start = (raw.get("validTime") or {}).get("startTime")
        published = raw.get("publicationTime") or start
        if day is None or not start or not published:
            continue
        out.append((_parse(published), _parse(start), day, raw))
    out.sort(key=lambda i: i[0])
    return out


def _last_per_delivery(
    issues: list[tuple[datetime.datetime, datetime.datetime, datetime.date, Any]],
) -> dict[tuple[datetime.date, datetime.datetime], int]:
    """
    Return, per (day, delivery time), the index of the issue a run would see.

    Issues published during the night hold are all delivered at 06:30, and
    the 06:30 run sees only whichever of them is then authoritative for its
    day. Mirrors ``replay_region``'s authority rule (a later ``valid_from``
    never loses to an earlier one) without building any render model.

    Args:
        issues: ``region_issues`` output, in publication order.

    Returns:
        The index of the last issue to become authoritative for each day at
        each delivery time.

    """
    held: dict[datetime.date, datetime.datetime] = {}
    out: dict[tuple[datetime.date, datetime.datetime], int] = {}
    for index, (published, valid_from, day, _raw) in enumerate(issues):
        if day in held and valid_from < held[day]:
            continue
        held[day] = valid_from
        out[(day, _delivery_time(published))] = index
    return out


def replay_region(
    records: Iterable[dict[str, Any]],
    *,
    source: str,
    region_id: str = DEFAULT_REGION,
    mode: str = "NEW_BULLETIN",
    label: str | None = None,
    new_snow: Mapping[datetime.date, float] | None = None,
) -> list[ReplayedAlert]:
    """
    Replay every alert decision for one region across an archive.

    Args:
        records: CAAML payloads from one provider's archive.
        source: The provider.
        region_id: The micro-region.
        mode: ``NEW_BULLETIN`` or ``CHANGES``.
        label: The place's name in the messages (defaults to the region's
            name in the first issue).
        new_snow: Snowfall in cm per bulletin day, for the new snow line
            (SNOW-944). The archive carries no weather, so without it the
            replay names none.

    Returns:
        Every decision, sent or not, in delivery order.

    """
    issues = region_issues(records, source, region_id)
    last_delivered = _last_per_delivery(issues)
    authoritative: dict[datetime.date, tuple[datetime.datetime, BulletinState]] = {}
    logged: dict[datetime.date, BulletinState] = {}
    out: list[ReplayedAlert] = []
    for index, (published, valid_from, day, raw) in enumerate(issues):
        if label is None:
            label = next(
                (
                    r.get("name")
                    for r in raw.get("regions") or []
                    if r.get("regionID") == region_id
                ),
                region_id,
            )
        held = authoritative.get(day)
        if held is not None and valid_from < held[0]:
            continue
        try:
            state = bulletin_state(build_render_model(raw))
        except Exception:
            logger.warning("Replay: skipping an issue whose render model failed")
            continue
        authoritative[day] = (valid_from, state)
        delivered = _delivery_time(published)
        if last_delivered[(day, delivered)] != index:
            # A later issue for this day goes out at the same moment (both
            # held overnight): the live run only ever sees that one.
            continue
        last = logged.get(day)
        if last is not None and last.fingerprint() == state.fingerprint():
            continue
        today = delivered.astimezone(ALERT_TZ).date()
        if day < today:
            # A late correction to a day already over is never announced.
            continue
        previous = authoritative.get(day - datetime.timedelta(days=1))
        decision = decide(
            mode=mode,
            day=day,
            today=today,
            current=state,
            logged=last,
            has_row=last is not None,
            previous_day=previous[1] if previous else None,
            enabled_before_day=True,
            rated_in_season=any(
                day - datetime.timedelta(days=gap) in authoritative
                for gap in range(2, SEASON_GAP_DAYS + 2)
            ),
        )
        logged[day] = state
        message = alert_message(
            [
                PlaceAlert(
                    region_id=region_id,
                    label=label,
                    target_date=day,
                    kind=decision.kind,
                    change=decision.change,
                    compared_to=decision.compared_to,
                    valid_from=valid_from,
                    valid_until=_valid_until(raw),
                    new_snow_cm=(new_snow or {}).get(day),
                )
            ],
            today,
        )
        out.append(
            ReplayedAlert(
                delivered_at=delivered,
                published_at=published,
                target_date=day,
                kind=decision.kind,
                outcome=str(decision.outcome),
                title=message.title,
                body=message.body,
                url=message.url,
            )
        )
    out.sort(key=lambda a: a.delivered_at)
    return out


def replay_archive(
    *, source: str, region_id: str = DEFAULT_REGION, mode: str = "NEW_BULLETIN"
) -> list[ReplayedAlert]:
    """
    Replay a region across its provider's committed archive.

    Args:
        source: The provider.
        region_id: The micro-region.
        mode: ``NEW_BULLETIN`` or ``CHANGES``.

    Returns:
        Every decision, in delivery order.

    """
    return replay_region(
        iter_archive_records(source), source=source, region_id=region_id, mode=mode
    )
