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
nothing (tallied ``held``), so an overnight issue is announced at the first
run of the morning.

**The gate** (SNOW-1491). Each run looks back to the start of the last
committed run that evaluated (``last_evaluated_at``) and evaluates only the
accounts something may have changed for (``accounts_to_evaluate``): a
changed bulletin in one of their places, an alert to retry, a place saved
or changed, alerts turned on. With none it is skipped (tallied ``gated``).
With no such run yet, or ``--full``, every alerting account is evaluated.

Read-only by default: it reports what each account would be sent. Pass
``--commit`` to send and write the send log.

Typical use::

    python manage.py send_bulletin_alerts            # what would be sent now
    python manage.py send_bulletin_alerts --commit   # send it
    python manage.py send_bulletin_alerts --ignore-night-hold
    python manage.py send_bulletin_alerts --commit --full   # everyone

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

from apps.accounts.models import Account, AccountQuerySet
from apps.bulletins.services.alerts import (
    accounts_to_evaluate,
    deliver,
    evaluate_account,
    in_night_hold,
    last_evaluated_at,
    local_now,
)
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
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Evaluate every alerting account, not only those something "
                "changed for since the last run."
            ),
        )

    def _tally_zeros(self) -> None:
        """Tally every count of a run that evaluated nothing as zero."""
        for name in ("accounts", "messages", "pushes", "emails", "failed"):
            self.tally(name, 0)

    def _hold(self, verbosity: int) -> None:
        """Record a run inside the night hold: nothing evaluated, all zeros.

        Args:
            verbosity: The command's verbosity.

        """
        if verbosity:
            self.stdout.write("Night hold (22:00–06:30 Europe/Zurich): nothing sent.")
        self.tally("held", 1)
        self.tally("gated", 0)
        self._tally_zeros()

    def _accounts(
        self, now: datetime.datetime, full: bool, verbosity: int
    ) -> AccountQuerySet | None:
        """Return the accounts this run evaluates, or None when it is skipped.

        Args:
            now: The run's clock.
            full: Evaluate every alerting account (``--full``).
            verbosity: The command's verbosity.

        Returns:
            Every alerting account with ``full`` or no watermark yet; else
            those ``accounts_to_evaluate`` names, or None when it names none.

        """
        queryset = Account.objects.alerting().select_related("user")
        since = None if full else last_evaluated_at()
        if since is None:
            return queryset
        wanted = accounts_to_evaluate(since, now)
        if wanted:
            return queryset.filter(pk__in=wanted)
        if verbosity:
            when = local_now(since).strftime("%Y-%m-%d %H:%M")
            self.stdout.write(f"Nothing changed since {when}: skipped.")
        self.tally("held", 0)
        self.tally("gated", 1)
        self._tally_zeros()
        return None

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

        queryset = self._accounts(now, options["full"], verbosity)
        if queryset is None:
            return

        accounts = messages = pushes = emails = failed = 0
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

        self.tally("held", 0)
        self.tally("gated", 0)
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
