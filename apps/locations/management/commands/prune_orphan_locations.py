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
``ResortLocation``, no ``MicroRegion``, no ``Favourite`` and no
``FieldObservation`` is a candidate. A named location is curated data owned
by ``import_locations`` and is never touched, even when nothing points at it
— an unreferenced curated place is a curation question, not garbage.

Deleting a ``Location`` cascades to its ``Weather`` rows, which is the point:
that data describes a place nothing can reach any more.

Usage:
    # Preview — reports what it would delete, writes nothing.
    uv run python manage.py prune_orphan_locations

    # Delete.
    uv run python manage.py prune_orphan_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.locations.models import Location
from apps.weather.models import Weather

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Delete anonymous Locations that nothing references.

    Read-only by default; pass --commit to delete. Named locations are
    never candidates.
    """

    help = (
        "Delete anonymous Locations no ResortLocation, MicroRegion, "
        "Favourite or FieldObservation points at, along with their "
        "Weather. Read-only unless --commit is passed."
    )

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

        Returns:
            A queryset of orphaned ``Location`` rows.

        """
        return Location.objects.anonymous().filter(
            resort_locations__isnull=True,
            micro_regions__isnull=True,
            favourites__isnull=True,
            field_observations__isnull=True,
        )

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

        if commit and location_count:
            with transaction.atomic():
                # Re-filter by pk: the queryset above spans four joins, and
                # a delete() over it would emit a DELETE with those joins on
                # some backends. The pk list is what we counted.
                pks = list(orphans.values_list("pk", flat=True))
                Location.objects.filter(pk__in=pks).delete()

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {location_count} location(s) and {weather_count} "
                    f"weather row(s) "
                    f"{'deleted' if commit else 'would be deleted'}."
                    f"{'' if commit else ' Pass --commit to delete.'}"
                )
            )
        logger.info(
            "prune_orphan_locations finished: locations=%d weather=%d commit=%s",
            location_count,
            weather_count,
            commit,
        )
