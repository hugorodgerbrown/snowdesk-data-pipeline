"""
check_ingest — fail when any scheduled feed is red (SNOW-1423).

Reads the staff ingest report (``apps.public.ingest_report``) for the last
day and raises ``CommandError`` naming every red feed. The scheduler runs it
hourly at :50, so a feed that stops bringing data in without failing (a
provider gone quiet mid-season, weather missing for some places) logs an
error, and Sentry alerts on it. A failed run already reaches Sentry on its
own; this catches the silence.

Writes nothing. An off-season bulletin provider is never red, so it never
alerts.

Usage::

    python manage.py check_ingest
    python manage.py check_ingest --window 1h
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import CommandError
from django.utils import timezone

from apps.core.command_register import RegisteredCommand
from apps.public.ingest_report import (
    DEFAULT_WINDOW,
    WINDOWS,
    build_report,
    red_feeds,
)

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Raise when any feed in the ingest report is red."""

    help = "Fail when any scheduled feed is red in the ingest report."
    mcp_runnable = True

    def add_arguments(self, parser: Any) -> None:
        """Add ``--window``."""
        parser.add_argument(
            "--window",
            choices=list(WINDOWS),
            default=DEFAULT_WINDOW,
            help=f"How far back to look (default {DEFAULT_WINDOW}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Build the report, print each feed's state and raise on any red."""
        verbosity = int(options["verbosity"])
        report = build_report(options["window"], now=timezone.now())
        red = red_feeds(report)
        self.tally("feeds_red", len(red))
        if verbosity >= 2:
            for group in report["groups"]:
                for row in group["feeds"]:
                    self.stdout.write(f"  {row['label']}: {row['status']}")
        if red:
            names = "; ".join(f"{row['label']} ({row['reason']})" for row in red)
            raise CommandError(f"check_ingest: {len(red)} red feed(s): {names}")
        if verbosity >= 1:
            self.stdout.write(f"check_ingest: {report['summary']}")
