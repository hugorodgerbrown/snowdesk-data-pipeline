"""
apps/routes/management/commands/rebuild_track_renders.py — rebuild stale renders.

SNOW-1309. Every Route, Trip and SkiTour holds a ``track_render``: the
drawing the map, the panel and the trip page read, built by
``apps.routes.services.track_render`` whenever the terrain is written. A
row whose record is missing (it predates SNOW-1309) or was built by an
older builder (its ``v`` is below ``TRACK_RENDER_VERSION``) is drawn
correctly anyway — readers build it in memory and log it — but every read
pays for that until this command has stored a current one.

Runs after every deploy that changes the builder, as a ``Deploy-Step``
trailer on the commit that changed it. Pure computation: no request to
any origin, so no pacing.

Since SNOW-1400 it only enqueues: one task per stale row, through
``enqueue_each``, at ``BULK_TASK_PRIORITY``. Each task re-reads its row
and rebuilds it in one transaction, so an interrupted run leaves the
rest stale and the next run picks them up. Three kinds of row, in this
order, each newest first:

- **routes** and **trips**: every stale row is rebuilt from its own
  points and terrain record, sampled or not
  (``rebuild_route_render_task``, ``rebuild_trip_render_task``).
- **tours**: only a tour holding a terrain record for its line is a
  candidate (``rebuild_tour_render_task``). One never walked has no
  render by design — the tours layer draws SAC's own line.

Usage:
    # Preview — how many of each would be enqueued; writes nothing.
    uv run python manage.py rebuild_track_renders

    # Enqueue the rebuilds.
    uv run python manage.py rebuild_track_renders --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.routes.models import Route, SkiTour
from apps.routes.services.render_tasks import (
    rebuild_route_render_task,
    rebuild_tour_render_task,
)
from apps.routes.services.track_render import stale_q
from apps.trips.models import Trip
from apps.trips.services.render_tasks import rebuild_trip_render_task

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Enqueue a render rebuild for every row whose ``track_render`` is stale.

    Read-only by default; pass --commit to enqueue. Exits non-zero if any
    task run inline failed, so a partial run is detectable.
    """

    help = (
        "Enqueue a rebuild of the stored track_render of every Route, Trip "
        "and walked SkiTour whose record is missing or stale. Read-only "
        "unless --commit."
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
            help="Enqueue one task per stale row. Without it, only count them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue every stale render's rebuild.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any task run inline failed, so cron/CI sees
                non-zero.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        flag_label = "" if commit else " [READ-ONLY]"
        # Slim: each task re-reads its row under a lock.
        kinds = (
            (
                "routes",
                Route.objects.filter(stale_q()).only("pk", "uuid", "name"),
                rebuild_route_render_task,
                lambda row: f"route {row.name or row.uuid}",
            ),
            (
                "trips",
                Trip.objects.filter(stale_q()).only("pk", "uuid", "name"),
                rebuild_trip_render_task,
                lambda row: f"trip {row.name or row.uuid}",
            ),
            (
                "tours",
                SkiTour.objects.filter(slope_samples__isnull=False)
                .filter(stale_q())
                .only("pk", "sac_id", "name"),
                rebuild_tour_render_task,
                lambda row: f"tour {row.sac_id} {row.name}",
            ),
        )
        enqueued: dict[str, int] = {}
        failed = 0
        already_queued = 0
        for kind, rows, task, describe in kinds:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"Rebuilding {rows.count()} {kind[:-1]} render(s){flag_label}"
                )
            )
            counts = enqueue_each(
                self,
                rows,
                task,
                args=lambda row: (row.pk,),
                describe=describe,
                commit=commit,
                verbosity=verbosity,
                tally_as=kind,
            )
            enqueued[kind] = counts.enqueued
            already_queued += counts.already_queued
            failed += counts.failed

        verb = "Enqueued" if commit else "Would enqueue"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: {enqueued['routes']} route(s), {enqueued['trips']} "
                f"trip(s), {enqueued['tours']} tour(s); {already_queued} "
                f"already queued; {failed} failed."
            )
        )
        logger.info(
            "rebuild_track_renders finished: commit=%s enqueued=%s "
            "already_queued=%d failed=%d",
            commit,
            enqueued,
            already_queued,
            failed,
        )
        if failed:
            raise CommandError(
                f"rebuild_track_renders completed with {failed} failure(s). "
                "Check logs for details."
            )
