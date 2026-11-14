"""
apps/bulletins/management/commands/resend_bulletin_alert.py.

Management command (SNOW-1493).

Resends one account's most recent bulletin alert, so an operator can test
delivery or make up for a message that never arrived. The title and body are
written afresh by ``alert_wording.cold_copy`` from the message's bulletins, so
a resend reads as a new alert does today, not in whatever wording was stored
when it first went (SNOW-1496); the stored ``url`` is kept. The send log is
never written, so the hourly ``send_bulletin_alerts`` run behaves as if the
resend never happened.

A resend is pushed as the message first was (SNOW-1495): the most urgent
``push_urgency`` and the latest ``push_expires_at`` stored on its rows, the
TTL running to that expiry, and the topic of the latest day it is for. Once
that expiry has passed the bulletin has ended and the resend is refused,
nothing sent. Rows from before SNOW-1495 store neither: they go at
``normal``, to the latest ``valid_to`` of their bulletins. Trips are not
re-read.

By default the channels follow the account's own settings, the same rule the
delivery worker uses: push to every active device, then email when
``alert_email`` is ALWAYS, or FALLBACK with no push trusted: accepted for a
device that confirmed its last alert (SNOW-1494). A resend push is counted
as unconfirmed as an alert's is, and each device's line says "accepted",
"accepted (unconfirmed device)" or "accepted, device retired". ``--channel``
names the channels outright and overrides ``alert_email``; an unverified or
empty address is still never emailed.

Read-only by default. ``--commit`` sends, and needs ``--email``.

Rule 1 exemption (docs/management-commands.md): with no ``--email`` the
command does nothing but say how to use it. Any default listing would show
who was sent what, and the output lands in the command register, readable
in the admin and over MCP (Hugo, 2026-11-12: no personal data by default).

Countdown exemption: a resend reads one account's rows, not a growable
queryset.

Typical use::

    python manage.py resend_bulletin_alert                        # usage only
    python manage.py resend_bulletin_alert --email a@example.com  # what would go
    python manage.py resend_bulletin_alert --email a@example.com --commit
    python manage.py resend_bulletin_alert --email a@b.info --commit --channel email

Exit codes: ``0`` on success; non-zero (``CommandError``) for an unknown
email, an account with no delivered alert, ``--commit`` without
``--email``, a commit of a message whose bulletin has ended, or a commit
that reached no channel.
"""

from __future__ import annotations

import datetime
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Account
from apps.accounts.push_service import dispatch_push
from apps.accounts.services.alert_email import send_alert_email
from apps.bulletins.models import BulletinAlert
from apps.bulletins.services.alert_wording import Release, cold_copy
from apps.bulletins.services.push_delivery import (
    FALLBACK_HOLD,
    Delivery,
    alert_topic,
    highest_urgency,
)
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)

CHANNELS: dict[str, tuple[bool, bool]] = {
    "push": (True, False),
    "email": (False, True),
    "both": (True, True),
}


def _delivered() -> Any:
    """Return delivered rows carrying a message, newest message first."""
    return BulletinAlert.objects.sent().exclude(title="").order_by("-updated_at", "-id")


def _message_rows(last: BulletinAlert) -> list[BulletinAlert]:
    """Return every row of the message ``last`` belongs to.

    The message's rows are the account's rows settled together with
    ``last``: the delivery worker stamps every row of one send with one
    ``updated_at``. Matching on the copy as well is not enough on its own,
    because two sends for the same day can carry the same title, body and
    url (``release_line`` drops the time when a bundle's issue times differ).

    Args:
        last: The newest delivered row of the message.

    Returns:
        The rows, with ``bulletin`` loaded.

    """
    return list(
        _delivered()
        .filter(
            account_id=last.account_id,
            title=last.title,
            body=last.body,
            url=last.url,
            updated_at=last.updated_at,
        )
        .select_related("bulletin")
    )


def _copy(rows: list[BulletinAlert]) -> tuple[str, str]:
    """Write today's title and body for a message.

    Args:
        rows: The message's rows (``_message_rows``).

    Returns:
        ``(title, body)`` from ``cold_copy``.

    """
    releases = [
        Release(source=r.bulletin.source, issued_at=r.bulletin.issued_at)
        for r in rows
        if r.bulletin is not None
    ]
    return cold_copy(releases)


