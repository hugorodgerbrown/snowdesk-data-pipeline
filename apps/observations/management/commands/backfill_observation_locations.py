"""backfill_observation_locations — mint a Location per existing observation.

One-shot backfill for SNOW-709, and the sibling of
``backfill_favourite_locations``. Every ``FieldObservation`` submitted
before that ticket stores its report coordinate on the row; this mints the
``Location`` it happened at and points the FK there.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations, so schema migrations carry DDL only and the backfill is a
``--commit``-gated command.

**Field observations are user data, and immutable event records.** Every
report must survive with its coordinates, timestamp and provenance intact.
The provenance model in particular does not move and is not touched:
``gps_latitude``/``gps_longitude`` (the raw device fix), ``location_source``
and ``accuracy_radius_km`` are data about the *report*, not about the place.
The ``save(update_fields=["location", "updated_at"])`` below cannot write
another column even by accident.

**No external calls, and no elevation.** The minted location carries no
``elevation_m``: an observation shows no forecast panel, and resolving one
would mean an Open-Meteo round trip per historical report for a figure
nothing renders.

Locations minted here carry **no name and no kind** — naming is a curation
act.

Idempotent: the candidate queryset excludes observations that already have
a location, so a second run selects zero rows.

Usage:
    # Preview — reports what would be minted, writes nothing.
    uv run python manage.py backfill_observation_locations

    # Apply it.
    uv run python manage.py backfill_observation_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import iterate_rows
from apps.locations.models import Location
from apps.observations.models import FieldObservation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Mint a Location per FieldObservation that has none, and point the FK.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    observation failed, so a partial run is detectable.
    """

    help = (
        "Mint a Location for every FieldObservation submitted before "
        "SNOW-709 and point its FK there. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted Locations and the FKs. Without this "
                "flag the command reports and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mint a location for every candidate observation."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        # Streamed, not materialised: FieldObservation is a growable user
        # table that only ever grows — reports are immutable event records.
        candidates = FieldObservation.objects.filter(location__isnull=True)
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Minting a Location for {total} observation(s){flag_label}"
            )
        )
        logger.info(
            "backfill_observation_locations started: candidates=%d commit=%s",
            total,
            commit,
        )

        counts = {"minted": 0, "failed": 0}
        for observation in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.uuid}",
        ):
            self._backfill_one(observation, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_observation_locations completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _backfill_one(
        self,
        observation: FieldObservation,
        counts: dict[str, int],
        *,
        commit: bool,
    ) -> None:
        """Mint one observation's Location and point its FK at it.

        Args:
            observation: The observation to back-fill.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        if not commit:
            counts["minted"] += 1
            return
        try:
            # One transaction per report, not one for the batch: a report
            # that fails must not roll back the reports already migrated,
            # and a single transaction over a growable user table is the
            # lock this command exists to avoid.
            with transaction.atomic():
                location = Location.objects.create(
                    latitude=observation.latitude,
                    longitude=observation.longitude,
                )
                observation.location = location
                observation.save(update_fields=["location", "updated_at"])
        except Exception:  # noqa: BLE001 — broad catch intentional: one report must not abort the batch
            logger.exception(
                "backfill_observation_locations: failed on observation id=%s uuid=%s",
                observation.pk,
                observation.uuid,
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
            "backfill_observation_locations finished: minted=%d failed=%d commit=%s",
            counts["minted"],
            counts["failed"],
            commit,
        )
