"""
apps/bulletins/services/alerts.py — Who is told what, and when (SNOW-942).

The send step of bulletin alerts. For each account with alerts on, for each
of its alert places (region pins and saved resorts), for today and
tomorrow, it compares the authoritative bulletin's state (A1) against what
the send log last recorded, decides, bundles every place that fired into
one message (A3) and delivers it by push and, as the fallback, email.

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

**Night hold.** Nothing is evaluated 22:00–06:30 Europe/Zurich, so an
issue landing overnight is picked up by the first run of the morning.

**Delivery.** Push goes to every active subscription on the account. Email
goes when ``alert_email`` is ALWAYS, or FALLBACK with no active push, to a
verified address only; never when NEVER. A message with no channel is
logged ``SKIPPED`` so the state still advances.

Rows are written only with ``commit=True``; without it the run reports what
it would send.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.db import transaction

from apps.accounts.models import Account
from apps.accounts.push_service import enqueue_push
from apps.accounts.services.alert_email import send_alert_email
from apps.bulletins.models import Bulletin, BulletinAlert, RegionDayRating
from apps.bulletins.services.alert_wording import (
    AlertKind,
    AlertMessage,
    PlaceAlert,
    alert_message,
)
from apps.bulletins.services.bulletin_change import (
    BulletinChange,
    BulletinState,
    bulletin_state,
    summarise_change,
)
from apps.favourites.models import Favourite

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

ALERT_TZ = ZoneInfo("Europe/Zurich")
NIGHT_STARTS = datetime.time(22, 0)
NIGHT_ENDS = datetime.time(6, 30)

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
    """One region an account follows, with the name it knows it by."""

    region: MicroRegion
    labels: list[str] = field(default_factory=list)

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
    return bulletin_state(bulletin.render_model)


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

    Returns:
        The decision.

    """
    sent, skipped = BulletinAlert.Outcome.SENT, BulletinAlert.Outcome.SKIPPED
    if has_row:
        change = summarise_change(logged, current)
        outcome = skipped if change.is_empty else sent
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
        return Decision(sent, "season_start", summarise_change(None, current), None)
    change = summarise_change(previous_day, current)
    if mode == Account.NotifyWhen.CHANGES and change.is_empty:
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
    for place in account_places(account.user):
        for day in (today, today + datetime.timedelta(days=1)):
            bulletin = authoritative_bulletin(place.region, day)
            if bulletin is None or not bulletin.render_model:
                continue
            current = bulletin_state(bulletin.render_model)
            fingerprint = current.fingerprint()
            last = BulletinAlert.objects.for_day(
                account.pk, place.region.pk, day
            ).first()
            if last is not None and last.fingerprint == fingerprint:
                continue
            enabled_at = account.alerts_enabled_at
            day_began = (
                day_start if day == today else day_start + datetime.timedelta(days=1)
            )
            decision = decide(
                mode=account.notify_when,
                day=day,
                today=today,
                current=current,
                logged=BulletinState.from_json(last.state) if last else None,
                has_row=last is not None,
                previous_day=(
                    None
                    if last is not None
                    else day_state(place.region, day - datetime.timedelta(days=1))
                ),
                enabled_before_day=enabled_at is not None and enabled_at < day_began,
            )
            run.rows.append(PendingRow(place, day, bulletin, current, decision))
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
                )
                for r in sending
            ],
            today,
        )
    return run


def deliver(run: AccountRun) -> AccountRun:
    """
    Send an account's message and write its rows, in one transaction.

    Args:
        run: An evaluated run.

    Returns:
        The run, with ``push_count`` and ``emailed`` filled in.

    """
    account = run.account
    message = run.message
    if message is not None:
        subscriptions = list(account.push_subscriptions.active())
        for sub in subscriptions:
            enqueue_push(sub, message.as_push())
        run.push_count = len(subscriptions)
        email_on = account.alert_email == Account.AlertEmail.ALWAYS or (
            account.alert_email == Account.AlertEmail.FALLBACK and not subscriptions
        )
        if email_on and account.is_verified and account.user.email:
            send_alert_email(account, message.title, message.body, message.url)
            run.emailed = True
    reached = run.push_count > 0 or run.emailed
    with transaction.atomic():
        for row in run.rows:
            outcome = row.decision.outcome
            if row.decision.sends and not reached:
                outcome = BulletinAlert.Outcome.SKIPPED
            sends = row.decision.sends and reached
            # update_or_create, not get_or_create: a state that recurs on
            # the same day rewrites its row, which bumps updated_at and so
            # makes it the latest again (BulletinAlertQuerySet.for_day).
            BulletinAlert.objects.update_or_create(
                account=account,
                region=row.place.region,
                target_date=row.day,
                fingerprint=row.state.fingerprint(),
                defaults={
                    "bulletin": row.bulletin,
                    "state": row.state.to_json(),
                    "kind": _KIND_TO_ROW[row.decision.kind],
                    "outcome": outcome,
                    "sent_push": sends and run.push_count > 0,
                    "sent_email": sends and run.emailed,
                    "title": message.title if sends and message else "",
                    "body": message.body if sends and message else "",
                    "url": message.url if sends and message else "",
                },
            )
    return run


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