def _delivery(rows: list[BulletinAlert], now: datetime.datetime) -> Delivery:
    """Return the push delivery a message first went with (SNOW-1495).

    The most urgent stored urgency and the latest stored expiry across the
    rows. A row from before SNOW-1495 stores neither: it counts as
    ``normal``, and the expiry is the latest ``valid_to`` of the rows'
    bulletins (twelve hours from now when none is left to read).

    Args:
        rows: The message's rows, at least one.
        now: The time of the resend.

    Returns:
        The delivery.

    """
    stored = [r.push_expires_at for r in rows if r.push_expires_at is not None]
    ends = stored or [r.bulletin.valid_to for r in rows if r.bulletin is not None]
    return Delivery(
        urgency=highest_urgency(r.push_urgency for r in rows if r.push_urgency),
        expires_at=max(ends) if ends else now + FALLBACK_HOLD,
        topic=alert_topic(
            (r.target_date for r in rows),
            season=all(r.kind == BulletinAlert.Kind.SEASON_START for r in rows),
        ),
    )


def _can_email(account: Account) -> bool:
    """Return True when the account has a verified address to email."""
    return account.is_verified and bool(account.user.email)


def _push_word(result: dict[str, Any]) -> str:
    """Say how one device took a resend push (SNOW-1494).

    Args:
        result: ``dispatch_push``'s outcome for the device.

    Returns:
        "failed", "accepted", "accepted (unconfirmed device)" or
        "accepted, device retired".

    """
    if not result.get("ok"):
        return "failed"
    if result.get("retired"):
        return "accepted, device retired"
    if not result.get("trusted"):
        return "accepted (unconfirmed device)"
    return "accepted"


