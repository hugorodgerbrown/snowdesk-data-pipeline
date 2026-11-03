"""prune_orphan_locations — delete anonymous Locations nothing points at.

An anonymous ``Location`` exists because something referenced it: a region
centroid, a favourite, a field observation. When that reference goes, the
row survives as an orphan — invisible, unreachable, and carrying every
``Weather`` row ever written for it.

**Where the orphans came from** (SNOW-771). Before the reuse fix, each
deploy's re-link minted a *fresh* centroid ``Location`` rather than rebinding
to the existing one, so every deploy stranded 461 rows and their weather.
Staging accumulated three generations in a day. The reuse fix stops new ones
appearing and rebinds to the oldest surviving row; this command clears what
the earlier behaviour left behind.

**It is deliberately narrow.** Only an *anonymous* location with no
``ResortLocation``, no ``MicroRegion``, no resort pin (``Resort.location``),
no ``Favourite``, no ``FieldObservation`` and no ``Trip`` meeting point is a
candidate. A named location is curated data owned by ``import_locations``
and is never touched, even when nothing points at it — an unreferenced
curated place is a curation question, not garbage.

Deleting a ``Location`` cascades to its ``Weather`` rows, which is the point:
that data describes a place nothing can reach any more.

Since SNOW-1400 ``--commit`` only enqueues: one
``prune_orphan_location_task`` per orphan, through ``enqueue_each``. Each
task re-asks ``orphaned()`` for its row, so a location something points at
again by the time it runs is kept, and an interrupted run leaves the rest
for the next.

Usage:
    # Preview — reports what it would delete, writes nothing.
    uv run python manage.py prune_orphan_locations

    # Enqueue the deletions.
    uv run python manage.py prune_orphan_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.locations.models import Location
from apps.locations.services.orphans import prune_orphan_location_task
from apps.weather.models import Weather

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Delete anonymous Locations that nothing references.

    Read-only by default; pass --commit to delete. Named locations are
    never candidates.
    """

    help = (
        "Delete anonymous Locations no ResortLocation, MicroRegion, "
        "resort pin, Favourite, FieldObservation or Trip meeting point "
        "points at, along with their Weather. Read-only unless --commit "
        "is passed."
    )

    # It only enqueues, so it finishes in seconds (SNOW-1400).
    mcp_runnable = True

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Delete the orphaned rows. Without this flag the command "
                "reports what it would delete and writes nothing."
            ),
        )

    def _orphans(self) -> Any:
        """Return the anonymous locations nothing references.

        The rule itself is ``LocationQuerySet.orphaned()``, shared with the
        ops MCP's estate report (SNOW-1191).

        Returns:
            A queryset of orphaned ``Location`` rows.

        """
        return Location.objects.orphaned()

    def handle(self, *args: Any, **options: Any) -> None:
        """Report, and optionally delete, the orphaned locations."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        orphans = self._orphans()
        location_count = orphans.count()
        weather_count = Weather.objects.filter(location__in=orphans).count()

        if verbosity >= 1:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"{location_count} orphaned location(s), "
                    f"{weather_count} weather row(s)"
                    f"{'' if commit else ' [READ-ONLY]'}"
                )
            )

        # Slim: the task re-asks orphaned() for its pk under a lock.
        counts = enqueue_each(
            self,
            orphans.only("pk", "name", "latitude", "longitude", "elevation_m", "kind"),
            prune_orphan_location_task,
            args=lambda row: (row.pk,),
            describe=Location.to_string,
            commit=commit,
            verbosity=verbosity,
            tally_as="enqueued",
        )

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts.enqueued} location(s) "
                    f"{'enqueued for deletion' if commit else 'would be enqueued'}, "
                    f"{counts.already_queued} already queued."
                    f"{'' if commit else ' Pass --commit to enqueue.'}"
                )
            )
        # Counts for the command run register (SNOW-1190); enqueue_each
        # tallied what it enqueued.
        self.tally("locations", location_count)
        self.tally("weather", weather_count)
        logger.info(
            "prune_orphan_locations finished: locations=%d weather=%d "
            "enqueued=%d failed=%d commit=%s",
            location_count,
            weather_count,
            counts.enqueued,
            counts.failed,
            commit,
        )
        if counts.failed:
            raise CommandError(
                f"prune_orphan_locations completed with {counts.failed} "
                "failure(s). Check logs for details."
            )
