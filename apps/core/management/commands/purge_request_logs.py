"""
apps/core/management/commands/purge_request_logs.py.

Management command.

Deletes ``RequestLog`` rows older than the retention window (SNOW-775).

``RequestLog`` is written at sign-up, sign-in, subscribe, add-region and
share-click, and every row carries ``ip_address``, ``city``, ``latitude``,
``longitude``, ``user_agent`` and ``session_key``. Until this command
existed nothing deleted any of it, while the Privacy Policy told readers
technical request data was kept for fourteen days — a promise no code made
true. The retention period is now twelve months, stated on the page and
enforced here.

Twelve months rather than fourteen days because the table is not an access
log. Rows exist to give ``Account.acquisition_request`` and
``Subscription.subscribed_via`` their geo and language context, so a
two-week window would blank that for every account older than a fortnight
and defeat the reason the rows are kept at all. A year keeps a full season
of acquisition history and still means no IP address or coordinate pair
outlives it.

This is a hard delete, matching the erasure decision in SNOW-774: an
account deletion removes its rows outright rather than anonymising them,
and a retention sweep that only blanked columns would leave the two paths
disagreeing about what a spent row looks like.

Rows still referenced by ``Account.acquisition_request`` or
``Subscription.subscribed_via`` are deleted like any other — both FKs are
``SET_NULL``, so the referring row survives with the pointer cleared. That
is the intended outcome: the account keeps its history, the identifiers
behind it expire.

Read-only by default — pass ``--commit`` to persist deletions (per the
project-wide management command convention; see
docs/decisions/dry-run-default-commands.md).

Typical use::

    # Read-only — reports how many rows are past the window.
    python manage.py purge_request_logs

    # Persist.
    python manage.py purge_request_logs --commit

    # A different window, e.g. to check what a stricter policy would remove.
    python manage.py purge_request_logs --days 90
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.command_iteration import iterate_rows
from apps.core.models import RequestLog

logger = logging.getLogger(__name__)

# The retention window, in days. Twelve months — see the module docstring
# for why this is not the fourteen days the Privacy Policy used to claim.
# The Privacy Policy states this period; changing it here without changing
# the page there puts the two back out of step, which is the whole defect
# SNOW-775 fixed.
RETENTION_DAYS: int = 365


class Command(BaseCommand):
    """Delete RequestLog rows past the retention window."""

    help = (
        "Deletes RequestLog rows older than the retention window "
        f"(default {RETENTION_DAYS} days). Read-only without --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Delete the rows past the window. Without this flag the "
                "command only reports what it would delete."
            ),
        )
        parser.add_argument(
            "--days",
            type=int,
            default=RETENTION_DAYS,
            metavar="N",
            help=(
                f"Retention window in days (default {RETENTION_DAYS}). Rows "
                "created strictly before the cutoff are deleted."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Delete the expired rows, or report them when not committing.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: If ``--days`` is not a positive integer, which
                would otherwise select a cutoff in the future and delete
                rows that are still within their retention window.

        """
        commit: bool = options["commit"]
        days: int = options["days"]
        verbosity: int = options["verbosity"]

        if days < 1:
            raise CommandError("--days must be a positive integer")

        cutoff = timezone.now() - timedelta(days=days)
        expired = RequestLog.objects.filter(created_at__lt=cutoff)

        # Count during the walk rather than accumulating ids. The first
        # production run purges everything older than a year in one go, and
        # the obvious shape — collect every pk, then delete WHERE pk IN
        # (...) — holds the whole set in memory to build an IN clause the
        # database then has to parse. Deleting on the same ``created_at``
        # predicate the walk used needs neither: the cutoff is fixed before
        # the walk starts, and a row created during it has ``created_at``
        # of now, so it cannot fall inside the window. The two are
        # equivalent, and only one of them scales.
        expired_count = 0
        for _ in iterate_rows(
            self,
            expired.values_list("pk", flat=True),
            verbosity=verbosity,
            describe=lambda pk: f"RequestLog {pk}",
        ):
            expired_count += 1

        if not expired_count:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"No RequestLog rows older than {days} days "
                        f"(cutoff {cutoff:%Y-%m-%d %H:%M} UTC)."
                    )
                )
            return

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"Would delete {expired_count} RequestLog row(s) older "
                        f"than {days} days (cutoff {cutoff:%Y-%m-%d %H:%M} UTC). "
                        "Re-run with --commit to persist."
                    )
                )
            return

        # ``delete()`` returns (total, {label: count}) where the total counts
        # cascaded rows too. Reporting that total would overstate the purge —
        # a RequestLog with one BulletinShareClick behind it reads as two
        # rows deleted — so the headline number comes from the per-model
        # dict and anything cascaded is reported as what it is.
        _, by_model = RequestLog.objects.filter(created_at__lt=cutoff).delete()
        deleted = by_model.get(RequestLog._meta.label, 0)  # noqa: SLF001
        cascaded = sum(
            count
            for label, count in by_model.items()
            if label != RequestLog._meta.label  # noqa: SLF001
        )

        logger.info(
            "purge_request_logs: deleted %d row(s) older than %d days "
            "(%d cascaded row(s))",
            deleted,
            days,
            cascaded,
        )
        if verbosity >= 1:
            suffix = f", plus {cascaded} cascaded row(s)" if cascaded else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {deleted} RequestLog row(s) older than "
                    f"{days} days{suffix}."
                )
            )
