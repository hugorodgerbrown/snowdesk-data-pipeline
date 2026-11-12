"""
apps/bulletins/services/alerts.py — Who is told what, and when (SNOW-942).

The send step of bulletin alerts. For each account with alerts on, for each
of its alert places (region pins and saved resorts, every day; a saved trip,
on its own day only — SNOW-947), for today and tomorrow, it compares the
authoritative bulletin's state (A1) against what the send log last recorded,
decides, bundles every place that fired into one message (A3) and delivers
it by push and, as the fallback, email.

**The decision** (:func:`decide`, pure, shared with the replay harness):

- The log's latest row for the day has the same fingerprint → nothing.
- No row, and the day is tomorrow → the establishing issue. Compared with
  today's bulletin: ``NEW_BULLETIN`` sends; ``CHANGES`` sends only when
  something moved and otherwise logs ``SKIPPED``. With no bulletin today
  it is the first of a run: "Bulletins are back".
- No row, and the day is today → sent like an establishing issue only if
  alerts were on before the day began; otherwise ``BASELINE``, logged
  silently so the first message is about the next change rather than a
  day the person is already in.
- A row with another fingerprint → an update. Both modes send, unless
  nothing a reader would notice moved, which logs ``SKIPPED``.
- Today and tomorrow both firing for one place → only tomorrow is in the
  message; today is logged ``SKIPPED``.
- **A trip's day always gets its establishing issue**, in ``CHANGES`` mode
  too (Hugo, 2026-10-30): the evening before a saved trip, the person is
  told the day's level and problems whether or not they moved. Updates on
  the day itself still need a change.

**A trip is one place** (SNOW-945, Hugo 2026-10-31), covering every region
its route crosses. A one-region trip folds into a pin on its region as
before. A trip crossing several regions stays its own place, named for the
trip and opening the trip page, even when one of its regions is pinned: the
two are different doors, so that region can appear twice in a bundle. The
send log stays one row per account, region and day; a multi-region trip is
decided region by region, and the trip place fires when any of them does.

**Night hold.** Nothing is evaluated 22:00–06:30 Europe/Zurich, so an
issue landing overnight is picked up by the first run of the morning.

**Delivery.** :func:`deliver` writes the rows ``PENDING`` and queues one
worker per account (:func:`_worker_deliver_alert`), which pushes to every
active subscription and then decides email from what actually happened:
ALWAYS emails anyway, FALLBACK emails when no push was accepted (none
active, or every one failed — a 410 from an expired device included),
NEVER never does, and only ever to a verified address. The rows then
become ``SENT``, or ``FAILED`` when nothing reached the person. A FAILED
row is not "told": the next run compares against the last row that was,
and sends again. A message with no possible channel at all is logged
``SKIPPED`` so the state still advances.

A ``PENDING`` row counts as told only until it is :data:`STALE_PENDING`
old; after that its worker is presumed lost and the next run sends again
(SNOW-1362). Each run claims its rows under the send log's unique key and
one claim time: a row another run holds fresh ``PENDING`` is lost to it,
so two overlapping runs send one alert once, and the worker sends for and
settles only rows still carrying its claim. A device or the mail server
raising is that channel failing; a worker that crashes part-way still
settles its rows from what had already gone.

**The gate** (SNOW-1491). Most hourly runs find nothing new, so the
command evaluates only the accounts :func:`accounts_to_evaluate` names
since :func:`last_evaluated_at`, the start of the last run that evaluated:
those following a region whose bulletin changed, those with an alert to
retry, those who saved or changed a place, and those who turned alerts on.
With none, the run is skipped. Anyone passed by would have been evaluated
to the answer they were already given.

Rows are written only with ``commit=True``; without it the run reports what
it would send.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone
from django_tasks import task

from apps.accounts.models import Account
from apps.accounts.push_service import dispatch_push
from apps.accounts.services.alert_email import send_alert_email
from apps.bulletins.models import (
    SEASON_GAP_DAYS,
    Bulletin,
    BulletinAlert,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.alert_wording import (
    ALERT_TZ,
    AlertKind,
    AlertMessage,
    PlaceAlert,
    Release,
    alert_message,
)
from apps.bulletins.services.bulletin_change import (
    BulletinChange,
    BulletinState,
    summarise_change,
)
from apps.core.models import CommandRun
from apps.favourites.models import Favourite
from apps.ingest.public import state_of
from apps.regions.models import MicroRegion
from apps.regions.services.track_regions import (
    resolve_track_regions,
    track_regions,
)
from apps.trips.models import Trip, TripParticipant
from apps.weather.models import Weather

if TYPE_CHECKING:
    from django.contrib.auth.models import User


logger = logging.getLogger(__name__)

NIGHT_STARTS = datetime.time(22, 0)
NIGHT_ENDS = datetime.time(6, 30)

# A PENDING row older than this has lost its worker (a crash, a deploy, a
# dropped task): it no longer counts as told, and the next run may claim
# it again (SNOW-1362). Far longer than any delivery takes.
STALE_PENDING = datetime.timedelta(minutes=30)

_KIND_TO_ROW: dict[AlertKind, str] = {
    "new": BulletinAlert.Kind.NEW,
    "update": BulletinAlert.Kind.UPDATE,
    "season_start": BulletinAlert.Kind.SEASON_START,
}


def local_now(now: datetime.datetime) -> datetime.datetime:
    """Return ``now`` in the alert time zone."""
    return now.astimezone(ALERT_TZ)


def in_night_hold(now: datetime.datetime) -> bool:
    """
    Return True when ``now`` falls in the overnight hold.

    Args:
        now: An aware datetime.

    Returns:
        True between 22:00 and 06:30 Europe/Zurich.

    """
    t = local_now(now).time()
    return t >= NIGHT_STARTS or t < NIGHT_ENDS


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


@dataclass
class AlertPlace:
    """One place an account follows, with the name it knows it by.

    ``trip_url`` is set when a saved trip is (one of) the reasons: the
    alert then links to the trip page rather than the map.

    ``region`` is the place's region — the first in route order for a trip.
    ``regions`` is set only for a trip crossing several regions (SNOW-945):
    every one, in route order, and the place covers them all.
    """

    region: MicroRegion
    labels: list[str] = field(default_factory=list)
    trip_url: str = ""
    regions: list[MicroRegion] = field(default_factory=list)
    # Each trip name's own page, so a page listing names one by one opens
    # the right trip for each (two trips in one region are one place).
    label_urls: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Return the place's name ("Verbier" or "Verbier, Bruson")."""
        return ", ".join(self.labels) or self.region.name

    @property
    def covered(self) -> list[MicroRegion]:
        """Return every region the place covers, in route order."""
        return self.regions or [self.region]

    @property
    def key(self) -> str:
        """Return what identifies the place within one run.

        A region's pk for a one-region place, and the trip page for a trip
        crossing several, which is its own place even beside a pin.
        """
        if len(self.covered) > 1:
            return f"trip:{self.trip_url}"
        return f"region:{self.region.pk}"


