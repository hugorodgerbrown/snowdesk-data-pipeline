"""backfill_favourite_locations — mint a Location for every existing Favourite.

One-shot backfill for SNOW-704. Every ``Favourite`` created before that
ticket stores its own ``latitude``/``longitude``/``elevation`` and points
straight at a ``ForecastCell``; this mints the ``Location`` each one *is*
and repoints the FK, so weather is reached through
``location.forecast_cell`` from then on.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations — they lock the table and break Render deploys — so schema
migrations carry DDL only and the backfill is a ``--commit``-gated command.

**Favourites are user data.** Every pin must survive with its coordinates,
elevation, name and region intact, so nothing here writes to the favourite
beyond the new FK: the ``save(update_fields=["location", "updated_at"])``
below cannot touch another column even by accident.

**No external calls.** Unlike ``link_location_forecast_cells``, this needs
no Open-Meteo round trip: the favourite already carries the elevation and
the resolved cell from when it was created. Copying them is both correct
and free, and re-resolving would risk a *different* answer for a pin whose
weather users have already been reading.

The minted location carries **no name and no kind** — naming is a curation
act, and a pin's name is the user's own text, which stays on the favourite
and must never reach a shared, curatable table.

Idempotent: the candidate queryset excludes favourites that already have a
location, so a second run selects zero rows.

Usage:
    # Preview — reports what would be minted, writes nothing.
    uv run python manage.py backfill_favourite_locations

    # Apply it.
    uv run python manage.py backfill_favourite_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import iterate_rows
from apps.favourites.models import Favourite
from apps.locations.models import Location

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Mint a Location per Favourite that has none, and repoint the FK.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    favourite failed, so a partial run is detectable.
    """

    help = (
        "Mint a Location for every Favourite created before SNOW-704 and "
        "repoint its FK, so the pin reaches its weather through "
        "location.forecast_cell. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted Locations and the repointed FKs. "
                "Without this flag the command reports and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mint a location for every candidate favourite."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        # Streamed, not materialised: Favourite is a growable user table.
        candidates = Favourite.objects.filter(location__isnull=True).select_related(
            "forecast_point"
        )
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Minting a Location for {total} favourite(s){flag_label}"
            )
        )
        logger.info(
            "backfill_favourite_locations started: candidates=%d commit=%s",
            total,
            commit,
        )

        counts = {"minted": 0, "failed": 0}
        for favourite in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.uuid}",
        ):
            self._backfill_one(favourite, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_favourite_locations completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _backfill_one(
        self, favourite: Favourite, counts: dict[str, int], *, commit: bool
    ) -> None:
        """Mint one favourite's Location and repoint its FK.

        Args:
            favourite: The favourite to back-fill.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        if not commit:
            counts["minted"] += 1
            return
        try:
            # One transaction per favourite, not one for the batch: a pin
            # that fails must not roll back the pins already migrated, and
            # a single transaction over a growable user table is the lock
            # this command exists to avoid.
            with transaction.atomic():
                location = Location.objects.create(
                    latitude=favourite.latitude,
                    longitude=favourite.longitude,
                    elevation_m=favourite.elevation,
                    forecast_cell=favourite.forecast_point,
                )
                favourite.location = location
                favourite.save(update_fields=["location", "updated_at"])
        except Exception:  # noqa: BLE001 — broad catch intentional: one pin must not abort the batch
            logger.exception(
                "backfill_favourite_locations: failed on favourite id=%s uuid=%s",
                favourite.pk,
                favourite.uuid,
            )
            counts["failed"] += 1
        else:
            counts["minted"] += 1

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['minted']} location(s) minted, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['minted']} "
                        "location(s) would be minted. No data written. "
                        "Pass --commit to persist."
                    )
                )
        logger.info(
            "backfill_favourite_locations finished: minted=%d failed=%d commit=%s",
            counts["minted"],
            counts["failed"],
            commit,
        )
