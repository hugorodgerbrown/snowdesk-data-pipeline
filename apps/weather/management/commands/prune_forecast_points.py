"""
apps/weather/management/commands/prune_forecast_points.py.

Management command.

Deletes every ``ForecastPoint`` that no ``Favourite`` and no ``Resort``
references (SNOW-633). A point becomes unreferenced when the last pin
holding it goes away — a favourite deleted by its owner, or a resort
removed by ``import_resorts``. The first production run of that command
retired 22 resorts and left 15 such points behind.

An unreferenced point is already invisible to the pipeline: the
``fetch_weather`` point pass iterates ``ForecastPoint.objects.active()``,
so nothing refreshes it and nothing reads it. What remains is the row plus
its ``ForecastPointWeather`` and ``ForecastPointWeatherHistory`` children,
holding forecasts that can only grow staler. This command removes them.

Both child tables are ``CASCADE``, so deleting the point takes its weather
with it. ``Favourite.forecast_point`` and ``Resort.forecast_point`` are
``PROTECT``, which makes the delete fail-safe: a point that gained a
reference between the walk and the delete raises ``ProtectedError`` rather
than taking a live favourite's weather with it. Such a row is counted as
``failed`` and the command exits non-zero; the rest of the batch still runs.

Read-only by default — pass ``--commit`` to delete (see
docs/decisions/dry-run-default-commands.md).

Typical use::

    # Read-only walk — reports what would be deleted.
    python manage.py prune_forecast_points

    # Persist.
    python manage.py prune_forecast_points --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import ProtectedError

from apps.core.command_iteration import iterate_rows
from apps.weather.models import ForecastPoint

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Delete ForecastPoints that no favourite and no resort references.

    Read-only unless --commit is passed. Weather and history rows cascade
    away with the point; a point that is still referenced is protected by
    the FKs, counted as failed, and does not abort the batch.
    """

    help = (
        "Delete every ForecastPoint with no Favourite and no Resort "
        "referencing it (SNOW-633), along with its cascaded weather and "
        "history rows. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Delete the unreferenced points. Without this flag the "
                "command reports what it would delete and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Walk the unreferenced points and, with ``--commit``, delete them.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: If any point could not be deleted, so the run
                exits non-zero for cron/CI.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        candidates = ForecastPoint.objects.inactive()
        total = candidates.count()

        logger.info(
            "prune_forecast_points started: candidates=%s commit=%s",
            total,
            commit,
        )
        if verbosity >= 1:
            self.stdout.write(f"{total} unreferenced ForecastPoint(s)")

        deleted = 0
        weather_deleted = 0
        history_deleted = 0
        failed = 0

        for point in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: (
                f"{row.pk} ({row.latitude:.4f}, {row.longitude:.4f} "
                f"@ {row.elevation:.0f}m)"
            ),
        ):
            weather = point.weather_snapshots.count()
            history = point.forecast_history.count()
            if not commit:
                deleted += 1
                weather_deleted += weather
                history_deleted += history
                continue
            try:
                point.delete()
            except ProtectedError:
                # The point gained a favourite or resort between the walk
                # and the delete. The FK did its job; count it and carry on.
                failed += 1
                logger.warning(
                    "prune_forecast_points: point %s is referenced — skipped",
                    point.pk,
                )
                continue
            deleted += 1
            weather_deleted += weather
            history_deleted += history

        summary = (
            f"{deleted} point(s), {weather_deleted} weather row(s), "
            f"{history_deleted} history row(s), {failed} failed."
        )
        if verbosity >= 1:
            self.stdout.write(f"{'Deleted' if commit else 'Would delete'} {summary}")
            if not commit and total:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — nothing deleted.")
                )
        logger.info(
            "prune_forecast_points finished: deleted=%s weather=%s history=%s "
            "failed=%s commit=%s",
            deleted,
            weather_deleted,
            history_deleted,
            failed,
            commit,
        )

        if failed:
            raise CommandError(f"{failed} point(s) could not be deleted.")