class Command(RegisteredCommand):
    """Resend one account's most recent bulletin alert."""

    help = (
        "Resend an account's most recent bulletin alert, exactly as it went "
        "(SNOW-1493). Read-only without --commit; never writes the send log."
    )
    mcp_runnable = True

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register ``--email``, ``--channel`` and ``--commit``.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--email",
            help="The account to resend to. Without it, nothing is done.",
        )
        parser.add_argument(
            "--channel",
            choices=list(CHANNELS),
            help=(
                "Send by exactly these channels, overriding the account's "
                "email setting. Default: follow the account's settings."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Send. Without it, only report what would go.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Show, and with ``--commit`` resend, one account's last alert.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command options.

        Raises:
            CommandError: See the module docstring's exit codes.

        """
        email = options.get("email")
        commit = bool(options["commit"])
        verbosity = int(options["verbosity"])
        if not email:
            if commit:
                raise CommandError("--commit needs --email: say whose alert to resend.")
            if verbosity >= 1:
                self.stdout.write(
                    "Nothing to do: pass --email to show that account's last "
                    "alert, and --commit to resend it."
                )
            return
        account = Account.objects.by_email(email).select_related("user").first()
        if account is None:
            raise CommandError(f"No account with email {email!r}.")
        last = _delivered().filter(account=account).first()
        if last is None:
            raise CommandError(
                f"Account {account.pk} has no delivered alert to resend."
            )
        self._resend(account, last, options.get("channel"), commit, verbosity)

    def _resend(
        self,
        account: Account,
        last: BulletinAlert,
        channel: str | None,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Report, and with ``commit`` send, the account's last alert.

        Args:
            account: The recipient, with ``user`` loaded.
            last: The newest delivered row of the message.
            channel: A ``CHANNELS`` key, or None to follow the account.
            commit: Send when True.
            verbosity: The run's ``--verbosity``.

        Raises:
            CommandError: A commit of a message whose bulletin has ended, or
                a commit that reached no channel.

        """
        devices = list(account.push_subscriptions.active())
        use_push, use_email = CHANNELS[channel] if channel else (True, None)
        rows = _message_rows(last)
        title, body = _copy(rows)
        now = timezone.now()
        delivery = _delivery(rows, now)
        if verbosity >= 1:
            self._report(account, last, (title, body), channel, commit, len(devices))
            self._report_delivery(delivery, now)
        ended = delivery.expired(now)
        self.tally("refused_ended", int(ended))
        if ended:
            if commit:
                raise CommandError(
                    f"Not resent to account {account.pk}: its bulletin ended "
                    f"{delivery.expires_at.isoformat(timespec='minutes')}."
                )
            return
        if not commit:
            if use_email is None:
                use_email = account.alert_email == Account.AlertEmail.ALWAYS or (
                    account.alert_email == Account.AlertEmail.FALLBACK and not devices
                )
            self.tally("would_push", len(devices) if use_push else 0)
            self.tally("would_email", int(use_email and _can_email(account)))
            return
        payload = {"title": title, "body": body, "url": last.url}
        pushed, trusted, push_failed = (
            self._push(devices, payload, delivery, verbosity) if use_push else (0, 0, 0)
        )
        if use_email is None:
            use_email = account.alert_email == Account.AlertEmail.ALWAYS or (
                account.alert_email == Account.AlertEmail.FALLBACK and not trusted
            )
        emailed, email_failed = (
            self._email(account, payload, channel is not None, verbosity)
            if use_email
            else (0, 0)
        )
        self.tally("pushed", pushed)
        self.tally("push_failed", push_failed)
        self.tally("emailed", emailed)
        self.tally("email_failed", email_failed)
        if not (pushed or emailed):
            raise CommandError(
                f"Resend to account {account.pk} reached no channel "
                f"(push failed {push_failed}, email failed {email_failed})."
            )

    def _report(
        self,
        account: Account,
        last: BulletinAlert,
        copy: tuple[str, str],
        channel: str | None,
        commit: bool,
        devices: int,
    ) -> None:
        """Print the message and the channels a resend uses.

        Args:
            account: The recipient.
            last: The newest delivered row of the message.
            copy: The ``(title, body)`` a resend sends.
            channel: A ``CHANNELS`` key, or None to follow the account.
            commit: Whether this run sends.
            devices: The account's active push devices.

        """
        mode = "" if commit else "[READ-ONLY] "
        sent = last.updated_at.isoformat(timespec="minutes")
        email = (
            "possible"
            if _can_email(account)
            else "not possible (unverified or no address)"
        )
        self.stdout.write(f"{mode}Last alert to account {account.pk}, sent {sent}")
        self.stdout.write(f"  {copy[0]}")
        self.stdout.write(f"  {copy[1]}")
        self.stdout.write(
            f"  went by push={last.sent_push} email={last.sent_email}; "
            f"{devices} active device(s); email {email}"
        )
        self.stdout.write(f"  channels: {self._plan(account, channel)}")

    def _report_delivery(self, delivery: Delivery, now: datetime.datetime) -> None:
        """Print how the push would go, or that the bulletin has ended.

        Args:
            delivery: The message's push delivery.
            now: The time of the resend.

        """
        ends = delivery.expires_at.isoformat(timespec="minutes")
        if delivery.expired(now):
            self.stdout.write(f"  bulletin ended {ends}: a resend is refused")
            return
        self.stdout.write(
            f"  push: urgency {delivery.urgency}, TTL {delivery.ttl(now)}s "
            f"(bulletin ends {ends}), topic {delivery.topic}"
        )

    def _push(
        self,
        devices: list[Any],
        payload: dict[str, str],
        delivery: Delivery,
        verbosity: int,
    ) -> tuple[int, int, int]:
        """Push ``payload`` to each device; return (accepted, trusted, failed).

        Each push is counted as unconfirmed until the device's receipt
        comes, as an alert's is (SNOW-1494), so a resend to a dead device
        can retire it.

        Args:
            devices: The account's active push subscriptions.
            payload: The ``{title, body, url}`` to send.
            delivery: The urgency, TTL and topic it goes with.
            verbosity: The run's ``--verbosity``.

        Returns:
            How many devices accepted the push, how many of those had
            confirmed their last alert, and how many did not accept it.

        """
        accepted = trusted = 0
        for sub in devices:
            try:
                result = dispatch_push(
                    sub, payload, delivery=delivery, count_unconfirmed=True
                )
            except Exception:
                logger.exception("Resend push to subscription %s raised", sub.pk)
                result = {"ok": False, "status": "raised"}
            ok = bool(result.get("ok"))
            accepted += ok
            trusted += bool(result.get("trusted"))
            if verbosity >= 1:
                self.stdout.write(
                    f"  push to device {sub.pk}: {_push_word(result)} "
                    f"({result.get('status')})"
                )
        return accepted, trusted, len(devices) - accepted

    def _email(
        self, account: Account, payload: dict[str, str], force: bool, verbosity: int
    ) -> tuple[int, int]:
        """Email the message; return (sent, failed) as 0 or 1 each.

        Args:
            account: The recipient, with ``user`` loaded.
            payload: The ``{title, body, url}`` to send.
            force: Override the account's ``alert_email`` (``--channel``).
            verbosity: The run's ``--verbosity``.

        Returns:
            ``(1, 0)`` when the mail backend took it, otherwise ``(0, 1)``.

        """
        try:
            ok = send_alert_email(
                account, payload["title"], payload["body"], payload["url"], force=force
            )
        except Exception:
            logger.exception("Resend email to account %s raised", account.pk)
            ok = False
        if verbosity >= 1:
            self.stdout.write(f"  email: {'sent' if ok else 'not sent'}")
        return (1, 0) if ok else (0, 1)

    def _plan(self, account: Account, channel: str | None) -> str:
        """Describe which channels a resend would use, and why.

        Args:
            account: The recipient.
            channel: A ``CHANNELS`` key, or None to follow the account.

        Returns:
            One readable line.

        """
        if channel:
            return f"{channel} (--channel overrides the account's settings)"
        setting = Account.AlertEmail(account.alert_email)
        if setting == Account.AlertEmail.ALWAYS:
            return "push and email (account setting: always email)"
        if setting == Account.AlertEmail.FALLBACK:
            return (
                "push; email if no confirmed device accepts it "
                "(account setting: fallback)"
            )
        return "push only (account setting: never email)"