def account_places(user: User) -> list[AlertPlace]:
    """
    Return the regions a user's alert places cover, one entry per region.

    A saved resort is known by the resort's name and alerts for its region;
    a region pin by its own name or the region's. Two places in one region
    are one entry with both names, so the region is reported once.

    Args:
        user: The account's user.

    Returns:
        The places, ordered by label.

    """
    favourites = (
        Favourite.objects.for_user(user)
        .alert_places()
        .select_related("region", "resort", "resort__region")
        .order_by("created_at")
    )
    by_region: dict[int, AlertPlace] = {}
    for fav in favourites:
        if fav.resort is not None and fav.resort.region is not None:
            region, label = fav.resort.region, fav.resort.name
        elif fav.region is not None:
            region, label = fav.region, fav.name or fav.region.name
        else:  # pragma: no cover — alert_places() excludes this
            continue
        place = by_region.setdefault(region.pk, AlertPlace(region=region))
        if label not in place.labels:
            place.labels.append(label)
    return sorted(by_region.values(), key=lambda p: p.label)


def trip_places(user: User, day: datetime.date) -> list[AlertPlace]:
    """
    Return the places a user's saved trips on one day make (SNOW-947, SNOW-945).

    The regions are every one the route crosses (``track_regions``), the
    rule the trip page and the Plan page use, so the alert covers what the
    trip page shows. A trip crossing one region is a place in that region,
    and two such trips in one region are one place with both names. A trip
    crossing several is one place of its own, covering all of them in
    route order. A track outside every region adds nothing. Every account
    that saved the trip counts, organiser or not; nobody is told who else
    holds it.

    Args:
        user: The account's user.
        day: The day the trips are dated.

    Returns:
        The places, labelled by trip name, ordered by label.

    """
    by_key: dict[str, AlertPlace] = {}
    trips = Trip.objects.for_user(user).filter(date=day).order_by("start_time")
    for trip in trips.only("uuid", "name", "route_name", "points", "crossed_regions"):
        regions = track_regions(trip.crossed_regions, trip.points)
        if not regions:
            continue
        url = reverse("trips:detail", kwargs={"uuid": trip.uuid})
        candidate = AlertPlace(
            region=regions[0], regions=regions if len(regions) > 1 else []
        )
        candidate.trip_url = url
        place = by_key.setdefault(candidate.key, candidate)
        if trip.display_name and trip.display_name not in place.labels:
            place.labels.append(trip.display_name)
            place.label_urls[trip.display_name] = url
        place.trip_url = place.trip_url or url
    return sorted(by_key.values(), key=lambda p: p.label)


def places_on(pins: list[AlertPlace], trips: list[AlertPlace]) -> list[AlertPlace]:
    """
    Return one day's places: every pin, with that day's trips folded in.

    A one-region trip in a region the person already pins is one place
    with both names, so the send log (one row per account, region and day)
    and the message carry it once. A trip crossing several regions is
    never folded: its door is the trip page (SNOW-945).

    Args:
        pins: ``account_places`` — the every-day places.
        trips: ``trip_places`` for the day.

    Returns:
        The day's places, ordered by label.

    """
    by_key = {
        p.key: replace(p, labels=list(p.labels), label_urls=dict(p.label_urls))
        for p in pins
    }
    for trip in trips:
        place = by_key.get(trip.key)
        if place is None:
            by_key[trip.key] = trip
            continue
        place.labels.extend(lbl for lbl in trip.labels if lbl not in place.labels)
        place.label_urls.update(trip.label_urls)
        place.trip_url = trip.trip_url
    return sorted(by_key.values(), key=lambda p: p.label)


# ---------------------------------------------------------------------------
# Bulletin state for a region and day
# ---------------------------------------------------------------------------


def authoritative_bulletin(region: MicroRegion, day: datetime.date) -> Bulletin | None:
    """
    Return the bulletin authoritative for a region on a day, or None.

    Args:
        region: The micro-region.
        day: The day forecast.

    Returns:
        ``RegionDayRating.source_bulletin`` for that pair.

    """
    rating = (
        RegionDayRating.objects.filter(region=region, date=day)
        .select_related("source_bulletin")
        .first()
    )
    return rating.source_bulletin if rating is not None else None


