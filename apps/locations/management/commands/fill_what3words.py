"""fill_what3words — give every Location its three word address.

Walks ``Location.objects.unaddressed()`` and converts each coordinate to
the three word address for the 3m square it falls in, storing it on
``Location.what3words`` (SNOW-881).

**The primary fill path, and the one that guarantees the estate is
complete.** Locations arrive from several directions — a curated resort
point, a region centroid, a saved favourite, a field observation, a trip's
meeting point — and only some of those mint through a service that already
reaches the network. This command is what catches the rest, and what fills
the estate that existed before any of them did.

**Why a command and not a page render.** SNOW-840 filled the column lazily,
on the first render of a trip page, because ``convert-to-3wa`` was believed
to be metered at 1,000 conversions a month and a backfill looked like
spending a year's allowance on squares nobody had asked for. It is not
metered — the allowance belongs to ``convert-to-coordinates``, which
Snowdesk never calls — so the rationing was protecting nothing while
putting a five-second timeout on a page render. The column now resolves out
of band exactly as ``elevation_m`` does, which is the same shape of field:
derived from the coordinate by an HTTP call that cannot ride on a model
save. See docs/decisions/what3words-addresses-are-stored-indefinitely.md.

**No API key is a supported state, not a failure**, and it is checked
BEFORE the walk rather than discovered during it. ``convert_to_3wa``
returns None both for "no key configured" and for "the conversion failed",
which is right for a page render — the answer either way is "no address" —
but wrong here: walking the estate without a key would count every row as
a failure and exit non-zero, so a scheduled run in an environment that has
simply not subscribed would alarm on every pass. The key is read once up
front and an empty one reports and returns cleanly.

The ``what3words`` waffle flag is not read here at all: it gates what a
page SHOWS, and a row filled while the flag is off is simply a row that is
ready when it is turned on.

**Failure is per-row and never aborts the batch.** A location that cannot
be converted keeps its null and shows a coordinate pair, which every
surface already handles. Failures are logged, counted and reported, and the
command exits non-zero if there were any, so a partial run is never
mistaken for a clean one.

Idempotent: the candidate set excludes rows that already carry an address,
so a second run selects zero. Nothing is ever re-converted — a stored
address encodes a fixed square and cannot go stale; a pin that MOVES has
its column cleared at the point of the move, which puts it back in the
candidate set on the next run.

Usage:
    # Preview — reports what it would convert, writes nothing.
    uv run python manage.py fill_what3words

    # Persist.
    uv run python manage.py fill_what3words --commit

    # Pace it harder against the upstream's rate limit.
    uv run python manage.py fill_what3words --commit --delay 1.0
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.command_iteration import (
    announce_link_run,
    iterate_rows,
    non_negative_float,
)
from apps.locations.models import Location
from apps.locations.services.what3words import convert_to_3wa

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fill Location.what3words for every location that lacks one.

    Read-only by default; pass --commit to persist. One ``convert-to-3wa``
    call per unaddressed location, paced by --delay. Per-row failures are
    caught, logged and counted — they never abort the batch — and the
    command exits non-zero when any failed.
    """

    help = (
        "Convert each Location's coordinate to its three word address and "
        "store it. Skips locations that already have one. Read-only unless "
        "--commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the converted addresses. Without this flag the "
                "command converts (and reports) but writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=0.2,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive conversions. "
                "Default 0.2 — conversions are unmetered, but a burst of "
                "several hundred is still a burst, and pacing them costs "
                "nothing on an out-of-band run."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Convert and store an address for every candidate location."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        candidates = Location.objects.unaddressed()
        total = candidates.count()

        # Before the walk, not during it — see the module docstring. An
        # environment with no subscription is a supported state, and the
        # only honest report for it is that nothing was attempted.
        #
        # The fake is checked alongside the key because ``convert_to_3wa``
        # tests it FIRST and answers without a key at all. Skipping on an
        # empty key alone would make this command the one place the local
        # fake does not work, which is exactly the environment it exists
        # for — no subscription, and someone trying to see the feature.
        faking = settings.WHAT3WORDS_FAKE and settings.DEBUG
        if not settings.WHAT3WORDS_API_KEY and not faking:
            logger.info("fill_what3words: no API key configured, nothing to do")
            if verbosity >= 1:
                self.stdout.write(
                    self.style.NOTICE(
                        f"WHAT3WORDS_API_KEY is not set — skipping "
                        f"{total} candidate(s). This is the expected state "
                        f"in an environment with no what3words subscription."
                    )
                )
            return

        announce_link_run(
            self,
            logger=logger,
            command_name="fill_what3words",
            banner=f"Resolving a three word address for {total} location(s)",
            candidate_count=total,
            commit=commit,
            delay=delay,
        )

        counts = {"filled": 0, "failed": 0}
        for location in iterate_rows(self, candidates, verbosity=verbosity):
            self._resolve_one(location, counts, commit=commit, delay=delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        # Non-zero on ANY failure, partial batches included — the command
        # contract in CLAUDE.md. The batch has already finished by here: a
        # failed row is logged, counted and stepped over.
        if counts["failed"] > 0:
            raise CommandError(
                f"fill_what3words: {counts['failed']} of {total} "
                f"candidate(s) failed to convert. Check logs."
            )

    def _resolve_one(
        self,
        location: Location,
        counts: dict[str, int],
        *,
        commit: bool,
        delay: float,
    ) -> None:
        """Convert one location's coordinate and store the result.

        Writes with ``update_fields`` so the walk touches the two address
        columns and nothing else — it cannot clobber a coordinate edit made
        while the command is running.

        NOT ``fill_what3words``, deliberately, even though that service
        function does the same thing for one row. It re-reads the column to
        decide whether to convert, which is a wasted check on a candidate
        set already filtered to rows without one, and it always writes —
        leaving this command no way to honour ``--commit``.

        Args:
            location: The location to resolve.
            counts: The running tally, mutated in place.
            commit: Whether to persist the result.
            delay: Seconds to sleep after the conversion.

        """
        words = convert_to_3wa(location.latitude, location.longitude)

        # Paced AFTER the call and before the next one, so a run of N rows
        # sleeps N times rather than N-1 — the difference is one delay on
        # a several-hundred-row walk, and the simpler shape is worth more
        # than saving it.
        if delay:
            time.sleep(delay)

        if words is None:
            # By row id, never by coordinate (SNOW-718): a lat/lon pair is
            # somebody's location and a log is the wrong place for one.
            logger.warning(
                "fill_what3words: no address for location id=%s", location.pk
            )
            counts["failed"] += 1
            return

        counts["filled"] += 1
        if not commit:
            return

        location.what3words = words
        location.what3words_fetched_at = timezone.now()
        location.save(
            update_fields=["what3words", "what3words_fetched_at", "updated_at"]
        )

    def _report_outcome(
        self,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Write the end-of-run summary and log line.

        Args:
            counts: The final tally.
            commit: Whether ``--commit`` was passed.
            verbosity: Django's ``--verbosity`` level.

        """
        logger.info(
            "fill_what3words finished: filled=%d failed=%d commit=%s",
            counts["filled"],
            counts["failed"],
            commit,
        )
        if verbosity < 1:
            return

        verb = "Filled" if commit else "Would fill"
        summary = f"{verb} {counts['filled']} location(s), {counts['failed']} failed"
        style = self.style.WARNING if counts["failed"] else self.style.SUCCESS
        self.stdout.write(style(summary))
        if not commit:
            self.stdout.write(
                self.style.NOTICE("Read-only run — pass --commit to persist.")
            )
