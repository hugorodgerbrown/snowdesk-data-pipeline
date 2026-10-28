"""
apps/weather/management/commands/copy_weather_to_service.py — copy_weather_to_service.

A one-time copy, run once per environment that is a source, after
SNOW-1275 deploys: every past ``Weather`` row of a reference location
(``Location.objects.reference_data()``) that the weather service does not
hold yet is copied into ``SourceWeather`` for that location's station.

Why: until SNOW-1253 and SNOW-1275, ``fetch_weather`` and the backfill
wrote ``Weather`` directly, so production's history lives only there. The
service diffs ``SourceWeather`` to decide what is missing, and serves it at
``/api/data/v2/weather/``. Without this copy the hourly sweep would spend
Open-Meteo's quota re-fetching a season production already holds, and the
v2 feed would have no history.

Only past days are copied; today is ``fetch_weather``'s row, which the
service writes itself. A day the service already holds is left alone, so
the command is safe to re-run. Read-only unless ``--commit`` is passed.

Usage:
    uv run python manage.py copy_weather_to_service           # preview
    uv run python manage.py copy_weather_to_service --commit  # copy
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import WEATHER_COLUMNS, SourceLocation, SourceWeather
from apps.locations.models import Location
from apps.weather.models import Weather

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Copy each reference location's past Weather into the weather service."""

    help = (
        "Copy the past Weather rows of every reference location into the "
        "weather service's SourceWeather, where it does not hold the day. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Copy. Without it the command counts what it would copy.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Walk the reference locations, copying each one's missing past days."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        today = timezone.localdate()

        stations = {
            station.uuid: station
            for station in SourceLocation.objects.only("pk", "uuid")
        }
        locations = Location.objects.reference_data().only("pk", "name", "source_uuid")
        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Copying past weather of {locations.count()} reference "
                f"location(s) into the weather service{label}"
            )
        )

        copied = unstationed = 0
        for location in iterate_rows(
            self,
            locations,
            verbosity=verbosity,
            describe=lambda row: row.name or row.source_uuid,
        ):
            station = stations.get(location.source_uuid)
            if station is None:
                # Not mirrored yet: fetch_weather or mirror_reference_data
                # creates its station, and a re-run copies it then.
                unstationed += 1
                continue
            rows = self._missing(location, station, today)
            copied += len(rows)
            if commit and rows:
                with transaction.atomic():
                    SourceWeather.objects.bulk_create(rows)

        self.tally("copied", copied)
        self.tally("unstationed", unstationed)
        if verbosity >= 1:
            verb = "Copied" if commit else "Would copy"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {copied} day(s); {unstationed} location(s) have "
                    "no station yet."
                )
            )
        logger.info(
            "copy_weather_to_service: copied=%d unstationed=%d commit=%s",
            copied,
            unstationed,
            commit,
        )

    def _missing(
        self, location: Location, station: SourceLocation, today: date
    ) -> list[SourceWeather]:
        """Return the station rows for ``location``'s past days the service lacks.

        Args:
            location: The reference location.
            station: Its station.
            today: The first day not copied.

        Returns:
            Unsaved ``SourceWeather`` rows, one per missing past day.

        """
        held = set(
            SourceWeather.objects.filter(location=station).values_list(
                "observed_on", flat=True
            )
        )
        return [
            SourceWeather(
                location=station,
                observed_on=weather.observed_on,
                **{name: getattr(weather, name) for name in WEATHER_COLUMNS},
            )
            for weather in Weather.objects.filter(
                location=location, observed_on__lt=today
            ).exclude(observed_on__in=held)
        ]
