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

Rows are written only with ``commit=True``; without it the run reports what
it would send.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django_tasks import task

from apps.accounts.models import Account
from apps.accounts.push_service import dispatch_push
from apps.accounts.services.alert_email import send_alert_email
from apps.bulletins.models import Bulletin, BulletinAlert, RegionDayRating
from apps.bulletins.services.alert_wording import (
    ALERT_TZ,
    AlertKind,
    AlertMessage,
    PlaceAlert,
    alert_message,
)
from apps.bulletins.services.bulletin_change import (
    BulletinChange,
    BulletinState,
    summarise_change,
)
from apps.favourites.models import Favourite
from apps.ingest.public import state_of
from apps.trips.models import Trip
from apps.trips.services.plan_days import start_region
from apps.weather.models import Weather

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

NIGHT_STARTS = datetime.time(22, 0)
NIGHT_ENDS = datetime.time(6, 30)

# A region with no bulletin for this long before a day starts a new
# season on it; a shorter gap (SLF pauses early in the winter) is a
# resumption (SNOW-1356).
SEASON_GAP_DAYS: int = 60

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
    """One region an account follows, with the name it knows it by.

    ``trip_url`` is set when a saved trip is (one of) the reasons: the
    alert then links to the trip page rather than the map.
    """

    region: MicroRegion
    labels: list[str] = field(default_factory=list)
    trip_url: str = ""

    @property
    def label(self) -> str:
        """Return the place's name ("Verbier" or "Verbier, Bruson")."""
        return ", ".join(self.labels) or self.region.name


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
    Return the regions a user's saved trips on one day start in (SNOW-947).

    The region is the route's start, the rule the trip page and the Plan
    page already use (``plan_days.start_region``), so the alert names the
    region and level the trip page shows. A start outside every region
    adds nothing. Every account that saved the trip counts, organiser or
    not; nobody is told who else holds it.

    Args:
        user: The account's user.
        day: The day the trips are dated.

    Returns:
        One place per region, labelled by trip name, ordered by label.

    """
    by_region: dict[int, AlertPlace] = {}
    trips = Trip.objects.for_user(user).filter(date=day).order_by("start_time")
    for trip in trips.only("uuid", "name", "route_name", "points"):
        region = start_region(trip.points)
        if region is None:
            continue
        place = by_region.setdefault(region.pk, AlertPlace(region=region))
        if trip.display_name and trip.display_name not in place.labels:
            place.labels.append(trip.display_name)
        place.trip_url = place.trip_url or reverse(
            "trips:detail", kwargs={"uuid": trip.uuid}
        )
    return sorted(by_region.values(), key=lambda p: p.label)


def places_on(pins: list[AlertPlace], trips: list[AlertPlace]) -> list[AlertPlace]:
    """
    Return one day's places: every pin, with that day's trips folded in.

    A trip in a region the person already pins is one place with both
    names, so the send log (one row per account, region and day) and the
    message carry it once.

    Args:
        pins: ``account_places`` — the every-day places.
        trips: ``trip_places`` for the day.

    Returns:
        The day's places, ordered by label.

    """
    by_region = {p.region.pk: replace(p, labels=list(p.labels)) for p in pins}
    for trip in trips:
        place = by_region.get(trip.region.pk)
        if place is None:
            by_region[trip.region.pk] = trip
            continue
        place.labels.extend(lbl for lbl in trip.labels if lbl not in place.labels)
        place.trip_url = trip.trip_url
    return sorted(by_region.values(), key=lambda p: p.label)


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
        True when any day in the ``SEASON_GAP_DAYS`` before ``day`` has an
        authoritative bulletin.

    """
    return RegionDayRating.objects.filter(
        region=region,
        date__gte=day - datetime.timedelta(days=SEASON_GAP_DAYS),
        date__lt=day,
        source_bulletin__isnull=False,
    ).exists()


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


