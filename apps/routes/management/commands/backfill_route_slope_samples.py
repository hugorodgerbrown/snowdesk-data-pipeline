"""backfill_route_slope_samples — sample the terrain under existing routes.

Backfill for SNOW-910, and for every later ticket that adds a key to the
record — SNOW-1043's ``heights`` is the one it currently selects on.
(SNOW-911's ``cruxes`` was once a second; SNOW-1066 removed cruxes, so a
record without that key is no longer a candidate.)

Every ``Route`` uploaded before SNOW-910 has a null ``slope_samples``,
which means NEVER SAMPLED and draws as a flat line; this walks each of
them and asks the terrain grid how steep the ground under it is.

**Not a data migration.** AGENTS.md forbids bulk dataset updates in
migrations, and this one could not be a migration even if it did not: the
work is one HTTP request per terrain tile a track crosses, against an
origin outside this process, and a deploy's ``migrate`` step must not sit
on a table making network calls. Migration ``0005`` adds the column and
nothing else.

**Two kinds of candidate.** The queryset selects rows whose
``slope_samples`` is null — never sampled — and rows whose record is
missing a key a later ticket added: no ``heights`` is one sampled before
SNOW-1043 and so drawing its profile, legs and totals off the recording
device's drifting altimeter. A record carrying the key is current even
when the list inside holds nulls, because those are answers ("no ground
there").

**Null stays honest.** A row that comes back with nothing
learnable — the origin unreachable for the whole of it — is left null
rather than written as a record of nothing, so a later run picks it up
again; that rule is ``build_slope_samples``', not this command's, and is
argued in its module docstring.

**It only enqueues (SNOW-1398).** One ``backfill_route_slopes_task`` per
candidate, through ``apps.core.command_tasks.enqueue_each``, so a run
returns in seconds and is offered to the ops MCP server's ``run_command``.
Each task re-reads its row and returns when it is no longer stale, so a
duplicate enqueue is a no-op; a row whose task is still waiting is not
enqueued again; and a run that stops early leaves the rest as candidates.

**The tasks ask the tile origin, so they are paced.** ``--delay`` (a second
by default) staggers their start times in production, sleeps between them
where tasks run inline, and is the gap each task waits for on the shared
``TILE_ORIGIN`` slot before it walks — politeness towards the origin rather
than a rate limit it imposes.

Usage:
    # Preview — reports how many routes would be sampled, writes nothing
    # and asks the origin nothing.
    uv run python manage.py backfill_route_slope_samples

    # Enqueue one task per route.
    uv run python manage.py backfill_route_slope_samples --commit

    # A first batch, to watch it before committing to the whole table.
    uv run python manage.py backfill_route_slope_samples --commit --limit 20
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.routes.models import Route
from apps.routes.services.slope_segments import (
    STALE_TERRAIN,
    backfill_route_slopes_task,
)

logger = logging.getLogger(__name__)

# Seconds between routes. Not imposed by the origin — the tiles are served
# ``immutable, max-age=31536000`` off a CDN and a route mostly re-reads
# tiles the process cache already holds — but a backfill over the whole
# table is the one caller that could look like a scrape.
_DEFAULT_DELAY_S = 1.0


class Command(RegisteredCommand):
    """Enqueue a terrain walk for every Route whose terrain is stale.

    Read-only by default; pass --commit to enqueue. Exits non-zero if any
    task run inline failed, so a partial run is detectable.
    """

    help = (
        "Enqueue a terrain walk for every Route with no current "
        "slope_samples. Read-only unless --commit."
    )

    # It only enqueues, so it finishes in seconds (SNOW-1398).
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
                "Enqueue one task per route. Without this flag the command "
                "reports the candidates, makes no request and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds between one route's walk and the next. Defaults to "
                f"{_DEFAULT_DELAY_S}; 0 disables the pause."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Enqueue at most this many routes. Defaults to 0, meaning "
                "every candidate — a first small batch is the reason this "
                "exists."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue every candidate route.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any task run inline failed, so cron/CI sees
                non-zero.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]

        # Slim: the task re-reads its row, so the walk needs only the pk
        # and the countdown the uuid.
        candidates = Route.objects.filter(STALE_TERRAIN).only("pk", "uuid")
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Sampling the terrain under {total} route(s){flag_label}"
            )
        )
        counts = enqueue_each(
            self,
            candidates,
            backfill_route_slopes_task,
            args=lambda row: (row.pk, delay),
            describe=lambda row: row.uuid,
            commit=commit,
            verbosity=verbosity,
            limit=limit,
            pace=delay,
        )
        logger.info(
            "backfill_route_slope_samples finished: enqueued=%d "
            "already_queued=%d failed=%d commit=%s",
            counts.enqueued,
            counts.already_queued,
            counts.failed,
            commit,
        )
        if verbosity >= 1:
            verb = "enqueued" if commit else "would be enqueued"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts.enqueued} route(s) {verb}, "
                    f"{counts.already_queued} already queued."
                )
            )
        if counts.failed:
            raise CommandError(
                f"backfill_route_slope_samples completed with "
                f"{counts.failed} failure(s). Check logs for details."
            )
