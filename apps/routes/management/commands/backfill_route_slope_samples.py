"""backfill_route_slope_samples — sample the terrain under existing routes.

One-shot backfill for SNOW-910. Every ``Route`` uploaded before that
ticket has a null ``slope_samples``, which means NEVER SAMPLED and draws
as a flat line; this walks each of them and asks the terrain grid how
steep the ground under it is.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations, and this one could not be a migration even if it did not: the
work is one HTTP request per terrain tile a track crosses, against an
origin outside this process, and a deploy's ``migrate`` step must not sit
on a table making network calls. Migration ``0005`` adds the column and
nothing else.

**Null is the candidate, and stays honest.** The queryset selects rows
whose ``slope_samples`` is null. A row that comes back with nothing
learnable — the origin unreachable for the whole of it — is left null
rather than written as a record of nothing, so a later run picks it up
again; that rule is ``build_slope_samples``', not this command's, and is
argued in its module docstring.

Idempotent: a sampled row is not a candidate, so a second run selects only
what the first could not answer for.

**This command makes real outbound requests, so it paces itself.** The
default ``--delay`` is a second between routes, which is politeness
towards the tile origin rather than a rate limit it imposes. A long run is
expected: a 15 km tour is several hundred samples.

Usage:
    # Preview — reports how many routes would be sampled, writes nothing
    # and asks the origin nothing.
    uv run python manage.py backfill_route_slope_samples

    # Sample and persist.
    uv run python manage.py backfill_route_slope_samples --commit

    # A first batch, to watch it before committing to the whole table.
    uv run python manage.py backfill_route_slope_samples --commit --limit 20
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.routes.models import Route
from apps.routes.services.slope_segments import build_slope_samples

logger = logging.getLogger(__name__)

# Seconds between routes. Not imposed by the origin — the tiles are served
# ``immutable, max-age=31536000`` off a CDN and a route mostly re-reads
# tiles the process cache already holds — but a backfill over the whole
# table is the one caller that could look like a scrape.
_DEFAULT_DELAY_S = 1.0


class Command(BaseCommand):
    """Sample the terrain under every Route that has never been sampled.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    route failed, so a partial run is detectable.
    """

    help = (
        "Sample the terrain under every Route uploaded before SNOW-910 and "
        "store the result on slope_samples. Read-only unless --commit."
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
                "Sample the terrain and persist the results. Without this "
                "flag the command reports the candidates, makes no request "
                "and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds to wait between routes. Defaults to "
                f"{_DEFAULT_DELAY_S}; 0 disables the pause."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Stop after this many routes. Defaults to 0, meaning every "
                "candidate — a first small batch is the reason this exists."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Sample every candidate route.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any route failed, so cron/CI sees non-zero.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]

        # Streamed, not materialised: Route is a growable user table.
        candidates = Route.objects.filter(slope_samples__isnull=True)
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Sampling the terrain under {total} route(s){flag_label}"
            )
        )
        logger.info(
            "backfill_route_slope_samples started: candidates=%d commit=%s "
            "delay=%s limit=%s",
            total,
            commit,
            delay,
            limit,
        )

        counts = {"sampled": 0, "unanswered": 0, "failed": 0}
        processed = 0
        for route in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.uuid}",
        ):
            self._backfill_one(route, counts, commit=commit)
            processed += 1
            # Checked AFTER the work, not before it. ``iterate_rows`` prints
            # its countdown line as it yields, so breaking on the way in
            # would count a row down and then discard it — ``--limit 20``
            # reading as 21 rows processed. Breaking here also skips the
            # trailing ``--delay``, which nothing is waiting for.
            if limit and processed >= limit:
                break
            if commit and delay:
                time.sleep(delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_route_slope_samples completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _backfill_one(
        self, route: Route, counts: dict[str, int], *, commit: bool
    ) -> None:
        """Sample one route and store the result.

        ``save(update_fields=…)`` writes the one column and nothing else:
        the row is the user's, and the sampler has no business touching
        the geometry or the name.

        Args:
            route: The route to sample.
            counts: The running tally, mutated in place.
            commit: Whether to make the requests and persist.

        """
        if not commit:
            # No request either, not merely no write: a read-only run must
            # not spend the origin's bandwidth to report a count it already
            # has from the queryset.
            counts["sampled"] += 1
            return
        try:
            samples = build_slope_samples(route.points, f"route pk={route.pk}")
        except Exception:  # noqa: BLE001 — broad catch intentional: one route must not abort the batch
            logger.exception(
                "backfill_route_slope_samples: failed on route id=%s uuid=%s",
                route.pk,
                route.uuid,
            )
            counts["failed"] += 1
            return

        if samples is None:
            # Left null on purpose — see the module docstring. Not a
            # failure: the command completes, and a later run retries it.
            counts["unanswered"] += 1
            return

        route.slope_samples = samples
        route.save(update_fields=["slope_samples", "updated_at"])
        counts["sampled"] += 1

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log.

        Args:
            counts: The final tally.
            commit: Whether --commit was passed.
            verbosity: Django's --verbosity level.

        """
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['sampled']} route(s) sampled, "
                        f"{counts['unanswered']} left null (nothing learnable), "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['sampled']} "
                        "route(s) would be sampled. No requests made, no "
                        "data written. Pass --commit to sample."
                    )
                )
        logger.info(
            "backfill_route_slope_samples finished: sampled=%d unanswered=%d "
            "failed=%d commit=%s",
            counts["sampled"],
            counts["unanswered"],
            counts["failed"],
            commit,
        )
