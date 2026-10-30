"""
apps/bulletins/management/commands/send_bulletin_alerts.py.

Management command (SNOW-942).

Tells every account with bulletin alerts on what moved in the bulletins for
its places, today and tomorrow, in one message per account. The decision,
the bundling and the delivery are ``apps.bulletins.services.alerts``; this
command walks the accounts and writes what it did.

Runs on the ingest cadence, after ``fetch_bulletins`` (scheduled at minute
10 of every hour, behind the ``schedule_send_bulletin_alerts`` switch), and
sends only when the authoritative bulletin's state differs from the one the
send log last recorded. Between 22:00 and 06:30 Europe/Zurich it evaluates
nothing, so an overnight issue is announced at the first run of the
morning.

Read-only by default: it reports what each account would be sent. Pass
``--commit`` to send and write the send log.

Typical use::

    python manage.py send_bulletin_alerts            # what would be sent now
    python manage.py send_bulletin_alerts --commit   # send it
    python manage.py send_bulletin_alerts --ignore-night-hold

Exit codes:

- ``0`` — every account was evaluated (and, with ``--commit``, delivered).
- Non-zero — at least one account failed (``CommandError``); the others are
  still attempted, and a failed account's state is not logged, so the next
  run tries it again.
"""

from __future__ import annotations

import datetime
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Account
from apps.bulletins.services.alerts import deliver, evaluate_account, in_night_hold
from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Send bulletin alerts for every account with alerts on."""

    help = (
        "Tell every account with bulletin alerts on what moved in its places' "
        "bulletins, one message per account (SNOW-942). Read-only unless "
        "--commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Send the alerts and write the send log.",
        )
        parser.add_argument(
            "--ignore-night-hold",
            action="store_true",
            help="Evaluate even between 22:00 and 06:30 Europe/Zurich.",
        )

    def _hold(self, verbosity: int) -> None:
        """Record a run inside the night hold: nothing evaluated, all zeros.

        Args:
            verbosity: The command's verbosity.

        """
        if verbosity:
            self.stdout.write("Night hold (22:00–06:30 Europe/Zurich): nothing sent.")
        for name in ("accounts", "messages", "pushes", "emails", "failed"):
            self.tally(name, 0)

    def _alert_one(
        self, account: Account, now: datetime.datetime, commit: bool, verbosity: int
    ) -> tuple[int, int, int]:
        """Evaluate one account and, with ``commit``, deliver its message.

        Args:
            account: The account to alert.
            now: The run's clock.
            commit: Deliver and log when True.
            verbosity: The command's verbosity.

        Returns:
            ``(messages, pushes, emails)`` for this account.

        """
        run = evaluate_account(account, now)
        if run.message is not None and verbosity:
            self.stdout.write(f"    {run.message.title}")
            self.stdout.write(f"    {run.message.body}")
        if not commit:
            return int(run.message is not None), 0, 0
        deliver(run)
        return int(run.message is not None), run.push_count, int(run.emailed)

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: At least one account failed.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        now = timezone.now()

        if in_night_hold(now) and not options["ignore_night_hold"]:
            self._hold(verbosity)
            return

        if verbosity:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    "Bulletin alerts" + ("" if commit else " [READ-ONLY]")
                )
            )

        accounts = messages = pushes = emails = failed = 0
        queryset = Account.objects.alerting().select_related("user")
        for account in iterate_rows(
            self,
            queryset,
            verbosity=verbosity,
            describe=lambda a: f"{a.notify_when} {a.user.email}",
        ):
            accounts += 1
            try:
                sent, pushed, emailed = self._alert_one(account, now, commit, verbosity)
            except Exception:
                logger.exception("Bulletin alerts failed for account %s", account.pk)
                failed += 1
                continue
            messages += sent
            pushes += pushed
            emails += emailed

        self.tally("accounts", accounts)
        self.tally("messages", messages)
        self.tally("pushes", pushes)
        self.tally("emails", emails)
        self.tally("failed", failed)
        if verbosity:
            verb = "Sent" if commit else "Would send"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {messages} message(s) to {accounts} account(s);"
                    f" {failed} failed."
                )
            )
        if failed:
            raise CommandError(f"{failed} account(s) failed. Check logs for details.")
