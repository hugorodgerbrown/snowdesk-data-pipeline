"""
apps/core/management/commands/prune_command_runs.py — Management command.

Keeps the command run register (``CommandRun``, SNOW-1189) honest and small.
Two jobs, both read-only without ``--commit``:

1. **Close what was killed.** A run whose process died without unwinding —
   a deploy restarting the worker, ``SIGKILL``, the OOM killer — never
   reaches the code that closes its row, so the row reads ``RUNNING`` for
   ever. A ``RUNNING`` row older than ``--stale-hours`` (default 6) is
   marked ``INTERRUPTED``. A run that genuinely outlasts the window and then
   finishes overwrites the mark with its real outcome, so a wrong guess
   corrects itself.
2. **Forget old scheduled runs.** The scheduler starts ``fetch_bulletins``
   48 times a day; a year of those rows is noise next to the handful of
   backfills an operator runs. Rows of ``SCHEDULER`` runs older than
   ``--days`` (default 90) are deleted. Every other run — shell, build,
   ``call_command`` — is kept for good, because "did this ever run here?" is
   exactly the question the register exists to answer.

Scheduled daily by ``schedule.py``. Typical use::

    # Read-only — reports what would be closed and deleted.
    python manage.py prune_command_runs

    # Persist.
    python manage.py prune_command_runs --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import timedelta
from typing import Any

from django.core.management.base import CommandError
from django.utils import timezone

from apps.core.command_iteration import positive_int
from apps.core.command_register import RegisteredCommand
from apps.core.models import CommandRun

logger = logging.getLogger(__name__)

# How long a run may read RUNNING before it is presumed killed. The longest
# routine backfill takes well under an hour; six leaves room for a slow one.
STALE_HOURS: int = 6

# How long a scheduled run's row is kept.
SCHEDULED_RETENTION_DAYS: int = 90


class Command(RegisteredCommand):
    """Close stale RUNNING rows and delete old scheduled runs."""

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Marks CommandRun rows left RUNNING longer than --stale-hours as "
        f"INTERRUPTED (default {STALE_HOURS}h) and deletes SCHEDULER runs older "
        f"than --days (default {SCHEDULED_RETENTION_DAYS}). Read-only without --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the changes. Without this flag nothing is written.",
        )
        parser.add_argument(
            "--stale-hours",
            type=positive_int,
            default=STALE_HOURS,
            metavar="N",
            help=f"Hours a run may read RUNNING (default {STALE_HOURS}).",
        )
        parser.add_argument(
            "--days",
            type=positive_int,
            default=SCHEDULED_RETENTION_DAYS,
            metavar="N",
            help=(
                "Days a scheduled run's row is kept "
                f"(default {SCHEDULED_RETENTION_DAYS})."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Close the stale rows and delete the expired ones.

        Exempt from row-by-row streaming (CLAUDE.md management-command rule
        5): both jobs are one UPDATE or DELETE over a filter, with no
        per-row work to show a countdown for.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: If ``--stale-hours`` or ``--days`` is not positive
                when passed through ``call_command``, which skips argparse.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        stale_hours: int = options["stale_hours"]
        days: int = options["days"]
        if stale_hours < 1 or days < 1:
            raise CommandError("--stale-hours and --days must be positive integers")

        stale = CommandRun.objects.stale(older_than=timedelta(hours=stale_hours))
        # A run never sweeps its own row, however long it has been open.
        if self.current_run_id is not None:
            stale = stale.exclude(pk=self.current_run_id)
        expired = CommandRun.objects.filter(
            invoked_via=CommandRun.INVOKED_VIA.SCHEDULER,
            started_at__lt=timezone.now() - timedelta(days=days),
        )

        if commit:
            # The sweep time stands in for the unknown moment the process
            # died, so the row reads as closed and has a duration.
            now = timezone.now()
            interrupted = stale.update(
                status=CommandRun.STATUS.INTERRUPTED, finished_at=now, updated_at=now
            )
            deleted, _ = expired.delete()
        else:
            interrupted = stale.count()
            deleted = expired.count()
        self.tally("interrupted", interrupted)
        self.tally("deleted", deleted)

        if verbosity >= 1:
            verb = ("Marked", "Deleted") if commit else ("Would mark", "Would delete")
            self.stdout.write(
                f"{verb[0]} {interrupted} run(s) RUNNING for over {stale_hours}h "
                f"as INTERRUPTED; {verb[1].lower()} {deleted} scheduled run(s) "
                f"older than {days} days."
            )
            if not commit and (interrupted or deleted):
                self.stdout.write(
                    self.style.WARNING("Read-only run — pass --commit to persist.")
                )
        logger.info(
            "prune_command_runs: interrupted=%d deleted=%d commit=%s",
            interrupted,
            deleted,
            commit,
        )
