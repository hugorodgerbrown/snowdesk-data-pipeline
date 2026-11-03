r"""
apps/bulletins/management/commands/recompute_day_ratings.py — Management command.

Re-derives this instance's ``RegionDayRating`` rows under the current
day-rating policy (``DAY_RATING_VERSION``): the instance counterpart of
``rebuild_source_render_models``' stale-rating refresh on the service's
``SourceDayRating``.

A bulletin rebuild recomputes the ratings it governs, but a day-rating rule
change that bumps ``DAY_RATING_VERSION`` without a render-model bump reaches
no bulletin, so the rows it left stale are this command's. By default it
selects the rows computed under an older version; ``--all`` selects every
row, for a rule change made without a bump. ``--start-date`` /
``--end-date`` narrow either.

Since SNOW-1400 ``--commit`` only enqueues: one ``recompute_day_rating_task``
per row through ``enqueue_each``, at ``BULK_TASK_PRIORITY``. Each task
re-checks its row (still stale, unless ``--all``), takes the same sorted
rating lock as a bulletin rebuild, and recomputes in one transaction. An
interrupted run leaves the rest stale for the next. Pure computation: no
pace.

Read-only by default — pass ``--commit`` to enqueue.

Typical use::

    # Read-only: count the stale rows.
    python manage.py recompute_day_ratings

    # Enqueue the stale rows' recompute.
    python manage.py recompute_day_ratings --commit

    # Every row in a date window, stale or not.
    python manage.py recompute_day_ratings --all \\
        --start-date 2026-01-01 --end-date 2026-04-30 --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import CommandError

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.render_rebuild import recompute_day_rating_task
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.ingest.public import DAY_RATING_VERSION

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Enqueue a recompute of every stale ``RegionDayRating``."""

    help = (
        "Enqueue a recompute of every RegionDayRating computed under an older "
        "DAY_RATING_VERSION (--all: every row). Read-only unless --commit. "
        "Use --start-date / --end-date to restrict to a date window."
    )

    # It only enqueues, so it finishes in seconds (SNOW-1400).
    mcp_runnable = True

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Enqueue one task per row. Without this flag the command "
                "counts the rows and writes nothing."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Recompute every row, not only those computed under an older "
                "DAY_RATING_VERSION."
            ),
        )
        parser.add_argument(
            "--start-date",
            metavar="YYYY-MM-DD",
            type=date.fromisoformat,
            help="Only rows on or after this date (inclusive).",
        )
        parser.add_argument(
            "--end-date",
            metavar="YYYY-MM-DD",
            type=date.fromisoformat,
            help="Only rows on or before this date (inclusive).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue the selected rows' recompute.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any task run inline failed, so cron/CI sees
                non-zero.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        force: bool = options["all"]
        start_date: date | None = options["start_date"]
        end_date: date | None = options["end_date"]

        # Slim: the task re-reads its row and its region.
        rows = RegionDayRating.objects.select_related("region").only(
            "pk", "date", "region__region_id"
        )
        if not force:
            rows = rows.exclude(version=DAY_RATING_VERSION)
        if start_date:
            rows = rows.filter(date__gte=start_date)
        if end_date:
            rows = rows.filter(date__lte=end_date)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Recomputing {rows.count()} "
                + ("" if force else "stale ")
                + "day rating(s)"
                + (f" from {start_date}" if start_date else "")
                + (f" to {end_date}" if end_date else "")
                + ("" if commit else " [READ-ONLY]")
            )
        )
        counts = enqueue_each(
            self,
            rows,
            recompute_day_rating_task,
            args=lambda row: (row.pk, force),
            describe=lambda row: f"{row.region.region_id} {row.date}",
            commit=commit,
            verbosity=verbosity,
        )
        verb = "enqueued" if commit else "would be enqueued"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {counts.enqueued} day rating(s) {verb}, "
                f"{counts.already_queued} already queued."
                + ("" if commit else " Pass --commit to enqueue.")
            )
        )
        logger.info(
            "recompute_day_ratings finished: enqueued=%d already_queued=%d "
            "failed=%d all=%s commit=%s",
            counts.enqueued,
            counts.already_queued,
            counts.failed,
            force,
            commit,
        )
        if counts.failed:
            raise CommandError(
                f"recompute_day_ratings completed with {counts.failed} "
                "failure(s). Check logs for details."
            )
