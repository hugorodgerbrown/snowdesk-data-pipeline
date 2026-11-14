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

By default the channels follow the account's own settings, the same rule the
delivery worker uses: push to every active device, then email when
``alert_email`` is ALWAYS, or FALLBACK with no push accepted. ``--channel``
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
``--email``, or a commit that reached no channel.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.accounts.models import Account
from apps.accounts.push_service import (
    ALERT_TTL_SECONDS,
    ALERT_URGENCY,
    dispatch_push,
)
from apps.accounts.services.alert_email import send_alert_email
from apps.bulletins.models import BulletinAlert
from apps.bulletins.services.alert_wording import Release, cold_copy
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


def _copy(last: BulletinAlert) -> tuple[str, str]:
    """Write today's title and body for the message ``last`` belongs to.

    The message's rows are the account's rows stored with the same title,
    body and url as ``last``: one per place in a bundle.

    Args:
        last: The newest delivered row of the message.

    Returns:
        ``(title, body)`` from ``cold_copy``.

    """
    rows = (
        _delivered()
        .filter(
            account_id=last.account_id, title=last.title, body=last.body, url=last.url
        )
        .select_related("bulletin")
    )
    releases = [
        Release(source=r.bulletin.source, issued_at=r.bulletin.issued_at)
        for r in rows
        if r.bulletin is not None
    ]
    return cold_copy(releases)


def _can_email(account: Account) -> bool:
    """Return True when the account has a verified address to email."""
    return account.is_verified and bool(account.user.email)


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
            CommandError: A commit reached no channel.

        """
        devices = list(account.push_subscriptions.active())
        use_push, use_email = CHANNELS[channel] if channel else (True, None)
        title, body = _copy(last)
        if verbosity >= 1:
            self._report(account, last, (title, body), channel, commit, len(devices))
        if not commit:
            if use_email is None:
                use_email = account.alert_email == Account.AlertEmail.ALWAYS or (
                    account.alert_email == Account.AlertEmail.FALLBACK and not devices
                )
            self.tally("would_push", len(devices) if use_push else 0)
            self.tally("would_email", int(use_email and _can_email(account)))
            return
        payload = {"title": title, "body": body, "url": last.url}
        pushed, push_failed = (
            self._push(devices, payload, verbosity) if use_push else (0, 0)
        )
        if use_email is None:
            use_email = account.alert_email == Account.AlertEmail.ALWAYS or (
                account.alert_email == Account.AlertEmail.FALLBACK and not pushed
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

    def _push(
        self, devices: list[Any], payload: dict[str, str], verbosity: int
    ) -> tuple[int, int]:
        """Push ``payload`` to each device; return (accepted, failed).

        Args:
            devices: The account's active push subscriptions.
            payload: The ``{title, body, url}`` to send.
            verbosity: The run's ``--verbosity``.

        Returns:
            How many devices accepted the push, and how many did not.

        """
        accepted = 0
        for sub in devices:
            try:
                result = dispatch_push(
                    sub, payload, ttl=ALERT_TTL_SECONDS, urgency=ALERT_URGENCY
                )
            except Exception:
                logger.exception("Resend push to subscription %s raised", sub.pk)
                result = {"ok": False, "status": "raised"}
            ok = bool(result.get("ok"))
            accepted += ok
            if verbosity >= 1:
                word = "accepted" if ok else "failed"
                self.stdout.write(
                    f"  push to device {sub.pk}: {word} ({result.get('status')})"
                )
        return accepted, len(devices) - accepted

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
            return "push; email if no push is accepted (account setting: fallback)"
        return "push only (account setting: never email)"
