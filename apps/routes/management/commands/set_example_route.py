r"""set_example_route — choose the example route every visitor is offered.

SNOW-1230. The map's welcome card offers "Explore an example route": one
real tour, opened on a past day with a full bulletin, that a visitor can
explore before they upload anything. This command is the only thing that
sets it.

It takes a canonical track (``apps/routes/fixtures/canonical/``, SNOW-989
— real tours, committed with their owner's agreement) and:

1. makes sure the example owner account exists
   (``apps.routes.services.example.EXAMPLE_OWNER_EMAIL``, which nobody
   signs in to, so the tour sits in nobody's own routes list);
2. creates that track as one of its routes, through ``create_route`` — the
   path an upload takes — unless the owner already holds it;
3. marks a share of that route as the example, opening on ``--date``
   (``apps.routes.services.example.set_example``).

Bare, it says what the example is now and what a ``--commit`` run would
change; it writes nothing and asks the terrain origin nothing.

Usage:
    # Preview, with the defaults (Mont Fort – Col de la Chaux, 10 April 2026).
    uv run python manage.py set_example_route

    # Set it.
    uv run python manage.py set_example_route --commit

    # Another tour, or another day.
    uv run python manage.py set_example_route --track hidden-valley.gpx \\
        --date 2026-03-02 --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_register import RegisteredCommand
from apps.routes.models import Route, RouteShare
from apps.routes.services.canonical import canonical_documents
from apps.routes.services.example import (
    DEFAULT_EXAMPLE_DATE,
    DEFAULT_EXAMPLE_FILENAME,
    example_owner,
    example_share,
    set_example,
)
from apps.routes.services.routes import create_route

logger = logging.getLogger(__name__)


def _iso_date(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` argument.

    Args:
        value: The raw argument.

    Returns:
        The date.

    Raises:
        ValueError: When the value is not an ISO date; argparse reports it.

    """
    return date.fromisoformat(value)


def _describe(current: RouteShare | None) -> str:
    """Say what the example is now, for the run's first line.

    Args:
        current: The live example share, or None.

    Returns:
        One line for stdout.

    """
    if current is None or current.route is None:
        return "No example route is set."
    return (
        f"Current example: {current.route.source_filename} on "
        f"{current.example_date} (share {current.token})"
    )


def _is_current(current: RouteShare | None, track: str, example_date: date) -> bool:
    """Whether the example is already ``track`` on ``example_date``.

    Args:
        current: The live example share, or None.
        track: The canonical file name asked for.
        example_date: The day asked for.

    Returns:
        True when a run would change nothing.

    """
    return (
        current is not None
        and current.route is not None
        and current.route.source_filename == track
        and current.example_date == example_date
    )


class Command(RegisteredCommand):
    """Set the example route and the day it opens on.

    Read-only by default; pass --commit to write. Exits non-zero when the
    track is not in the canonical corpus or cannot be imported.
    """

    help = (
        "Set the example route the map offers every visitor, from a canonical "
        "track, and the past day it opens on. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        parser.add_argument(
            "--track",
            default=DEFAULT_EXAMPLE_FILENAME,
            help=(
                "The canonical track's file name, from "
                f"apps/routes/fixtures/canonical/ (default {DEFAULT_EXAMPLE_FILENAME})."
            ),
        )
        parser.add_argument(
            "--date",
            type=_iso_date,
            default=DEFAULT_EXAMPLE_DATE,
            help=(
                "The day the example opens the map on, YYYY-MM-DD — a past day "
                f"with a full bulletin (default {DEFAULT_EXAMPLE_DATE.isoformat()})."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the change. Without it the command only reports.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Report, and with --commit set, the example route.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When the track is unknown or cannot be imported.

        """
        track: str = options["track"]
        example_date: date = options["date"]
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        documents = dict(canonical_documents())
        if track not in documents:
            raise CommandError(
                f"{track} is not a canonical track. Choose one of: "
                + ", ".join(sorted(documents))
            )

        current = example_share()
        if verbosity >= 1:
            self.stdout.write(_describe(current))

        if _is_current(current, track, example_date):
            self.tally("set", 0)
            if verbosity >= 1:
                self.stdout.write("Example route up to date.")
            return

        if not commit:
            self.tally("set", 1)
            if verbosity >= 1:
                self.stdout.write(
                    f"Would set the example to {track} on {example_date} [READ-ONLY]"
                )
                self.stdout.write(
                    "Read-only run — nothing written. Pass --commit to apply."
                )
            return

        route = self._example_route(track, documents[track])
        share = set_example(route, example_date)
        self.tally("set", 1)
        if verbosity >= 1:
            self.stdout.write(
                f"Example set: {track} on {example_date} (share {share.token})"
            )

    def _example_route(self, track: str, document: bytes) -> Route:
        """Return the example owner's copy of ``track``, importing it if absent.

        Args:
            track: The canonical file name.
            document: The track's GPX bytes.

        Returns:
            The owner's route.

        Raises:
            CommandError: When the track cannot be imported.

        """
        owner = example_owner()
        route = Route.objects.for_user(owner).filter(source_filename=track).first()
        if route is not None:
            self.tally("routes_created", 0)
            return route
        try:
            route = create_route(owner, document, source_filename=track)
        except Exception as exc:  # noqa: BLE001 — reported as the command's failure, with the cause
            self.tally("failed", 1)
            logger.exception("set_example_route: could not import %s", track)
            raise CommandError(f"Could not import {track}: {exc}") from exc
        self.tally("routes_created", 1)
        return route
