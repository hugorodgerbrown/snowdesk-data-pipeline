"""fill_location_elevations — resolve ``Location.elevation_m`` from the coordinate.

``elevation_m`` is deliberately not a sheet column: ``docs/locations.md`` makes
it always derived, never supplied, so ``import_locations`` writes every other
field and leaves this one null. Something has to run the derivation, and until
SNOW-732 nothing did — ``Location.objects.unresolved()`` was written for this
pass and named in two comments as "how a missing one gets filled in later",
with no command behind it, so every imported location sat at null forever.

Sibling of ``fill_what3words``, which walks ``unaddressed()`` the same way for
the same reason.

**The elevation is a check on the coordinate, not just a value.** A curated
peak's ``note`` records the height the resort sheet claims for it; the height
Open-Meteo resolves at the pinned coordinate is an independent second opinion.
A location whose resolved height comes back nowhere near its note has been
mis-pinned, and this command is where that shows up — at import time, against
a list, rather than on a resort page months later. ``--report`` prints those
disagreements and writes nothing.

Re-runnable: a location that already carries an elevation is skipped, so an
interrupted run costs only the rows it had not reached. ``--force`` re-resolves
every row, which is what a re-pinned coordinate needs — moving a location
leaves a stale elevation behind and nothing else would notice.

Usage:
    # Preview — reports what it would resolve, writes nothing.
    uv run python manage.py fill_location_elevations

    # Resolve the missing ones.
    uv run python manage.py fill_location_elevations --commit

    # Re-resolve everything after coordinates moved.
    uv run python manage.py fill_location_elevations --commit --force

    # Check curated pins against the heights their notes claim.
    uv run python manage.py fill_location_elevations --report
"""

from __future__ import annotations

import logging
import re
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.locations.management.commands.dump_locations_sheets import notes_from
from apps.locations.management.commands.import_locations import DEFAULT_SHEET_PATH
from apps.locations.models import Location
from apps.locations.services.elevation import fetch_elevation

logger = logging.getLogger(__name__)

# Open-Meteo's elevation endpoint is free and unmetered, but a burst of ~90
# lookups is still worth spacing out — the same courtesy delay
# refresh_centroid_elevations applies for the same API.
DEFAULT_DELAY_SECONDS = 0.2

# How far a resolved height may sit from the figure a curated note claims
# before the pin is worth a second look. Generous on purpose: the sheet's
# top_elevation_m is rounded and often names the highest lift-served point
# rather than the summit itself, so a 60 m disagreement is ordinary and only a
# larger one suggests the coordinate is wrong.
ELEVATION_DISAGREEMENT_M = 60.0

# The claimed height inside a curated note — "ele=3329 m" as written by the
# SNOW-732 import. A note in any other shape simply has nothing to check
# against, which is not an error.
_CLAIMED_RE = re.compile(r"ele=(\d+(?:\.\d+)?)\s*m")


class Command(BaseCommand):
    """Resolve the elevation of every Location that has none."""

    help = "Resolve Location.elevation_m from each location's coordinate."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the command's flags.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the resolved elevations. Without it, nothing is saved.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-resolve locations that already carry an elevation.",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help=(
                "Only compare stored elevations against the heights their "
                "notes claim. Resolves nothing and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=DEFAULT_DELAY_SECONDS,
            help=f"Seconds between lookups (default {DEFAULT_DELAY_SECONDS}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve elevations, or report on the ones already stored.

        Args:
            *args: Unused positional arguments.
            **options: Django's parsed options.

        Raises:
            SystemExit: Non-zero when any lookup failed, so a scheduled run
                is visibly bad rather than quietly partial.

        """
        verbosity = int(options["verbosity"])
        if options["report"]:
            self._report()
            return

        commit = bool(options["commit"])
        force = bool(options["force"])
        delay = float(options["delay"])

        queryset = Location.objects.all() if force else Location.objects.unresolved()
        counts = {"resolved": 0, "failed": 0}

        for location in iterate_rows(self, queryset, verbosity=verbosity):
            self._resolve_one(
                location, counts, commit=commit, delay=delay, verbosity=verbosity
            )

        self.stdout.write(f"{counts['resolved']} resolved, {counts['failed']} failed.")
        if not commit:
            self.stdout.write("Dry-run (no --commit) — nothing written.")
        # A partially failed batch exits non-zero so cron and CI can see it,
        # per CLAUDE.md's management-command contract.
        if counts["failed"]:
            raise SystemExit(1)

    def _resolve_one(
        self,
        location: Location,
        counts: dict[str, int],
        *,
        commit: bool,
        delay: float,
        verbosity: int,
    ) -> None:
        """Resolve one location's elevation.

        Args:
            location: The location to resolve.
            counts: The running tally, mutated in place.
            commit: Whether to persist the resolved value.
            delay: Seconds to sleep after a successful lookup.
            verbosity: Django's ``--verbosity`` level.

        """
        try:
            elevation = fetch_elevation(location.latitude, location.longitude)
        except Exception:  # noqa: BLE001 — one location must not abort the run
            logger.exception(
                "fill_location_elevations: failed to resolve %s", location.pk
            )
            counts["failed"] += 1
            return

        counts["resolved"] += 1
        if verbosity >= 2:
            self.stdout.write(f"{location.name} -> {elevation:.0f}m")
        if commit:
            location.elevation_m = elevation
            location.save(update_fields=["elevation_m"])
        if delay > 0:
            time.sleep(delay)

    def _report(self) -> None:
        """Print locations whose resolved elevation disagrees with the sheet.

        ``note`` is a SHEET column with no model field — ``dump_locations_sheets``
        carries it forward from the file on disk, keyed by uuid, and it never
        reaches the database. So the claim is read from the sheet and the
        resolved height from the DB, which is what makes this a comparison of
        two independent sources rather than a row against itself.
        """
        notes = notes_from(DEFAULT_SHEET_PATH)
        checked = 0
        disagreements = []
        for location in Location.objects.exclude(elevation_m__isnull=True):
            match = _CLAIMED_RE.search(notes.get(str(location.uuid), ""))
            if not match:
                continue
            resolved = location.elevation_m
            if resolved is None:  # excluded by the query; narrows the type
                continue
            checked += 1
            claimed = float(match.group(1))
            delta = abs(resolved - claimed)
            if delta > ELEVATION_DISAGREEMENT_M:
                disagreements.append((location, claimed, delta))

        for location, claimed, delta in sorted(disagreements, key=lambda row: -row[2]):
            self.stdout.write(
                f"{location.name}: sheet claims {claimed:.0f}m, "
                f"resolved {location.elevation_m:.0f}m, off by {delta:.0f}m"
            )
        self.stdout.write(
            f"{checked} location(s) checked, {len(disagreements)} disagreement(s) "
            f"over {ELEVATION_DISAGREEMENT_M:.0f}m."
        )
