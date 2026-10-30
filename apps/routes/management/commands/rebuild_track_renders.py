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

Three kinds of row, in this order, each newest first:

- **routes** and **trips**: every stale row is rebuilt from its own
  points and terrain record, sampled or not.
- **tours**: only a tour holding a terrain record for its line is
  rebuilt. One never walked has no render by design — the tours layer
  draws SAC's own line — and is counted ``tours_unwalked``.

Usage:
    # Preview — how many of each would be rebuilt; writes nothing.
    uv run python manage.py rebuild_track_renders

    # Rebuild and store.
    uv run python manage.py rebuild_track_renders --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.routes.models import Route, SkiTour
from apps.routes.services.ski_tours import rebuild_tour_render
from apps.routes.services.track_render import stale_q, store_terrain
from apps.trips.models import Trip

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Store a current ``track_render`` on every row missing one.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    row failed, so a partial run is detectable.
    """

    help = (
        "Rebuild the stored track_render of every Route, Trip and walked "
        "SkiTour whose record is missing or stale. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Rebuild and store. Without it, only count the candidates.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Rebuild every stale render.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any row failed, so cron/CI sees non-zero.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        counts = {
            "routes": 0,
            "trips": 0,
            "tours": 0,
            "tours_unwalked": 0,
            "failed": 0,
        }
        flag_label = "" if commit else " [READ-ONLY]"

        routes = Route.objects.filter(stale_q())
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding {routes.count()} route render(s){flag_label}"
            )
        )
        for route in iterate_rows(
            self,
            routes,
            verbosity=verbosity,
            describe=lambda row: f"route {row.name or row.uuid}",
        ):
            self._rebuild(route, "routes", counts, commit=commit)

        trips = Trip.objects.filter(stale_q())
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding {trips.count()} trip render(s){flag_label}"
            )
        )
        for trip in iterate_rows(
            self,
            trips,
            verbosity=verbosity,
            describe=lambda row: f"trip {row.name or row.route_name or row.uuid}",
        ):
            self._rebuild(trip, "trips", counts, commit=commit)

        tours = SkiTour.objects.filter(slope_samples__isnull=False).filter(stale_q())
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding {tours.count()} walked tour render(s){flag_label}"
            )
        )
        for tour in iterate_rows(
            self,
            tours,
            verbosity=verbosity,
            describe=lambda row: f"tour {row.sac_id} {row.name}",
        ):
            self._rebuild(tour, "tours", counts, commit=commit)

        for name, n in counts.items():
            self.tally(name, n)
        self.stdout.write(
            self.style.SUCCESS(
                ("Rebuilt" if commit else "Would rebuild")
                + f": {counts['routes']} route(s), {counts['trips']} trip(s), "
                f"{counts['tours']} tour(s); {counts['tours_unwalked']} tour(s) "
                f"hold no record for their line; {counts['failed']} failed."
            )
        )
        logger.info("rebuild_track_renders finished: commit=%s %s", commit, counts)
        if counts["failed"]:
            raise CommandError(
                f"rebuild_track_renders completed with {counts['failed']} "
                "failure(s). Check logs for details."
            )

    def _rebuild(
        self,
        row: Route | Trip | SkiTour,
        kind: str,
        counts: dict[str, int],
        *,
        commit: bool,
    ) -> None:
        """Rebuild one row's render and count it.

        Args:
            row: The row.
            kind: Its count's name.
            counts: The running tally, mutated in place.
            commit: Whether to store.

        """
        if not commit:
            counts[kind] += 1
            return
        try:
            if isinstance(row, SkiTour):
                if not rebuild_tour_render(row):
                    counts["tours_unwalked"] += 1
                    return
            else:
                store_terrain(row, row.slope_samples)
        except Exception:  # noqa: BLE001 — broad catch intentional: one row must not abort the batch
            logger.exception("rebuild_track_renders: failed on %s pk=%s", kind, row.pk)
            counts["failed"] += 1
            return
        counts[kind] += 1
