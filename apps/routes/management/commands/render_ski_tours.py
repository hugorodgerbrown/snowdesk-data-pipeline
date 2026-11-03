"""render_ski_tours — enqueue one render task per SAC tour the service has not drawn.

SNOW-1316, replacing SNOW-1288's ``backfill_ski_tour_slope_samples``. A
tour's walk (the terrain under its line) and its drawing (its legs, slope
and point-card rows) are deterministic from SAC's line and the terrain
model, so the reference-data service makes them once and every instance
copies them from the v2 ``ski-tours`` feed, rather than each walking 2,941
tours itself. Runs where the service runs: production.

**SNOW-1345: the command enqueues, the task runner works.** Walking every
tour in one process took over an hour and copied nothing onto the map
until it ended, so a dropped shell lost the lot. ``--commit`` now enqueues
one ``render_tour_task`` per candidate and returns in seconds; each task
walks, draws and applies its own tour
(``apps.routes.services.ski_tour_render``), at low priority on the
``default`` queue. Under ``ImmediateBackend`` (dev, test, staging) the
tasks run inline, one after another, as the old loop did.

**Candidates** are ``tours_to_render()``: a ``SourceSkiTour`` never walked
from its current document and terrain model (a fresh import, or one the
weekly sync rewrote, which clears its walk), or one whose drawing a
builder change has made stale.

**Idempotent.** A tour whose fingerprints match is not a candidate, and a
task re-checks before it walks, so a second run, or a tour enqueued twice,
writes nothing.

**It paces the tile origin** (SNOW-1397, ``apps.core.command_tasks``) —
task *i* is enqueued to run ``i × --delay`` seconds after the first (a
second by default), and each walk waits for its slot (``wait_for_slot``),
so two walks never start closer than ``--delay`` even when overdue tasks
become due together; inline, the command sleeps between tasks. A tour the origin
did not answer opens a five-minute breaker, and the tasks after it leave
their tours for the next run. A tour that already has a task waiting is
not enqueued again.

Usage:
    # Preview — how many tours would be enqueued; no request, no write.
    uv run python manage.py render_ski_tours

    # Enqueue every candidate.
    uv run python manage.py render_ski_tours --commit

    # A first batch, to watch it.
    uv run python manage.py render_ski_tours --commit --limit 20
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.db.models.fields.json import KeyTextTransform

from apps.core.command_iteration import non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.ingest.public import terrain_grid, tours_to_render
from apps.routes.services.ski_tour_render import render_tour_task

logger = logging.getLogger(__name__)

# Seconds between one task's start and the next: politeness towards the
# tile origin.
_DEFAULT_DELAY_S = 1.0


class Command(RegisteredCommand):
    """Enqueue a render task for every SAC tour the service has not drawn.

    Read-only by default; pass --commit to enqueue. Exits non-zero when the
    terrain grid cannot be loaded, or when a task run inline failed.
    """

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Enqueue one background task per SAC ski tour the reference-data "
        "service has not walked and drawn; each task renders its tour and "
        "applies it to this instance. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Enqueue the tasks. Without this flag the command reports "
                "the candidates, makes no request and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds between one task's start and the next. Defaults "
                f"to {_DEFAULT_DELAY_S}; 0 disables the pacing."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Enqueue at most this many tours. Defaults to 0, meaning "
                "every candidate."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue a render task per candidate tour.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: No terrain grid, or a task run inline failed.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]

        grid = terrain_grid()
        if grid is None:
            self.tally("enqueued", 0)
            self.tally("already_queued", 0)
            self.tally("failed", 0)
            raise CommandError(
                "render_ski_tours: the terrain grid could not be loaded; "
                "nothing was enqueued."
            )

        candidates = tours_to_render(grid)
        total = candidates.count()
        shown = min(total, limit) if limit else total
        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Enqueuing {shown} of {total} SAC tour(s){flag_label}"
            )
        )
        # Slim rows: the id and the two fields the countdown names a tour by,
        # never the document itself.
        rows = candidates.annotate(
            sac_id=KeyTextTransform("sac_id", "document"),
            tour_name=KeyTextTransform("name", "document"),
        ).values_list("id", "sac_id", "tour_name")
        counts = enqueue_each(
            self,
            rows,
            render_tour_task,
            args=lambda row: (row[0], delay),
            describe=_describe,
            commit=commit,
            verbosity=verbosity,
            limit=limit,
            pace=delay,
        )
        enqueued, failed = counts.enqueued, counts.failed

        verb = "Enqueued" if commit else "Would enqueue"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {enqueued}; {counts.already_queued} already queued; "
                f"{failed} failed."
            )
        )
        logger.info(
            "render_ski_tours finished: commit=%s enqueued=%s failed=%s",
            commit,
            enqueued,
            failed,
        )
        if failed:
            raise CommandError(
                f"render_ski_tours completed with {failed} failure(s). "
                "Check logs for details."
            )


def _describe(row: tuple[int, str | None, str | None]) -> str:
    """Return a tour's label for the countdown: its SAC id and name."""
    _pk, sac_id, name = row
    return f"SAC {sac_id} {name or ''}"
