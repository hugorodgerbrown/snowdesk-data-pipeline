"""
apps/routes/management/commands/backfill_crossed_regions.py — Fill crossed_regions.

Every Route and Trip created since SNOW-945 stores the ordered regions its
track crosses (``crossed_regions``). Rows from before it hold null, which
every reader answers by resolving live — correct, but a geometry walk per
page view. This fills them, routes first, then trips, newest first.

Read-only by default: it resolves each row and reports what it would
write. ``--commit`` writes. Each row is resolved from its own points; a
row already filled is not selected, so a second run selects nothing.

Usage::

    python manage.py backfill_crossed_regions            # report only
    python manage.py backfill_crossed_regions --commit   # write

Tallies ``routes``, ``trips``, ``outside_coverage`` and ``failed``, zeros
included, and exits non-zero when any row failed. Runs as a Deploy-Step.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.db.models import Model, QuerySet

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.regions.services.track_regions import resolve_track_regions
from apps.routes.models import Route
from apps.trips.models import Trip

logger = logging.getLogger(__name__)


def describe_route(route: Route) -> str:
    """Return a route's countdown label from its loaded fields.

    Args:
        route: A route loaded with ``name`` and ``uuid``.

    Returns:
        ``"route {name or uuid}"``.

    """
    return f"route {route.name or route.uuid}"


def describe_trip(trip: Trip) -> str:
    """Return a trip's countdown label from its loaded fields.

    Args:
        trip: A trip loaded with ``name``, ``route_name`` and ``uuid``.

    Returns:
        ``"trip {name or route_name or uuid}"``.

    """
    return f"trip {trip.name or trip.route_name or trip.uuid}"


class Command(RegisteredCommand):
    """Fill Route.crossed_regions and Trip.crossed_regions where null."""

    help = (
        "Resolve the regions each route's and trip's track crosses and store "
        "them in crossed_regions where it is still null. Read-only unless "
        "--commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the resolved lists. Without it the run only reports.",
        )

    def _fill(
        self,
        queryset: QuerySet[Any],
        *,
        describe: Any,
        commit: bool,
        verbosity: int,
    ) -> tuple[int, int, int]:
        """Resolve and (with ``commit``) store each row's crossed regions.

        Args:
            queryset: The rows still null, narrowed with ``.only()``.
            describe: The row's countdown label.
            commit: Whether to write.
            verbosity: Django's ``--verbosity``.

        Returns:
            ``(filled, outside_coverage, failed)``; ``filled`` counts rows
            written, or that would be written in a read-only run.

        """
        filled = outside = failed = 0
        for row in iterate_rows(self, queryset, verbosity=verbosity, describe=describe):
            try:
                regions = resolve_track_regions(row.points)
                if commit:
                    self._save(row, regions)
            except Exception:
                logger.exception("backfill_crossed_regions: %s failed", describe(row))
                failed += 1
                continue
            filled += 1
            if not regions:
                outside += 1
            if verbosity >= 2:
                self.stdout.write(f"  → {regions or 'outside coverage'}")
        return filled, outside, failed

    @staticmethod
    def _save(row: Model, regions: list[str]) -> None:
        """Write one row's list, touching nothing else.

        A queryset update rather than ``save()``, so ``updated_at`` — which
        the trips list reads as "last edited" — is not moved by a backfill.

        Args:
            row: The route or trip.
            regions: Its resolved region ids.

        """
        type(row)._default_manager.filter(
            pk=row.pk, crossed_regions__isnull=True
        ).update(crossed_regions=regions)

    def handle(self, *args: Any, **options: Any) -> None:
        """Fill routes, then trips, and report.

        Raises:
            CommandError: When any row failed.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        routes, route_outside, route_failed = self._fill(
            Route.objects.filter(crossed_regions__isnull=True).only(
                "pk", "uuid", "name", "points"
            ),
            describe=describe_route,
            commit=commit,
            verbosity=verbosity,
        )
        trips, trip_outside, trip_failed = self._fill(
            Trip.objects.filter(crossed_regions__isnull=True).only(
                "pk", "uuid", "name", "route_name", "points"
            ),
            describe=describe_trip,
            commit=commit,
            verbosity=verbosity,
        )
        failed = route_failed + trip_failed

        self.tally("routes", routes)
        self.tally("trips", trips)
        self.tally("outside_coverage", route_outside + trip_outside)
        self.tally("failed", failed)

        verb = "Filled" if commit else "Would fill"
        self.stdout.write(
            f"{verb} {routes} route(s) and {trips} trip(s); "
            f"{route_outside + trip_outside} outside coverage, {failed} failed."
        )
        if not commit:
            self.stdout.write("Read-only run. Pass --commit to write.")
        if failed:
            raise CommandError(f"{failed} row(s) failed. Check the logs.")
