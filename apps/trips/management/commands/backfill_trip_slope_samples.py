"""backfill_trip_slope_samples — give existing trips the ground they cross.

Backfill for SNOW-962, and for every later ticket that adds a key to the
record — SNOW-911's ``cruxes`` is the first.

Every ``Trip`` created before SNOW-962 has a null ``slope_samples``, which
means NEVER SAMPLED and draws the trip page's line and height profile
flat. Nothing else would ever fill them in:
``enqueue_trip_slope_sampling`` runs at creation only, and rendering an old
trip schedules nothing. Without this command the whole existing estate
stays permanently uncoloured, which is how the gap was found — by review
on the pull request that added the column.

**IT COPIES BEFORE IT WALKS, and that is most of the point.** A trip's
snapshot was taken from a route whose geometry never changes (``Route``
has create and delete and no geometry edit), so where the source route is
still present, already sampled, and its ``points`` are identical to the
snapshot's, its record answers this trip exactly — and copying costs
nothing where a walk costs an HTTP request per terrain tile. The walk is
the fallback for a trip whose route is gone, unsampled, or no longer
matches.

The equality check is not ceremony. ``Trip.route`` is provenance and may
be null, and a trip may outlive the route it came from; copying a record
belonging to a different track would paint one track's steepness onto
another's, which is the failure this whole feature exists to prevent.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations, and the fallback path makes outbound requests besides.
Migration ``0003`` adds the column and nothing else.

**Two kinds of candidate.** A null is a trip nothing has sampled. A
record with no ``cruxes`` key is one written before SNOW-911, which draws
its colours but none of its markers — and nothing else would ever add
them. A record carrying the key is current even when the list inside is
empty, because that is "nothing was flagged", which is an answer.

**Null stays honest.** A trip that comes back with nothing learnable is
left null rather than written as a record of nothing, so a later run
picks it up again. Idempotent: a current row is not a candidate.

Usage:
    # Preview — reports how many trips would be filled, and by which
    # path, writing nothing and asking the origin nothing.
    uv run python manage.py backfill_trip_slope_samples

    # Copy and sample, and persist.
    uv run python manage.py backfill_trip_slope_samples --commit

    # A first batch, to watch it before committing to the whole table.
    uv run python manage.py backfill_trip_slope_samples --commit --limit 20
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.routes.services.slope_segments import build_slope_samples
from apps.trips.models import Trip

logger = logging.getLogger(__name__)

# Seconds between trips that actually WALK. Matches the routes backfill's
# own default and carries its reasoning: politeness towards the tile
# origin rather than a limit it imposes. A copied row waits for nothing,
# because it asked the origin nothing.
_DEFAULT_DELAY_S = 1.0


class Command(BaseCommand):
    """Fill in ``Trip.slope_samples`` for every trip that has none.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    trip failed, so a partial run is detectable.
    """

    help = (
        "Give every Trip created before SNOW-962 the terrain record its "
        "page draws from — copied from the source route where that is "
        "exactly right, sampled otherwise. Read-only unless --commit."
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
                "Fill in the records and persist them. Without this flag "
                "the command reports the candidates, makes no request and "
                "writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds to wait after a trip that had to be SAMPLED. "
                f"Defaults to {_DEFAULT_DELAY_S}; 0 disables the pause. A "
                f"copied trip never waits."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "Stop after this many trips. Defaults to 0, meaning every "
                "candidate — a first small batch is the reason this exists."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Fill in every candidate trip.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any trip failed, so cron/CI sees non-zero.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]

        # Streamed, not materialised: Trip is a growable user table.
        # ``select_related`` because the copy path reads the source route
        # for all but a handful of rows.
        # Two kinds of candidate, as the routes command has: a null is a
        # trip nothing has sampled, and a record with no ``cruxes`` key is
        # one written before SNOW-911, which draws its colours but none of
        # its markers. A record carrying the key is current even when the
        # list inside is empty — that is "nothing was flagged".
        candidates = Trip.objects.filter(
            Q(slope_samples__isnull=True) | ~Q(slope_samples__has_key="cruxes")
        ).select_related("route")
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Filling in the terrain under {total} trip(s){flag_label}"
            )
        )
        logger.info(
            "backfill_trip_slope_samples started: candidates=%d commit=%s "
            "delay=%s limit=%s",
            total,
            commit,
            delay,
            limit,
        )

        counts = {"copied": 0, "sampled": 0, "unanswered": 0, "failed": 0}
        processed = 0
        for trip in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.uuid}",
        ):
            walked = self._backfill_one(trip, counts, commit=commit)
            processed += 1
            # Checked AFTER the work, for the reason the routes backfill
            # states: ``iterate_rows`` prints its countdown as it yields,
            # so breaking on the way in would count a row down and then
            # discard it.
            if limit and processed >= limit:
                break
            # Only a row that actually asked the origin something waits.
            if commit and delay and walked:
                time.sleep(delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_trip_slope_samples completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _inherited_record(self, trip: Trip) -> dict[str, Any] | None:
        """Return the source route's record when it describes THIS track.

        Three conditions, and each is load-bearing: the route must still
        exist (``Trip.route`` is provenance and may be null), it must have
        been sampled, and its geometry must be identical to the snapshot's
        — a trip may outlive its route, and a record copied off a
        different track would paint one track's steepness onto another.

        Args:
            trip: The trip being filled in.

        Returns:
            The record to copy, or None when the trip has to be walked.

        """
        route = trip.route
        if route is None or route.slope_samples is None:
            return None
        if route.points != trip.points:
            return None
        # A record the route itself has not brought up to date is not
        # worth inheriting: the trip would still be missing the key this
        # command selects on, so it would be re-copied on every run and
        # never converge. Walking answers it once.
        if "cruxes" not in route.slope_samples:
            return None
        record: dict[str, Any] = route.slope_samples
        return record

    def _backfill_one(
        self, trip: Trip, counts: dict[str, int], *, commit: bool
    ) -> bool:
        """Fill in one trip, by copy where possible and by walk otherwise.

        ``save(update_fields=…)`` writes the one column and nothing else:
        the row is the organiser's, and this command has no business
        touching their name for the day or its meeting point.

        Args:
            trip: The trip to fill in.
            counts: The running tally, mutated in place.
            commit: Whether to make the requests and persist.

        Returns:
            True when the tile origin was asked something, which is what
            the caller's pacing answers to.

        """
        inherited = self._inherited_record(trip)

        if not commit:
            # No request either, not merely no write — a read-only run
            # must not spend the origin's bandwidth. The split is still
            # reported, because "how many of these need the origin at all"
            # is the figure an operator is previewing for.
            counts["copied" if inherited is not None else "sampled"] += 1
            return False

        if inherited is not None:
            trip.slope_samples = inherited
            trip.save(update_fields=["slope_samples", "updated_at"])
            counts["copied"] += 1
            return False

        try:
            samples = build_slope_samples(trip.points, f"trip pk={trip.pk}")
        except Exception:  # noqa: BLE001 — broad catch intentional: one trip must not abort the batch
            logger.exception(
                "backfill_trip_slope_samples: failed on trip id=%s uuid=%s",
                trip.pk,
                trip.uuid,
            )
            counts["failed"] += 1
            return True

        if samples is None:
            # Left null on purpose — null is "never sampled", and a later
            # run retries it. Not a failure.
            counts["unanswered"] += 1
            return True

        trip.slope_samples = samples
        trip.save(update_fields=["slope_samples", "updated_at"])
        counts["sampled"] += 1
        return True

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
                        f"Done. {counts['copied']} trip(s) inherited their "
                        f"route's record, {counts['sampled']} sampled, "
                        f"{counts['unanswered']} left null (nothing "
                        f"learnable), {counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['copied']} "
                        f"trip(s) would inherit their route's record and "
                        f"{counts['sampled']} would be sampled. No requests "
                        "made, no data written. Pass --commit to fill them in."
                    )
                )
        logger.info(
            "backfill_trip_slope_samples finished: copied=%d sampled=%d "
            "unanswered=%d failed=%d commit=%s",
            counts["copied"],
            counts["sampled"],
            counts["unanswered"],
            counts["failed"],
            commit,
        )
