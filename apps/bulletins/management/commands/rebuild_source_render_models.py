"""
apps/bulletins/management/commands/rebuild_source_render_models.py — Service models.

The service's counterpart of ``rebuild_render_models`` (SNOW-1270). The
weather service builds a render model for every bulletin it stores
(``SourceBulletin.render_model``), indexes its target day and region codes,
and computes its day ratings (``SourceDayRating``) from them; it serves
all of it. After a builder change bumps ``RENDER_MODEL_VERSION``, or a
rule change bumps ``DAY_RATING_VERSION``, the stored values are stale: a
bulletin is rebuilt when it is next fetched, but the history is not, so
this command rebuilds it.

**SNOW-1346: the command enqueues, the task runner works** (AGENTS.md
command rule 7). Two walks, each enqueuing one task per row at priority
-50 (``apps.bulletins.services.render_rebuild``):

  - ``rebuild_source_bulletin_task`` per bulletin whose model predates the
    current version, failed builds and never-indexed rows included
    (``--all`` enqueues every row, forced, for a builder fix that did not
    bump the version). Each task rebuilds its bulletin and recomputes the
    day ratings of every (region, day) it governed before or governs now,
    in one transaction.
  - ``refresh_source_day_rating_task`` per rating computed under an older
    ``DAY_RATING_VERSION`` (a rule bump with no render bump), which
    re-checks the row is still stale before recomputing it.

A changed row's ``updated_at`` moves, so the feeds serve it again; a
rebuild that comes out the same writes nothing, and a task re-checks its
row, so a second run or a duplicate enqueue is a no-op; a row that already
has the same task waiting is not enqueued again (SNOW-1397,
``apps.core.command_tasks.enqueue_each``). On a queued
backend (production) ``--commit`` returns in seconds; under
``ImmediateBackend`` (dev, test, staging) each task runs inline and a
failed one counts towards the exit code. Re-run the preview to read how
many rows are still stale.

Run it once per source environment after deploying a version bump.
Read-only unless ``--commit`` is passed.

Usage:
    uv run python manage.py rebuild_source_render_models           # preview
    uv run python manage.py rebuild_source_render_models --commit  # enqueue
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.bulletins.services.render_rebuild import (
    rebuild_source_bulletin_task,
    refresh_source_day_rating_task,
)
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.ingest.public import (
    DAY_RATING_VERSION,
    RENDER_MODEL_VERSION,
    SourceBulletin,
    SourceDayRating,
)

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Enqueue the rebuild of the weather service's render models and ratings."""

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Enqueue one background task per bulletin whose service render model "
        "predates RENDER_MODEL_VERSION (or every bulletin, with --all); each "
        "rebuilds it and the day ratings it touches. Then one task per day "
        "rating computed under an older DAY_RATING_VERSION. Read-only unless "
        "--commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Enqueue the tasks. Without it the command counts them.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="Rebuild every bulletin, not only the stale ones.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue a task per stale bulletin, then per stale day rating.

        Raises:
            CommandError: A task run inline failed.

        """
        commit: bool = options["commit"]
        rebuild_all: bool = options["rebuild_all"]
        verbosity: int = options["verbosity"]
        # Slim rows: only what the countdown label reads, never the CAAML.
        rows = SourceBulletin.objects.only("pk", "bulletin_id", "provider")
        if not rebuild_all:
            rows = rows.stale_render_model(RENDER_MODEL_VERSION)
        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Enqueuing {rows.count()} service render model rebuild(s) at "
                f"version {RENDER_MODEL_VERSION}{label}"
            )
        )

        bulletins = enqueue_each(
            self,
            rows,
            rebuild_source_bulletin_task,
            args=lambda row: (row.pk, rebuild_all, True),
            describe=SourceBulletin.to_string,
            commit=commit,
            verbosity=verbosity,
        )

        # Evaluated after the bulletin walk: under an inline backend those
        # tasks have already brought their pairs current.
        ratings = SourceDayRating.objects.stale(DAY_RATING_VERSION).only(
            "pk", "region_code", "date"
        )
        day_ratings = enqueue_each(
            self,
            ratings,
            refresh_source_day_rating_task,
            args=lambda r: (r.region_code, r.date.isoformat()),
            describe=lambda r: f"{r.region_code} {r.date}",
            commit=commit,
            verbosity=verbosity,
            tally_as="day_ratings_enqueued",
        )
        enqueued = bulletins.enqueued
        ratings_enqueued = day_ratings.enqueued
        failed = bulletins.failed + day_ratings.failed
        if verbosity >= 1:
            verb = "Enqueued" if commit else "Would enqueue"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {enqueued} bulletin(s) and {ratings_enqueued} "
                    f"stale day rating(s); {failed} failed."
                )
            )
        logger.info(
            "rebuild_source_render_models: enqueued=%d day_ratings_enqueued=%d "
            "failed=%d commit=%s",
            enqueued,
            ratings_enqueued,
            failed,
            commit,
        )
        if failed:
            raise CommandError(f"{failed} rebuild task(s) failed")