@dataclass
class AccountRun:
    """What one run did for one account."""

    account: Account
    rows: list[PendingRow] = field(default_factory=list)
    message: AlertMessage | None = None
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
    latest: dict[int, datetime.date] = {}
    for row in rows:
        if row.decision.sends:
            pk = row.place.region.pk
            latest[pk] = max(latest.get(pk, row.day), row.day)
    for row in rows:
        if row.decision.sends and row.day < latest[row.place.region.pk]:
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
    day_start = datetime.datetime.combine(today, datetime.time(0), tzinfo=ALERT_TZ)
    pins = account_places(account.user)
    for day in (today, today + datetime.timedelta(days=1)):
        for place in places_on(pins, trip_places(account.user, day)):
            bulletin = authoritative_bulletin(place.region, day)
            if bulletin is None or not bulletin.render_model:
                continue
            # The state and fingerprint the service stored with the model
            # (SNOW-1341); computed only for a model older than version 10.
            current, fingerprint = state_of(bulletin.render_model)
            # A FAILED row reached nobody, so it is not what the person was
            # last told: compare against the row before it, and send again.
            untold = [BulletinAlert.Outcome.FAILED]
            if place.trip_url and day > today:
                # A trip's evening alert always sends, so a quiet day logged
                # SKIPPED for the pin before the trip was saved was not the
                # trip's alert: look past it to what was actually sent.
                untold.append(BulletinAlert.Outcome.SKIPPED)
            last = (
                BulletinAlert.objects.for_day(account.pk, place.region.pk, day)
                .exclude(outcome__in=untold)
                .first()
            )
            if last is not None and last.fingerprint == fingerprint:
                continue
            enabled_at = account.alerts_enabled_at
            day_began = (
                day_start if day == today else day_start + datetime.timedelta(days=1)
            )
            yesterday = day - datetime.timedelta(days=1)
            previous = None if last is not None else day_state(place.region, yesterday)
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
                    and had_bulletin_in_season(place.region, yesterday)
                ),
            )
            run.rows.append(
                PendingRow(place, day, bulletin, current, decision, fingerprint)
            )
    _one_day_per_place(run.rows)
    sending = run.sending
    if sending:
        run.message = alert_message(
            [
                PlaceAlert(
                    region_id=r.place.region.region_id,
                    label=r.place.label,
                    target_date=r.day,
                    kind=r.decision.kind,
                    change=r.decision.change,
                    compared_to=r.decision.compared_to,
                    url=r.place.trip_url,
                    valid_from=r.bulletin.valid_from,
                    valid_until=r.bulletin.valid_to,
                    new_snow_cm=(
                        forecast_new_snow(r.place.region, r.day, today)
                        if len(sending) == 1
                        else None
                    ),
                )
                for r in sending
            ],
            today,
        )
    return run


def deliver(run: AccountRun) -> AccountRun:
    """
    Write an account's rows and queue its message, in one transaction.

    The rows and the queued task commit together, so a failed enqueue
    leaves no PENDING row behind to count as told.

    The rows that send are written ``PENDING``; the worker settles them as
    ``SENT`` or ``FAILED`` once it knows what reached the person. With no
    possible channel (no active push, and email off or unverified) they are
    written ``SKIPPED`` and nothing is queued.

    Args:
        run: An evaluated run.

    Returns:
        The run, with ``push_count`` (devices tried) and ``emailed`` (email
        planned before any push result) filled in.

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
    pending: list[int] = []
    with transaction.atomic():
        for row in run.rows:
            outcome = row.decision.outcome
            sends = row.decision.sends and reachable
            if row.decision.sends:
                outcome = (
                    BulletinAlert.Outcome.PENDING
                    if reachable
                    else BulletinAlert.Outcome.SKIPPED
                )
            # update_or_create, not get_or_create: a state that recurs on
            # the same day rewrites its row, which bumps updated_at and so
            # makes it the latest again (BulletinAlertQuerySet.for_day).
            alert, _ = BulletinAlert.objects.update_or_create(
                account=account,
                region=row.place.region,
                target_date=row.day,
                fingerprint=row.fingerprint or row.state.fingerprint(),
                defaults={
                    "bulletin": row.bulletin,
                    "state": row.state.to_json(),
                    "kind": _KIND_TO_ROW[row.decision.kind],
                    "outcome": outcome,
                    "sent_push": False,
                    "sent_email": False,
                    "title": message.title if sends and message else "",
                    "body": message.body if sends and message else "",
                    "url": message.url if sends and message else "",
                },
            )
            if sends:
                pending.append(alert.pk)
        # Inside the transaction: the DatabaseBackend writes its task row on
        # this connection, so the PENDING rows and the work that settles them
        # commit together. An enqueue that raises rolls the rows back, and
        # the next run sees the day as untold and tries again.
        if message is not None and pending:
            _worker_deliver_alert.enqueue(
                account.pk,
                pending,
                message.as_push(),
                message.title,
                message.body,
                message.url,
            )
    return run


def _can_email(account: Account) -> bool:
    """Return True when alert email may go to this account at all."""
    return (
        account.alert_email != Account.AlertEmail.NEVER
        and account.is_verified
        and bool(account.user.email)
    )


@task()
def _worker_deliver_alert(
    account_id: int,
    alert_ids: list[int],
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
    next run retries. Only ``PENDING`` rows are touched, so a replayed task
    never undoes a later run's rows.

    Args:
        account_id: The account's pk.
        alert_ids: The ``BulletinAlert`` rows this message carries.
        push: The push payload (``AlertMessage.as_push``).
        title: The email subject.
        body: The email body.
        url: Where the alert lands.

    """
    account = Account.objects.select_related("user").filter(pk=account_id).first()
    if account is None:
        logger.info("Bulletin alert for account %s: account gone", account_id)
        return
    pushed = False
    for sub in account.push_subscriptions.active():
        pushed = dispatch_push(sub, push)["ok"] or pushed
    emailed = False
    wants_email = account.alert_email == Account.AlertEmail.ALWAYS or (
        account.alert_email == Account.AlertEmail.FALLBACK and not pushed
    )
    if wants_email and _can_email(account):
        send_alert_email(account, title, body, url)
        emailed = True
    reached = pushed or emailed
    if not reached:
        logger.warning(
            "Bulletin alert for account %s reached no channel; retrying next run",
            account_id,
        )
    # One updated_at for every row of the message: it is when it went, and
    # what tells one delivery's rows from another's in recent_alerts.
    BulletinAlert.objects.filter(
        pk__in=alert_ids, outcome=BulletinAlert.Outcome.PENDING
    ).update(
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
