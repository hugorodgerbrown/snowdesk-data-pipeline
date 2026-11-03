"""backfill_trip_slope_samples — give existing trips the ground they cross.

Backfill for SNOW-962, and for every later ticket that adds a key to the
record — SNOW-1043's ``heights`` is the one it currently selects on.
(SNOW-911's ``cruxes`` was once a second; SNOW-1066 removed cruxes, so a
record without that key is no longer a candidate.)

Every ``Trip`` created before SNOW-962 has a null ``slope_samples``, which
means NEVER SAMPLED and draws the trip page's line and height profile
flat. Nothing else would ever fill them in:
``enqueue_trip_slope_sampling`` runs at creation only, and rendering an old
trip schedules nothing. Without this command the whole existing estate
stays permanently uncoloured, which is how the gap was found — by review
on the pull request that added the column.

**IT COPIES BEFORE IT WALKS, and that is most of the point.** A trip's
snapshot was taken from a route whose geometry never changes (``Route``
has create and delete and no geometry edit), so where the source route is
still present, already sampled, and its ``points`` are identical to the
snapshot's, its record answers this trip exactly — and copying costs
nothing where a walk costs an HTTP request per terrain tile. The walk is
the fallback for a trip whose route is gone, unsampled, or no longer
matches.

The equality check is not ceremony. ``Trip.route`` is provenance and may
be null, and a trip may outlive the route it came from; copying a record
belonging to a different track would paint one track's steepness onto
another's, which is the failure this whole feature exists to prevent.

**Not a data migration.** AGENTS.md forbids bulk dataset updates in
migrations, and the fallback path makes outbound requests besides.
Migration ``0003`` adds the column and nothing else.

**Two kinds of candidate.** A null is a trip nothing has sampled. A
record missing a key is one written before the ticket that added it: no
``heights`` (SNOW-1043) draws its profile and figures off the recording
device's drifting altimeter — and nothing else would ever add it. A
record carrying the key is current even when the list inside holds
nulls, because those are answers.

**Null stays honest.** A trip that comes back with nothing learnable is
left null rather than written as a record of nothing, so a later run
picks it up again. Idempotent: a current row is not a candidate.

**It only enqueues (SNOW-1398).** One ``backfill_trip_slopes_task`` per
candidate, through ``apps.core.command_tasks.enqueue_each``; the copy and
the walk both happen in the task (``apps/trips/services/slope.py``), which
re-reads its row and returns when it is no longer stale. ``--delay``
paces the tasks, and a walking task also waits for it on the tile origin's
shared slot; a copy waits for no slot.

Usage:
    # Preview — reports how many trips would be filled, writing nothing
    # and asking the origin nothing.
    uv run python manage.py backfill_trip_slope_samples

    # Enqueue one task per trip.
    uv run python manage.py backfill_trip_slope_samples --commit

    # A first batch, to watch it before committing to the whole table.
    uv run python manage.py backfill_trip_slope_samples --commit --limit 20
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.routes.services.slope_segments import STALE_TERRAIN
from apps.trips.models import Trip
from apps.trips.services.slope import backfill_trip_slopes_task

logger = logging.getLogger(__name__)

# Seconds between tasks. Matches the routes backfill's own default and
# carries its reasoning: politeness towards the tile origin rather than a
# limit it imposes.
_DEFAULT_DELAY_S = 1.0


class Command(RegisteredCommand):
    """Enqueue a terrain fill-in for every Trip whose terrain is stale.

    Read-only by default; pass --commit to enqueue. Exits non-zero if any
    task run inline failed, so a partial run is detectable.
    """

    help = (
        "Enqueue a terrain fill-in (copied from the source route where it "
        "matches, walked otherwise) for every Trip with no current "
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
                "Enqueue one task per trip. Without this flag the command "
                "reports the candidates, makes no request and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds between one trip's task and the next. Defaults to "
                f"{_DEFAULT_DELAY_S}; 0 disables the pause."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Enqueue at most this many trips. Defaults to 0, meaning "
                "every candidate — a first small batch is the reason this "
                "exists."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue every candidate trip.

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

        # Slim: the task re-reads its row and its route.
        candidates = Trip.objects.filter(STALE_TERRAIN).only("pk", "uuid")
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Filling in the terrain under {total} trip(s){flag_label}"
            )
        )
        counts = enqueue_each(
            self,
            candidates,
            backfill_trip_slopes_task,
            args=lambda row: (row.pk, delay),
            describe=lambda row: row.uuid,
            commit=commit,
            verbosity=verbosity,
            limit=limit,
            pace=delay,
        )
        logger.info(
            "backfill_trip_slope_samples finished: enqueued=%d "
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
                    f"Done. {counts.enqueued} trip(s) {verb}, "
                    f"{counts.already_queued} already queued."
                )
            )
        if counts.failed:
            raise CommandError(
                f"backfill_trip_slope_samples completed with "
                f"{counts.failed} failure(s). Check logs for details."
            )
