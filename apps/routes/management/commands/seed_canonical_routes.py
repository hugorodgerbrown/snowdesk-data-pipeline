"""seed_canonical_routes — make sure the dev user holds the canonical routes.

SNOW-1023. ``seed_test_data`` puts the four canonical tracks
(``apps/routes/fixtures/canonical/``, SNOW-989) into the seeded dev user's
account — but it INSERTS rather than reconciles, so ``bin/init-worktree``
runs it only on the session that creates ``db.sqlite3`` (SNOW-997). A
worktree seeded before SNOW-989 therefore never gets the routes, and a
route whose terrain sampling failed at seed time (no network, say) stays
flat for the rest of that worktree's life.

This command is the reconciling half. For the dev user
``NORMAL_USER_EMAIL`` it:

1. creates each canonical route the user does not already hold, matched
   on ``source_filename``, through ``create_route`` — the path an upload
   and ``seed_test_data`` both take, so the derived fields are the real
   parser's;
2. samples the terrain under each of the user's canonical routes whose
   ``slope_samples`` is still null, with ``build_slope_samples`` — the
   function ``backfill_route_slope_samples`` and the upload worker share.

Only the canonical routes are touched, not the synthetic ``seed-route.gpx``
nor anything else the user uploaded: this command owns the corpus and
nothing more.

A route created in THIS run is not re-sampled in step 2 even if it comes
back null. ``create_route`` has already enqueued its sampling, which runs
inline under the ImmediateBackend a worktree uses; a null afterwards means
the origin answered nothing, and asking again a second later would double
the wait for the same answer. The next run picks it up.

When nothing needed doing it prints ``UP_TO_DATE`` and nothing else, and
``bin/init-worktree`` greps for that exact sentence to stay quiet on a
current worktree. Change it in one place only: here.

An absent dev user is reported and is not a failure — there is nobody to
seed for, and a SessionStart hook that exited non-zero over it would stop
the worktree bootstrapping.

Usage:
    # Preview — reports what would be created and sampled; writes nothing
    # and asks the terrain origin nothing.
    uv run python manage.py seed_canonical_routes

    # Create and sample.
    uv run python manage.py seed_canonical_routes --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.management.commands.seed_test_data import NORMAL_USER_EMAIL
from apps.routes.models import Route
from apps.routes.services.canonical import canonical_documents
from apps.routes.services.routes import create_route
from apps.routes.services.slope_segments import build_slope_samples

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# The one line a run with nothing to do prints. ``bin/init-worktree`` and
# the tests match on this exact string.
UP_TO_DATE = "Canonical routes up to date."


class Command(BaseCommand):
    """Create and sample the dev user's canonical routes where missing.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    route failed to be created or sampled.
    """

    help = (
        "Give the seeded dev user every canonical route it lacks, and sample "
        "the terrain under any of them still unsampled. Read-only unless "
        "--commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to register on.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Create the missing routes and sample the unsampled ones. "
                "Without this flag the command reports what it would do, "
                "makes no request and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Reconcile the dev user's canonical routes.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any route failed, so cron/CI sees non-zero.

        """
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        user = get_user_model().objects.filter(email=NORMAL_USER_EMAIL.lower()).first()
        if user is None:
            if verbosity >= 1:
                self.stdout.write(
                    f"No dev user {NORMAL_USER_EMAIL} — no canonical routes "
                    "to seed. Run seed_test_data --include user first."
                )
            return

        documents = canonical_documents()
        filenames = [filename for filename, _raw in documents]
        held = set(
            Route.objects.for_user(user)
            .filter(source_filename__in=filenames)
            .values_list("source_filename", flat=True)
        )
        missing = [(name, raw) for name, raw in documents if name not in held]

        # SNOW-602 exempt: the unit of work is a fixed corpus of four
        # committed files, not a growable table. Both loops are bounded by
        # it — ``missing`` is a subset of the corpus, and ``unsampled`` is
        # the user's routes whose filename is IN the corpus — so there is
        # nothing to stream and no countdown worth printing over at most
        # four items.
        unsampled = list(
            Route.objects.for_user(user).filter(
                source_filename__in=filenames, slope_samples__isnull=True
            )
        )

        if not missing and not unsampled:
            if verbosity >= 1:
                self.stdout.write(UP_TO_DATE)
            return

        if not commit:
            self._report_preview(missing, unsampled, verbosity=verbosity)
            return
        self._apply(user, missing, unsampled, verbosity=verbosity)

    def _apply(
        self,
        user: User,
        missing: list[tuple[str, bytes]],
        unsampled: list[Route],
        *,
        verbosity: int,
    ) -> None:
        """Create the missing routes, then sample the unsampled ones.

        Args:
            user: The dev user.
            missing: The canonical documents the user does not hold.
            unsampled: The user's canonical routes with no slope samples,
                read BEFORE any creation so a route made here is not
                sampled twice (see the module docstring).
            verbosity: Django's --verbosity level.

        Raises:
            CommandError: When any route failed.

        """
        failed = 0
        for filename, raw in missing:
            failed += self._create_one(user, filename, raw, verbosity=verbosity)
        for route in unsampled:
            failed += self._sample_one(route, verbosity=verbosity)

        logger.info(
            "seed_canonical_routes finished: created=%d sampled_candidates=%d "
            "failed=%d",
            len(missing),
            len(unsampled),
            failed,
        )
        if failed:
            raise CommandError(
                f"seed_canonical_routes completed with {failed} failure(s). "
                "Check logs for details."
            )

    def _report_preview(
        self,
        missing: list[tuple[str, bytes]],
        unsampled: list[Route],
        *,
        verbosity: int,
    ) -> None:
        """Say what a --commit run would do, and do none of it.

        Args:
            missing: The canonical documents the user does not hold.
            unsampled: The user's canonical routes with no slope samples.
            verbosity: Django's --verbosity level.

        """
        if verbosity < 1:
            return
        for filename, _raw in missing:
            self.stdout.write(f"Would create {filename} [READ-ONLY]")
        for route in unsampled:
            self.stdout.write(f"Would sample {route.source_filename} [READ-ONLY]")
        self.stdout.write(
            "Read-only run — no requests made, no data written. Pass --commit to apply."
        )

    def _create_one(
        self, user: User, filename: str, raw: bytes, *, verbosity: int
    ) -> int:
        """Create one canonical route through the upload path.

        Args:
            user: The dev user.
            filename: The canonical file's name, stored as source_filename.
            raw: The file's bytes.
            verbosity: Django's --verbosity level.

        Returns:
            1 if the route could not be created, else 0.

        """
        try:
            route = create_route(user, raw, source_filename=filename)
        except Exception:  # noqa: BLE001 — broad catch intentional: one file must not abort the rest
            logger.exception("seed_canonical_routes: failed to create %s", filename)
            return 1
        route.refresh_from_db(fields=["slope_samples"])
        if verbosity >= 1:
            state = "sampled" if route.slope_samples is not None else "unsampled"
            self.stdout.write(f"Created {filename} ({state})")
        return 0

    def _sample_one(self, route: Route, *, verbosity: int) -> int:
        """Sample the terrain under one route and store the record.

        A None from the sampler is left null, as in
        ``backfill_route_slope_samples``: the origin answered nothing, a
        later run retries, and it is not a failure.

        Args:
            route: The unsampled route.
            verbosity: Django's --verbosity level.

        Returns:
            1 if sampling raised, else 0.

        """
        try:
            samples = build_slope_samples(route.points, f"route pk={route.pk}")
        except Exception:  # noqa: BLE001 — broad catch intentional: one route must not abort the rest
            logger.exception(
                "seed_canonical_routes: failed to sample route uuid=%s", route.uuid
            )
            return 1
        if samples is None:
            if verbosity >= 1:
                self.stdout.write(
                    f"Left {route.source_filename} unsampled (origin answered nothing)"
                )
            return 0
        route.slope_samples = samples
        route.save(update_fields=["slope_samples", "updated_at"])
        if verbosity >= 1:
            self.stdout.write(f"Sampled {route.source_filename}")
        return 0
