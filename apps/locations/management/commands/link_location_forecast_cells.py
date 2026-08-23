"""link_location_forecast_cells — resolve each Location's elevation and cell.

Companion to ``import_locations``, in exactly the role
``link_resort_forecast_points`` plays for ``import_resorts``: the import
writes what a curator typed, and this resolves the two fields that need an
external call and therefore cannot ride on a model save.

For each unresolved ``Location``:

1. ``fetch_elevation`` gives the location's **own** height. This is not the
   forecast cell's ``elevation`` — that is the representative height of
   whichever pin minted the cell, shared by everything in it, and Mont Fort
   must carry 3328 m rather than the cell's average. Two calls per location
   is the price of the distinction, and this is a one-shot backfill.
2. ``resolve_forecast_point`` reuses (or creates) the shared cell the
   location's coordinates fall in, which is what puts the location into
   ``ForecastPoint.objects.active()`` and so into the scheduled
   ``fetch_weather`` point pass — no scheduler change needed.

Both calls are made even in a dry run, so the reported outcome reflects
reality; only the writes are gated on ``--commit``. They are kept outside
any transaction (mirroring ``apps.favourites.services.create_favourite``) so
a slow or failing request never holds a lock.

The elevation doubles as a **check on the curation**: a location whose
resolved height is nowhere near the figure in its sheet ``note`` has been
mis-pinned, and that shows up here rather than on the resort page.

A location that fails to resolve is logged and counted under ``failed``; it
never aborts the batch, and the command exits non-zero when any failed, so
cron/CI can detect a partial run.

Idempotent — ``Location.objects.unresolved()`` excludes rows that already
have both fields, so a second run with nothing new selects zero.

Usage:
    # Preview — resolves and reports, writes nothing.
    uv run python manage.py link_location_forecast_cells

    # Persist the resolved elevation and cell.
    uv run python manage.py link_location_forecast_cells --commit

    # Loosen pacing between the per-location lookups (default 1.0s).
    uv run python manage.py link_location_forecast_cells --commit --delay 2
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser, ArgumentTypeError
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.command_iteration import iterate_rows
from apps.locations.models import Location
from apps.weather.services.elevation import fetch_elevation
from apps.weather.services.forecast_points import resolve_forecast_point

logger = logging.getLogger(__name__)


def _non_negative_float(raw: str) -> float:
    """
    Argparse ``type=`` helper for non-negative float arguments.

    Args:
        raw: The raw command-line string.

    Returns:
        The parsed, non-negative float.

    Raises:
        ArgumentTypeError: if the value is unparseable or negative.

    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise ArgumentTypeError(f"invalid float value: {raw!r}") from exc
    if value < 0:
        raise ArgumentTypeError(f"delay must be non-negative (got {value})")
    return value


class Command(BaseCommand):
    """Resolve elevation and forecast cell for every unresolved Location.

    Read-only by default; pass --commit to persist. Locations that already
    carry both fields are excluded from the candidate set. Per-location
    failures are caught, logged and counted — they never abort the batch —
    and the command exits non-zero when any location failed to resolve.
    """

    help = (
        "Resolve each unresolved Location's own elevation (via "
        "fetch_elevation) and its shared forecast cell (via "
        "resolve_forecast_point), widening the point-weather polling set to "
        "cover curated locations. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the resolved elevation_m and forecast_cell on each "
                "location. Without this flag the command resolves (and "
                "reports) but writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive locations. "
                "Default 1.0 — paces the run inside Open-Meteo's free-tier "
                "rate limit. Pass 0 to disable pacing for a tiny count."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve every candidate location and report the outcome."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        # Streamed, not materialised: Location is a growable table — SNOW-704
        # and SNOW-709 mint a row per favourite and per observation.
        candidates = Location.objects.unresolved()
        total = candidates.count()

        self._announce(total, commit=commit, delay=delay)

        counts = {"linked": 0, "skipped": 0, "failed": 0}
        for index, location in enumerate(
            iterate_rows(
                self,
                candidates,
                verbosity=verbosity,
                describe=lambda row: f"{row.pk} {row}",
            )
        ):
            self._resolve_one(location, counts, commit=commit, verbosity=verbosity)
            if delay > 0 and index < total - 1:
                time.sleep(delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"link_location_forecast_cells completed with "
                f"{counts['failed']} location failure(s). Check logs for details."
            )

    def _resolve_one(
        self,
        location: Location,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Resolve one location's elevation and cell, counting the outcome.

        Args:
            location: The location to resolve.
            counts: The running tally, mutated in place.
            commit: Whether to persist the resolved values.
            verbosity: Django's ``--verbosity`` level.

        """
        try:
            # External HTTP calls — kept outside any transaction so a slow
            # or failing request never holds a DB lock.
            elevation = fetch_elevation(location.latitude, location.longitude)
            cell = resolve_forecast_point(location.latitude, location.longitude)
        except Exception:  # noqa: BLE001 — broad catch intentional: a per-location failure must not abort the batch
            logger.exception(
                "link_location_forecast_cells: failed to resolve location %r (id=%s)",
                location.name or "<anonymous>",
                location.pk,
            )
            counts["failed"] += 1
            return

        if commit:
            location.elevation_m = elevation
            location.forecast_cell = cell
            location.save(update_fields=["elevation_m", "forecast_cell", "updated_at"])
        counts["linked"] += 1
        if verbosity >= 2:
            logger.info(
                "Resolved location %r (id=%s) -> %.0fm, ForecastPoint id=%s",
                location.name or "<anonymous>",
                location.pk,
                elevation,
                cell.pk,
            )

    def _announce(self, candidate_count: int, *, commit: bool, delay: float) -> None:
        """Write the start-of-run banner and matching log line."""
        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Resolving {candidate_count} location(s) to an elevation "
                f"and a forecast cell{flag_label}"
            )
        )
        logger.info(
            "link_location_forecast_cells started: candidates=%d commit=%s delay=%s",
            candidate_count,
            commit,
            delay,
        )

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['linked']} resolved, "
                        f"{counts['skipped']} skipped, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['linked']} "
                        f"location(s) would be resolved, {counts['failed']} "
                        "failed. No data written. Pass --commit to persist."
                    )
                )

        logger.info(
            "link_location_forecast_cells finished: linked=%d skipped=%d "
            "failed=%d commit=%s",
            counts["linked"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
