"""
apps/routes/management/commands/seed_test_routes.py — Dev route seeding.

Gives the seeded dev user a couple of real routes so the map's routes panel,
the /account/routes/ page and the shared-route flow can be looked at with
content in them rather than in their empty state. Without this the only way
to see any of those surfaces populated is to find a .gpx and upload it by
hand, which everyone does differently and nobody writes down.

Deliberately NOT a ``SeedModel`` layer inside ``seed_test_data``. That command
runs on every worktree bootstrap and in CI, and its dataset is what the
SNOW-13 query-count baselines are measured against; adding rows there to suit
a manual look at one panel is how that baseline drifts. This is opt-in, and
seeds nothing unless it is run.

The fixtures are parsed through ``create_route`` — the same entry point an
upload takes — so distance, ascent, descent, thinning and bounds are derived
exactly as a real row's are instead of being written by hand. Two tracks, and
the pair is the point:

  * ``mont-fort-from-les-ruinettes.gpx`` carries elevation and timestamps, so
    its row shows ascent and descent and its popup draws a profile.
  * ``col-des-mines-traverse.gpx`` carries neither, which is the case
    /help/routes/ describes as showing no profile rather than a flat line.
    Seeding one of each means that claim can be checked rather than trusted.

Both sit near Verbier, inside CH-4115 — the region the seeded dev user is
already subscribed to — so they appear on the map where that user is looking.

Usage::

    # Read-only: report what would be created.
    python manage.py seed_test_routes

    # Persist.
    python manage.py seed_test_routes --commit

Exit codes:

- ``0`` — fixtures seeded, or nothing to do (the user already has routes).
- Non-zero — the dev user is missing, a fixture is unreadable or unparseable,
  or the per-user cap is already reached (``CommandError``).
"""

import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.routes.models import Route
from apps.routes.services.routes import create_route

logger = logging.getLogger(__name__)

#: The dev account the fixtures are attached to. Matches NORMAL_USER_EMAIL in
#: seed_test_data — the subscribed user documented in docs/worktrees.md.
DEV_USER_EMAIL = "dev@snowdesk.dev"

#: Where the .gpx fixtures live.
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "gpx"

#: Fixture filenames, in the order they are seeded.
FIXTURES = (
    "mont-fort-from-les-ruinettes.gpx",
    "col-des-mines-traverse.gpx",
)


class Command(BaseCommand):
    """Seed the dev user with a couple of GPX routes."""

    help = (
        "Attach two sample GPX routes to the seeded dev user so the routes "
        "panel, the Routes page and the share flow can be viewed with content "
        "in them. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the routes. Without it the command reports what it "
                "would create and writes nothing."
            ),
        )

    def _dev_user(self) -> User:
        """Return the seeded dev account.

        Returns:
            The user the fixtures are attached to.

        Raises:
            CommandError: If that account has not been seeded yet.

        """
        try:
            return User.objects.get(email=DEV_USER_EMAIL)
        except User.DoesNotExist as exc:
            raise CommandError(
                f"No user {DEV_USER_EMAIL!r}. Run `seed_test_data --include "
                f"user` first (bin/init-worktree does this for you)."
            ) from exc

    def _seed_one(self, user: User, filename: str, remaining: int) -> None:
        """Create one route from a bundled fixture.

        Args:
            user: The owner to attach the route to.
            filename: Fixture filename inside ``FIXTURE_DIR``.
            remaining: Countdown position, printed so stdout reads down to 1.

        Raises:
            CommandError: If the fixture cannot be read or parsed.

        """
        path = FIXTURE_DIR / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CommandError(f"Cannot read fixture {path}: {exc}") from exc

        try:
            route = create_route(user, raw, source_filename=filename)
        except Exception as exc:  # noqa: BLE001 — surfaced as CommandError
            raise CommandError(
                f"Failed to create route from {filename}: {exc}"
            ) from exc

        logger.info("seed_test_routes: created route %s", route.uuid)
        ascent = f"{route.ascent_m:.0f} m" if route.ascent_m else "no elevation"
        self.stdout.write(
            f"[{remaining}] {route.name} — "
            f"{route.distance_m / 1000:.1f} km, {ascent}, "
            f"{route.point_count} points"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Seed the fixtures, or report what would be seeded.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: If the dev user is absent, a fixture cannot be read,
                or ``create_route`` rejects one.

        """
        commit: bool = options["commit"]
        # Verbosity 0 silences the command entirely; the writes still happen.
        self.stdout = self.stdout if options["verbosity"] else _Silent()  # type: ignore[assignment]

        user = self._dev_user()

        existing = Route.objects.for_user(user).count()
        if existing:
            self.stdout.write(
                f"{DEV_USER_EMAIL} already has {existing} route(s) — "
                f"nothing to do. Remove them in the app to re-seed."
            )
            return

        # Iterated as a fixed tuple of files rather than via
        # apps.core.command_iteration: the unit of work is a bundled fixture,
        # not a growable queryset, which is the derived-unit exemption in
        # docs/management-commands.md. The countdown is kept anyway so stdout
        # reads like every other command's.
        total = len(FIXTURES)
        for index, filename in enumerate(FIXTURES):
            remaining = total - index
            if commit:
                self._seed_one(user, filename, remaining)
            else:
                self.stdout.write(f"[{remaining}] would create from {filename}")

        if commit:
            self.stdout.write(self.style.SUCCESS(f"Seeded {total} route(s)."))
        else:
            self.stdout.write(
                f"Dry run — {total} route(s) would be created. "
                f"Re-run with --commit to persist."
            )


class _Silent:
    """Stand-in for ``self.stdout`` under ``--verbosity 0``.

    Swallowing writes in one place keeps every ``verbosity`` check out of the
    command body, which is what pushed ``handle`` past the complexity limit
    when each write guarded itself.
    """

    def write(self, *args: Any, **kwargs: Any) -> None:
        """Discard the message."""
        return None