def had_bulletin_in_season(region: MicroRegion, day: datetime.date) -> bool:
    """
    Return whether a region had a bulletin in the season gap before a day.

    Args:
        region: The micro-region.
        day: The first day after the gap being judged (the day before the
            bulletin's own).

    Returns:
        True when any day in the ``SEASON_GAP_DAYS`` before ``day`` has a
        rated row (``RegionDayRatingQuerySet.rated``): a provider's
        ``no_rating`` bulletin is no bulletin here, as it is to the map's
        and calendar's date windows (SNOW-1431).

    """
    return (
        RegionDayRating.objects.rated()
        .filter(
            region=region,
            date__gte=day - datetime.timedelta(days=SEASON_GAP_DAYS),
            date__lt=day,
        )
        .exists()
    )


def day_state(region: MicroRegion, day: datetime.date) -> BulletinState | None:
    """
    Return the alert state of a region's authoritative bulletin on a day.

    Args:
        region: The micro-region.
        day: The day forecast.

    Returns:
        The state, or None when there is no bulletin with a render model.

    """
    bulletin = authoritative_bulletin(region, day)
    if bulletin is None or not bulletin.render_model:
        return None
    # The state the service stored with the model (SNOW-1341).
    return state_of(bulletin.render_model)[0]


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What to do about one region and day for one account."""

    outcome: str
    kind: AlertKind
    change: BulletinChange
    compared_to: datetime.date | None

    @property
    def sends(self) -> bool:
        """Return True when the decision is to tell the account."""
        return self.outcome == BulletinAlert.Outcome.SENT


def decide(
    *,
    mode: str,
    day: datetime.date,
    today: datetime.date,
    current: BulletinState,
    logged: BulletinState | None,
    has_row: bool,
    previous_day: BulletinState | None,
    enabled_before_day: bool,
    trip_day: bool = False,
    rated_in_season: bool = False,
) -> Decision:
    """
    Decide what one new state for one region and day means for one account.

    Call only when the state's fingerprint differs from the log's latest
    for the day (or there is no row).

    Args:
        mode: ``Account.NotifyWhen`` value (not OFF).
        day: The day the bulletin forecasts.
        today: The local date of the run.
        current: The state now authoritative.
        logged: The state the log's latest row recorded, if readable.
        has_row: Whether the log holds any row for this day.
        previous_day: The state authoritative for the day before ``day``.
        enabled_before_day: Whether alerts were on before ``day`` began.
        trip_day: Whether the person has a saved trip on ``day``; its
            establishing issue then sends in ``CHANGES`` mode too.
        rated_in_season: Whether the region had a bulletin in the
            ``SEASON_GAP_DAYS`` before ``previous_day``. With no bulletin
            yesterday, it tells a resumption ("Bulletins are back.") from
            the first bulletin of the winter (SNOW-1356).

    Returns:
        The decision.

    """
    sent, skipped = BulletinAlert.Outcome.SENT, BulletinAlert.Outcome.SKIPPED
    if has_row:
        change = summarise_change(logged, current)
        # Only a moved level or a problem added or removed is worth an
        # update; a shifted aspect or band is logged and not told.
        outcome = sent if change.is_notable else skipped
        return Decision(outcome, "update", change, day)

    compared_to = day - datetime.timedelta(days=1)
    if day <= today and not enabled_before_day:
        return Decision(
            BulletinAlert.Outcome.BASELINE,
            "new",
            summarise_change(previous_day, current),
            compared_to,
        )
    if previous_day is None:
        # A gap within the season is a resumption, sent as a new bulletin
        # with nothing to compare against; only the first is a season start.
        kind: AlertKind = "new" if rated_in_season else "season_start"
        return Decision(sent, kind, summarise_change(None, current), None)
    change = summarise_change(previous_day, current)
    if mode == Account.NotifyWhen.CHANGES and not change.is_notable and not trip_day:
        return Decision(skipped, "new", change, compared_to)
    return Decision(sent, "new", change, compared_to)


# ---------------------------------------------------------------------------
# One account
# ---------------------------------------------------------------------------


@dataclass
class PendingRow:
    """A send-log row waiting for its message."""

    place: AlertPlace
    day: datetime.date
    bulletin: Bulletin
    state: BulletinState
    decision: Decision
    # The state's fingerprint as the service stored it (SNOW-1341); the
    # send log's key, so the row is written under the value it was
    # compared on.
    fingerprint: str = ""
    # The region this row decides: the place's own, or one of a
    # multi-region trip's (SNOW-945). The send log is keyed on it.
    region: MicroRegion | None = None

    @property
    def log_region(self) -> MicroRegion:
        """Return the region the row is logged under."""
        return self.region if self.region is not None else self.place.region


@dataclass
class AccountRun:
    """What one run did for one account."""

    account: Account
    rows: list[PendingRow] = field(default_factory=list)
    message: AlertMessage | None = None
    # The local send date the message was worded for; deliver re-words a
    # partly claimed bundle on the same day (SNOW-1362).
    today: datetime.date | None = None
    # When the run read the send log, by the wall clock updated_at is
    # stamped with: a row written after it is another run's (SNOW-1362).
    evaluated_at: datetime.datetime | None = None
    push_count: int = 0
    emailed: bool = False

    @property
    def sending(self) -> list[PendingRow]:
        """Return the rows that become the message."""
        return [r for r in self.rows if r.decision.sends]


def _one_day_per_place(rows: list[PendingRow]) -> None:
    """
    Keep only the latest sending day for each place, in place.

    When today and tomorrow both fire for one place (alerts just turned on,
    or a same-day update landing with the evening issue), the message names
    tomorrow; today is logged SKIPPED so it is not announced later either.

    Args:
        rows: The run's rows.

    """
    latest: dict[str, datetime.date] = {}
    for row in rows:
        if row.decision.sends:
            key = row.place.key
            latest[key] = max(latest.get(key, row.day), row.day)
    for row in rows:
        if row.decision.sends and row.day < latest[row.place.key]:
            row.decision = replace(row.decision, outcome=BulletinAlert.Outcome.SKIPPED)


def forecast_new_snow(
    region: MicroRegion, day: datetime.date, today: datetime.date
) -> float | None:
    """
    Return the new snow forecast for a region's day, as known today (SNOW-944).

    Read from the row for that day at the region's centroid location: one
    row per day, refined by every fetch until the day passes (SNOW-1337), so
    a later day's row holds what the weather sheet says about it now. A past
    day is never read.

    Args:
        region: The micro-region.
        day: The bulletin's day.
        today: The local send date.

    Returns:
        The snowfall in cm, or None with no centroid, no row or a past day.

    """
    if region.centroid_location_id is None or day < today:
        return None
    row = (
        Weather.objects.filter(location_id=region.centroid_location_id, observed_on=day)
        .only("snowfall_sum")
        .first()
    )
    return row.snowfall_sum if row is not None else None


def evaluate_account(account: Account, now: datetime.datetime) -> AccountRun:
    """
    Work out every row and the one message for an account, writing nothing.

    Args:
        account: An account with alerts on.
        now: The run's time.

    Returns:
        The account's run, with ``message`` set when anything sends.

    """
    run = AccountRun(account=account)
    local = local_now(now)
    today = local.date()
    run.today = today
    # timezone.now(), not ``now``: it is compared with rows' updated_at.
    run.evaluated_at = timezone.now()
    # ``now``, not timezone.now(): the command passes timezone.now(), so the
    # two agree in production, and tests can pin the stale window.
    stale_before = now - STALE_PENDING
    day_start = datetime.datetime.combine(today, datetime.time(0), tzinfo=ALERT_TZ)
    pins = account_places(account.user)
    for day in (today, today + datetime.timedelta(days=1)):
        for place, region in (
            (place, region)
            for place in places_on(pins, trip_places(account.user, day))
            for region in place.covered
        ):
            bulletin = authoritative_bulletin(region, day)
            if bulletin is None or not bulletin.render_model:
                continue
            # The state and fingerprint the service stored with the model
            # (SNOW-1341); computed only for a model older than version 10.
            current, fingerprint = state_of(bulletin.render_model)
            # A FAILED row reached nobody, so it is not what the person was
            # last told: compare against the row before it, and send again.
            # So with a PENDING row gone stale: its worker never settled it.
            untold = [BulletinAlert.Outcome.FAILED]
            if place.trip_url and day > today:
                # A trip's evening alert always sends, so a quiet day logged
                # SKIPPED for the pin before the trip was saved was not the
                # trip's alert: look past it to what was actually sent.
                untold.append(BulletinAlert.Outcome.SKIPPED)
            last = (
                BulletinAlert.objects.for_day(account.pk, region.pk, day)
                .exclude(
                    Q(outcome__in=untold)
                    | Q(
                        outcome=BulletinAlert.Outcome.PENDING,
                        updated_at__lt=stale_before,
                    )
                )
                .first()
            )
            if last is not None and last.fingerprint == fingerprint:
                continue
            enabled_at = account.alerts_enabled_at
            day_began = (
                day_start if day == today else day_start + datetime.timedelta(days=1)
            )
            yesterday = day - datetime.timedelta(days=1)
            previous = None if last is not None else day_state(region, yesterday)
            decision = decide(
                mode=account.notify_when,
                day=day,
                today=today,
                current=current,
                logged=BulletinState.from_json(last.state) if last else None,
                has_row=last is not None,
                previous_day=previous,
                enabled_before_day=enabled_at is not None and enabled_at < day_began,
                trip_day=bool(place.trip_url),
                rated_in_season=(
                    last is None
                    and previous is None
                    and had_bulletin_in_season(region, yesterday)
                ),
            )
            run.rows.append(
                PendingRow(place, day, bulletin, current, decision, fingerprint, region)
            )
    _one_day_per_place(run.rows)
    if run.sending:
        run.message = _bundle_message(run.sending, today)
    return run


def _bundle_message(sending: list[PendingRow], today: datetime.date) -> AlertMessage:
    """
    Return the one message for a run's sending rows.

    Used by :func:`evaluate_account`, and by :func:`deliver` to re-word a
    bundle of which only some rows were claimed (SNOW-1362).

    Args:
        sending: The sending rows, at least one.
        today: The local send date.

    Returns:
        The message.

    """
    return alert_message(_place_alerts(sending), today)


def _place_alerts(sending: list[PendingRow]) -> list[PlaceAlert]:
    """
    Return one ``PlaceAlert`` per place that fired, in the rows' order.

    A one-region place is its row. A trip crossing several regions has a
    row per region that fired and is one alert covering them all, carrying
    each of their bulletins (SNOW-945); it is an update only when every one
    of its rows is.

    Args:
        sending: The run's sending rows.

    Returns:
        The places, at least one.

    """
    grouped: dict[str, list[PendingRow]] = {}
    for row in sending:
        grouped.setdefault(row.place.key, []).append(row)
    alerts: list[PlaceAlert] = []
    for rows in grouped.values():
        first = rows[0]
        kind: AlertKind = (
            "update"
            if all(r.decision.kind == "update" for r in rows)
            else next(r.decision.kind for r in rows if r.decision.kind != "update")
        )
        alerts.append(
            PlaceAlert(
                region_id=first.log_region.region_id,
                label=first.place.label,
                target_date=first.day,
                kind=kind,
                change=first.decision.change,
                releases=tuple(
                    Release(source=r.bulletin.source, issued_at=r.bulletin.issued_at)
                    for r in rows
                ),
            )
        )
    return alerts


def deliver(run: AccountRun) -> AccountRun:
    """
    Write an account's rows and queue its message, in one transaction.

    The rows and the queued task commit together, so a failed enqueue
    leaves no PENDING row behind to count as told.

    The rows that send are claimed ``PENDING`` (:func:`_claim_row`); the
    worker settles them as ``SENT`` or ``FAILED`` once it knows what reached
    the person. A row another run holds fresh ``PENDING`` is lost to it,
    so two overlapping runs never both send one alert (SNOW-1362). With no
    possible channel (no active push, and email off or unverified) the rows
    are written ``SKIPPED`` and nothing is queued.

    Args:
        run: An evaluated run.

    Returns:
        The run, with ``push_count`` (devices tried) and ``emailed`` (email
        planned before any push result) filled in, and ``message`` the one
        actually queued: re-worded when only some rows were claimed, None
        when none were.

    """
    account = run.account
    message = run.message
    subscriptions = list(account.push_subscriptions.active()) if message else []
    can_email = _can_email(account)
    reachable = message is not None and (bool(subscriptions) or can_email)
    if reachable:
        run.push_count = len(subscriptions)
        run.emailed = can_email and (
            account.alert_email == Account.AlertEmail.ALWAYS or not subscriptions
        )
    # One claim time for every row this run wins: the worker sends for, and
    # settles, only rows still carrying it.
    claimed_at = timezone.now()
    pending: list[int] = []
    won: set[tuple[int, datetime.date, str]] = set()
    lost = False
    with transaction.atomic():
        for row in _one_row_per_log_key(run.rows):
            fingerprint = row.fingerprint or row.state.fingerprint()
            sends = row.decision.sends and reachable
            defaults = {
                "bulletin": row.bulletin,
                "state": row.state.to_json(),
                "kind": _KIND_TO_ROW[row.decision.kind],
                "sent_push": False,
                "sent_email": False,
                "title": "",
                "body": "",
                "url": "",
            }
            if sends:
                alert = _claim_row(
                    account,
                    row,
                    fingerprint,
                    defaults,
                    claimed_at - STALE_PENDING,
                    run.evaluated_at,
                )
                if alert is None:
                    lost = True
                    continue
                pending.append(alert.pk)
                won.add((row.log_region.pk, row.day, fingerprint))
                continue
            outcome = row.decision.outcome
            if row.decision.sends:
                outcome = BulletinAlert.Outcome.SKIPPED
            # update_or_create, not get_or_create: a state that recurs on
            # the same day rewrites its row, which bumps updated_at and so
            # makes it the latest again (BulletinAlertQuerySet.for_day).
            BulletinAlert.objects.update_or_create(
                account=account,
                region=row.log_region,
                target_date=row.day,
                fingerprint=fingerprint,
                defaults={**defaults, "outcome": outcome},
            )
        if reachable and message is not None:
            _queue_claimed(run, message, pending, won, lost, claimed_at)
    return run


def _queue_claimed(
    run: AccountRun,
    message: AlertMessage,
    pending: list[int],
    won: set[tuple[int, datetime.date, str]],
    lost: bool,
    claimed_at: datetime.datetime,
) -> None:
    """
    Queue the worker for the rows this run claimed, inside deliver's transaction.

    Nothing claimed: another run holds every row and sends the message, so
    this run queues nothing and reports no message. Some rows lost: a partly
    lost bundle sends only the rows this run won; the others are another
    run's to send, so the message is re-worded without them rather than
    announce them twice.

    Args:
        run: The run being delivered, with a channel.
        message: The run's message, as evaluated.
        pending: The claimed rows' pks.
        won: Their (region pk, day, fingerprint) keys.
        lost: Whether any sending row was lost to another run.
        claimed_at: The claim time stamped on every claimed row.

    """
    if not pending:
        run.message = None
        run.push_count = 0
        run.emailed = False
        return
    if lost:
        message = run.message = _winners_message(run, won, claimed_at)
    # A queryset update, which auto_now does not touch: every claimed row
    # carries exactly the claim time the worker checks.
    BulletinAlert.objects.filter(pk__in=pending).update(
        title=message.title,
        body=message.body,
        url=message.url,
        updated_at=claimed_at,
    )
    # Inside the transaction: the DatabaseBackend writes its task row on
    # this connection, so the PENDING rows and the work that settles them
    # commit together. An enqueue that raises rolls the rows back, and the
    # next run sees the day as untold and tries again.
    _worker_deliver_alert.enqueue(
        run.account.pk,
        pending,
        claimed_at.isoformat(),
        message.as_push(),
        message.title,
        message.body,
        message.url,
    )


def _winners_message(
    run: AccountRun,
    won: set[tuple[int, datetime.date, str]],
    claimed_at: datetime.datetime,
) -> AlertMessage:
    """
    Return the message re-worded for only the send-log keys this run won.

    Args:
        run: The run being delivered.
        won: The (region pk, day, fingerprint) keys it claimed, at least one.
        claimed_at: The claim time, whose local date stands in for a run
            evaluated without one.

    Returns:
        The message naming only the winning places.

    """
    winners = [
        r
        for r in run.sending
        if (r.log_region.pk, r.day, r.fingerprint or r.state.fingerprint()) in won
    ]
    today = run.today or local_now(claimed_at).date()
    return _bundle_message(winners, today)


def _claim_row(
    account: Account,
    row: PendingRow,
    fingerprint: str,
    defaults: dict[str, object],
    stale_before: datetime.datetime,
    evaluated_at: datetime.datetime | None,
) -> BulletinAlert | None:
    """
    Claim one sending row of the send log for this run, or lose it.

    Locks the row by its unique key (``bulletin_alert_unique_state``). A
    row another run holds fresh ``PENDING`` is lost: that run sends it. So
    is a row written since this run was evaluated, whatever its outcome:
    an overlapping run claimed it and may already have sent it. Any other
    existing row (FAILED, a stale PENDING, or a state that recurs on the
    same day, all last written before this run looked) is rewritten
    ``PENDING``, which bumps ``updated_at`` and
    so makes it the latest again (BulletinAlertQuerySet.for_day). A missing
    row is created in a savepoint, and a concurrent create winning the
    unique key means this run lost it. Must run inside a transaction.

    Args:
        account: The account.
        row: The sending row.
        fingerprint: The send-log key's fingerprint.
        defaults: The row's fields, bar ``outcome``.
        stale_before: A PENDING row last written before this is stale.
        evaluated_at: When the run read the send log; None skips the check.

    Returns:
        The claimed row, or None when another run holds it.

    """
    key = {
        "account": account,
        "region": row.log_region,
        "target_date": row.day,
        "fingerprint": fingerprint,
    }
    fields = {**defaults, "outcome": BulletinAlert.Outcome.PENDING}
    existing = BulletinAlert.objects.select_for_update().filter(**key).first()
    if existing is not None:
        if (
            existing.outcome == BulletinAlert.Outcome.PENDING
            and existing.updated_at >= stale_before
        ) or (evaluated_at is not None and existing.updated_at > evaluated_at):
            return None
        for name, value in fields.items():
            setattr(existing, name, value)
        existing.save()
        return existing
    try:
        with transaction.atomic():
            return BulletinAlert.objects.create(**key, **fields)
    except IntegrityError:
        logger.info(
            "Bulletin alert row for account %s, region %s, %s claimed by another run",
            account.pk,
            row.log_region.pk,
            row.day,
        )
        return None


def _one_row_per_log_key(rows: list[PendingRow]) -> list[PendingRow]:
    """
    Return the rows to write, one per send-log key, a sending one winning.

    A trip crossing several regions is its own place even where a pin
    shares one of them (SNOW-945), so two rows can address one
    (region, day, fingerprint) row of the log. Written in turn, a pin's
    SKIPPED would overwrite the trip's PENDING and leave a delivered day
    recorded as untold. The row that sends is kept; otherwise the first.

    Args:
        rows: The run's rows, in evaluation order.

    Returns:
        The rows, in first-seen order, at most one per key.

    """
    chosen: dict[tuple[int, datetime.date, str], PendingRow] = {}
    for row in rows:
        key = (row.log_region.pk, row.day, row.fingerprint or row.state.fingerprint())
        held = chosen.get(key)
        if held is None or (row.decision.sends and not held.decision.sends):
            chosen[key] = row
    return list(chosen.values())


def _can_email(account: Account) -> bool:
    """Return True when alert email may go to this account at all."""
    return (
        account.alert_email != Account.AlertEmail.NEVER
        and account.is_verified
        and bool(account.user.email)
    )


def _claim_still_whole(
    claimed: QuerySet[BulletinAlert], alert_ids: list[int], account_id: int
) -> bool:
    """
    Return whether a delivery worker still holds every row of its bundle.

    None held: another run settled or re-claimed them, so nothing is sent.
    Some held: a newer run claimed the others and may already have told the
    person about those places. The payload was rendered for the whole bundle
    and cannot be re-worded here, so nothing is sent and the held rows
    become ``FAILED``, which the next run re-evaluates and re-words
    (SNOW-1362).

    Args:
        claimed: The rows still ``PENDING`` under this worker's claim.
        alert_ids: Every row the bundle was queued with.
        account_id: The account's pk, for the log.

    Returns:
        True when the worker should send.

    """
    held = claimed.count()
    if held == len(set(alert_ids)):
        return True
    if held:
        claimed.update(outcome=BulletinAlert.Outcome.FAILED, updated_at=timezone.now())
        logger.info(
            "Bulletin alert for account %s: part of the bundle claimed again; "
            "not sent, the rest left to the next run",
            account_id,
        )
    else:
        logger.info(
            "Bulletin alert for account %s: rows settled or claimed again; not sent",
            account_id,
        )
    return False


@task()
def _worker_deliver_alert(
    account_id: int,
    alert_ids: list[int],
    claimed_at: str,
    push: dict[str, str],
    title: str,
    body: str,
    url: str,
) -> None:
    """
    Background worker: push, fall back to email, and settle the rows.

    Push is tried on every active device first, synchronously, so email can
    follow what actually happened rather than what was queued: FALLBACK
    emails when no push was accepted. The rows become ``SENT`` when either
    channel reached the person and ``FAILED`` when neither did, which the
    next run retries.

    Only rows still ``PENDING`` under this claim (``updated_at`` equal to
    ``claimed_at``) are sent for or touched, so a replayed task never undoes
    a later run's rows, and a worker whose rows went stale and were claimed
    again by a newer run sends nothing (SNOW-1362). If only some of them
    were claimed again, it sends nothing either and marks the rest
    ``FAILED``, so the next run re-words the message for them. A device or the mail
    server raising counts as that channel not reaching the person; anything
    else raising still settles the rows from what had already gone, then
    re-raises.

    Args:
        account_id: The account's pk.
        alert_ids: The ``BulletinAlert`` rows this message carries.
        claimed_at: The rows' claim time, ISO 8601, as :func:`deliver`
            stamped it.
        push: The push payload (``AlertMessage.as_push``).
        title: The email subject.
        body: The email body.
        url: Where the alert lands.

    """
    claimed = BulletinAlert.objects.filter(
        pk__in=alert_ids,
        outcome=BulletinAlert.Outcome.PENDING,
        updated_at=datetime.datetime.fromisoformat(claimed_at),
    )
    if not _claim_still_whole(claimed, alert_ids, account_id):
        return
    pushed = False
    emailed = False
    try:
        account = Account.objects.select_related("user").filter(pk=account_id).first()
        if account is None:
            logger.info("Bulletin alert for account %s: account gone", account_id)
            return
        for sub in account.push_subscriptions.active():
            try:
                pushed = dispatch_push(sub, push)["ok"] or pushed
            except Exception:
                logger.exception(
                    "Bulletin alert push to subscription %s raised", sub.pk
                )
        wants_email = account.alert_email == Account.AlertEmail.ALWAYS or (
            account.alert_email == Account.AlertEmail.FALLBACK and not pushed
        )
        if wants_email and _can_email(account):
            try:
                emailed = send_alert_email(account, title, body, url)
            except Exception:
                logger.exception(
                    "Bulletin alert email for account %s raised", account_id
                )
        if not (pushed or emailed):
            logger.warning(
                "Bulletin alert for account %s reached no channel; retrying next run",
                account_id,
            )
    finally:
        # Settled whatever happened, so a crash after a push went out does
        # not leave the alert to be sent again once its row goes stale.
        # One updated_at for every row of the message: it is when it went,
        # and what tells one delivery's rows from another's in recent_alerts.
        # If this update itself fails (database gone), the rows stay PENDING
        # and the next run after STALE_PENDING claims them again.
        reached = pushed or emailed
        claimed.update(
            outcome=(
                BulletinAlert.Outcome.SENT if reached else BulletinAlert.Outcome.FAILED
            ),
            sent_push=pushed,
            sent_email=emailed,
            updated_at=timezone.now(),
        )


# ---------------------------------------------------------------------------
# Since the last bulletin (the region panel strip and the landing page)
# ---------------------------------------------------------------------------


def since_previous_day(
    region: MicroRegion, day: datetime.date
) -> tuple[BulletinChange, datetime.date] | None:
    """
    Return what moved in a region's bulletin since the day before.

    Args:
        region: The micro-region.
        day: The day being read.

    Returns:
        ``(change, previous day)``, or None when either day has no bulletin.

    """
    current = day_state(region, day)
    previous_date = day - datetime.timedelta(days=1)
    previous = day_state(region, previous_date)
    if current is None or previous is None:
        return None
    return summarise_change(previous, current), previous_date


# ---------------------------------------------------------------------------
# The gate: who needs evaluating since the last run that did (SNOW-1491)
# ---------------------------------------------------------------------------

# How many recent successful runs ``last_evaluated_at`` reads to find one
# that evaluated. Held and gated runs are filtered out in the query, so only
# the legacy night-time runs (before SNOW-1491 tallied ``held``) are skipped
# in Python, and a night has at most nine of them.
WATERMARK_CANDIDATES = 50

# How far before the watermark the gate looks. A row written in a
# transaction that began before the previous run started, and committed
# after that run had read, carries an ``updated_at`` before the watermark;
# looking back this far catches it at the next run. The cost is one extra
# evaluation of an account whose change has already been told.
GATE_OVERLAP = datetime.timedelta(minutes=15)


def last_evaluated_at(
    command: str = "send_bulletin_alerts",
) -> datetime.datetime | None:
    """
    Return when the most recent run that evaluated accounts started.

    The watermark of the gate (SNOW-1491): a committed, successful run that
    was neither held for the night nor gated. A run recorded before the
    ``held`` tally existed is judged by its start time instead: inside the
    night hold it evaluated nothing, unless it was told to ignore the hold.
    A dry run, a failed or partial run, and the run in progress (still
    ``RUNNING``) never count, so a run that did not tell everyone leaves
    the next one to look as far back as the last one that did.

    Args:
        command: The command whose runs are read.

    Returns:
        The run's ``started_at``, or None when no run has evaluated yet.

    """
    skipped = Q(counts__has_key="held", counts__held__gt=0) | Q(
        counts__has_key="gated", counts__gated__gt=0
    )
    runs = (
        CommandRun.objects.filter(
            command=command,
            mode=CommandRun.MODE.COMMIT,
            status=CommandRun.STATUS.SUCCEEDED,
        )
        .exclude(skipped)
        .order_by("-started_at", "-id")
        .only("started_at", "arguments", "counts")[:WATERMARK_CANDIDATES]
    )
    for run in runs:
        legacy_hold = (
            "held" not in run.counts
            and in_night_hold(run.started_at)
            and not run.arguments.get("ignore_night_hold")
        )
        if not legacy_hold:
            return run.started_at
    return None


def _changed_regions(
    since: datetime.datetime, window: datetime.datetime, days: list[datetime.date]
) -> set[int]:
    """
    Return the regions whose bulletin for today or tomorrow may have moved.

    A region linked to a bulletin written after ``window``: the bulletin's
    own ``updated_at`` is the durable marker, because a failed recompute
    deletes the region's ``RegionDayRating`` rather than updating it. Then
    a region whose rating for one of ``days`` was written after ``window``,
    and every rated region on a day the previous run (at ``since``) did not
    look at, as today and tomorrow move on.

    Args:
        since: The watermark, the previous evaluating run's start.
        window: The watermark less :data:`GATE_OVERLAP`.
        days: Today and tomorrow, local dates.

    Returns:
        The regions' primary keys.

    """
    first = local_now(since).date()
    covered = {first, first + datetime.timedelta(days=1)}
    uncovered = [day for day in days if day not in covered]
    linked = RegionBulletin.objects.filter(bulletin__updated_at__gt=window).values_list(
        "region_id", flat=True
    )
    rated = (
        RegionDayRating.objects.filter(date__in=days)
        .filter(Q(updated_at__gt=window) | Q(date__in=uncovered))
        .values_list("region_id", flat=True)
    )
    return set(linked) | set(rated)


def _trips_crossing(regions: set[int], days: list[datetime.date]) -> list[int]:
    """
    Return the trips on ``days`` whose route crosses any of ``regions``.

    The regions are read as ``trip_places`` reads them: a trip's stored
    ``crossed_regions``, or for one not yet resolved, the regions worked
    out live from its track (``resolve_track_regions``). The changed
    regions' region ids are loaded once, so a stored trip costs no
    query of its own (Codex, PR #1567).

    Args:
        regions: Region primary keys.
        days: Today and tomorrow, local dates.

    Returns:
        The trips' primary keys.

    """
    wanted = set(
        MicroRegion.objects.filter(pk__in=regions).values_list("region_id", flat=True)
    )
    trips = Trip.objects.filter(date__in=days).only("pk", "points", "crossed_regions")
    crossing: list[int] = []
    for trip in trips:
        ids = (
            resolve_track_regions(trip.points)
            if trip.crossed_regions is None
            else trip.crossed_regions
        )
        if wanted.intersection(ids):
            crossing.append(trip.pk)
    return crossing


def accounts_to_evaluate(since: datetime.datetime, now: datetime.datetime) -> set[int]:
    """
    Return the alerting accounts something may have changed for (SNOW-1491).

    Anything not returned would be evaluated to the same answer as at the
    run that started at ``since``, so the run may pass it by. An account is
    returned for any of four reasons, everything compared with ``since``
    less :data:`GATE_OVERLAP`:

    - **A bulletin changed** in a region it follows: a pinned region, a
      saved resort's region, or a region a trip of today or tomorrow
      crosses (every participant of that trip).
    - **An alert is undelivered**: a send-log row for today or tomorrow
      that is ``FAILED``, or ``PENDING`` and older than
      :data:`STALE_PENDING`, which ``evaluate_account`` sends again.
    - **A place was saved or changed**: a favourite, a trip joined, or a
      trip the account is on edited.
    - **Alerts were turned on**: ``alerts_enabled_at``, which only turning
      them on moves, and not ``Account.updated_at``, which any profile
      save does.

    Over-selecting is safe: an account evaluated with nothing new is
    told nothing. The queries do not grow with the number of accounts.

    Args:
        since: The watermark (:func:`last_evaluated_at`).
        now: The run's time.

    Returns:
        The accounts' primary keys.

    """
    today = local_now(now).date()
    days = [today, today + datetime.timedelta(days=1)]
    window = since - GATE_OVERLAP
    wanted = (
        Q(
            pk__in=BulletinAlert.objects.filter(target_date__in=days)
            .filter(
                Q(outcome=BulletinAlert.Outcome.FAILED)
                | Q(
                    outcome=BulletinAlert.Outcome.PENDING,
                    updated_at__lt=now - STALE_PENDING,
                )
            )
            # A failure a later row has superseded is not undelivered:
            # evaluate_account reads that later row and has nothing to
            # retry (Codex, PR #1567).
            .exclude(
                Exists(
                    BulletinAlert.objects.filter(
                        account_id=OuterRef("account_id"),
                        region_id=OuterRef("region_id"),
                        target_date=OuterRef("target_date"),
                        updated_at__gt=OuterRef("updated_at"),
                    ).exclude(outcome=BulletinAlert.Outcome.FAILED)
                )
            )
            .values("account_id")
        )
        | Q(
            user_id__in=Favourite.objects.filter(updated_at__gt=window).values(
                "user_id"
            )
        )
        | Q(
            user_id__in=TripParticipant.objects.filter(
                Q(updated_at__gt=window) | Q(trip__updated_at__gt=window)
            ).values("user_id")
        )
        | Q(alerts_enabled_at__gt=window)
    )
    regions = _changed_regions(since, window, days)
    if regions:
        wanted |= Q(
            user_id__in=Favourite.objects.alert_places()
            .filter(Q(region_id__in=regions) | Q(resort__region_id__in=regions))
            .values("user_id")
        )
        trips = _trips_crossing(regions, days)
        if trips:
            wanted |= Q(
                user_id__in=TripParticipant.objects.filter(trip_id__in=trips).values(
                    "user_id"
                )
            )
    return set(Account.objects.alerting().filter(wanted).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# The bell: what an account was sent (SNOW-1333)
# ---------------------------------------------------------------------------

# How far back the map's bell lists alerts (Hugo, 2026-10-30: one week).
RECENT_ALERT_DAYS: int = 7


@dataclass(frozen=True)
class SentAlert:
    """One message an account was sent, as the map's bell lists it."""

    title: str
    body: str
    url: str
    sent_at: datetime.datetime


def recent_alerts(account: Account, now: datetime.datetime) -> list[SentAlert]:
    """
    Return the messages an account was sent in the past week, newest first.

    A bundle writes one row per place, each carrying the whole message, and
    ``_worker_deliver_alert`` stamps them all with one ``updated_at``, so
    rows with the same words and the same send time are one message. Two
    sends with the same words (a second update in one validity window) stay
    two entries.

    Args:
        account: The account.
        now: The current time.

    Returns:
        One entry per message.

    """
    rows = (
        BulletinAlert.objects.filter(
            account=account,
            updated_at__gte=now - datetime.timedelta(days=RECENT_ALERT_DAYS),
        )
        .sent()
        .exclude(title="")
        .order_by("-updated_at", "-id")
        .only("title", "body", "url", "updated_at")
    )
    out: list[SentAlert] = []
    for row in rows:
        if out and (
            out[-1].title,
            out[-1].body,
            out[-1].url,
            out[-1].sent_at,
        ) == (row.title, row.body, row.url, row.updated_at):
            continue
        out.append(SentAlert(row.title, row.body, row.url, row.updated_at))
    return out
