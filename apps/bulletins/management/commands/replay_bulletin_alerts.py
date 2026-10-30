"""
apps/bulletins/management/commands/replay_bulletin_alerts.py.

Management command (SNOW-949).

Prints every bulletin alert a subscriber to one region would have received
across a provider's committed season archive, in delivery order, as they
would have read it. The engine is ``apps.bulletins.services.replay``; the
staff page at ``/_alerts-replay/`` renders the same schedule.

Read-only by construction: it reads the archive file and nothing else, and
never touches ``PushSubscription`` or sends anything, so there is no
``--commit``.

Countdown exemption (docs/management-commands.md): stdout carries a data
artefact — the schedule itself, one line per message — not progress over
rows, and the unit is an archive record rather than a database row.

Typical use::

    python manage.py replay_bulletin_alerts       # CH-4115, SLF, every new bulletin
    python manage.py replay_bulletin_alerts --mode CHANGES
    python manage.py replay_bulletin_alerts --region AT-07-14-01 --source ALBINA
    python manage.py replay_bulletin_alerts --all  # include the skipped decisions

Exit codes: ``0``; non-zero for an unknown source (``CommandError``).
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import CommandError

from apps.bulletins.services.alerts import ALERT_TZ
from apps.bulletins.services.golden_week import SOURCES
from apps.bulletins.services.replay import DEFAULT_REGION, replay_archive
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Print a season of bulletin alerts for one region."""

    help = (
        "Replay a provider's committed season archive for one region and "
        "print every bulletin alert a subscriber would have received "
        "(SNOW-949). Read-only."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument("--region", default=DEFAULT_REGION, help="Micro-region id.")
        parser.add_argument(
            "--source", default="SLF", help=f"One of {', '.join(SOURCES)}."
        )
        parser.add_argument(
            "--mode",
            default="NEW_BULLETIN",
            choices=["NEW_BULLETIN", "CHANGES"],
            help="What earns an alert.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Also print the decisions that sent nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: For an unknown source.

        """
        source = str(options["source"]).upper()
        if source not in SOURCES:
            raise CommandError(f"Unknown source {source!r}; use one of {SOURCES}.")
        alerts = replay_archive(
            source=source, region_id=options["region"], mode=options["mode"]
        )
        verbosity: int = options["verbosity"]
        for alert in alerts:
            if not alert.sent and not options["all"]:
                continue
            when = alert.delivered_at.astimezone(ALERT_TZ).strftime("%a %d %b %H:%M")
            marker = "" if alert.sent else f" [{alert.outcome}]"
            self.stdout.write(f"{when}{marker}  {alert.title}")
            if verbosity:
                self.stdout.write(f"                  {alert.body}")
        counts = Counter(a.outcome for a in alerts)
        self.tally("sent", counts.get("SENT", 0))
        self.tally("skipped", counts.get("SKIPPED", 0))
        if verbosity:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{counts.get('SENT', 0)} sent, {counts.get('SKIPPED', 0)} skipped"
                    f" ({options['mode']}, {options['region']}, {source})."
                )
            )
