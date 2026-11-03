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

**It only enqueues (SNOW-1398).** One ``fill_what3words_task`` per
candidate, through ``apps.core.command_tasks.enqueue_each``, so a run
returns in seconds and is offered to the ops MCP server's ``run_command``.
A read-only run counts the candidates and converts nothing. ``--delay``
paces the tasks, and each also waits for it on the ``what3words`` slot.

**Failure is per-row and never aborts the batch.** A location that cannot
be converted keeps its null and shows a coordinate pair, which every
surface already handles. The failed task opens the what3words breaker, so
the tasks behind it leave their rows for the next run instead of failing
in turn; where tasks run inline the command exits non-zero, so a partial
run is never mistaken for a clean one.

Idempotent: the candidate set excludes rows that already carry an address,
so a second run selects zero. Nothing is ever re-converted — a stored
address encodes a fixed square and cannot go stale; a pin that MOVES has
its column cleared at the point of the move, which puts it back in the
candidate set on the next run.

Usage:
    # Preview — reports what it would convert, converts nothing.
    uv run python manage.py fill_what3words

    # Enqueue one task per location.
    uv run python manage.py fill_what3words --commit

    # Pace it harder against the upstream's rate limit.
    uv run python manage.py fill_what3words --commit --delay 1.0
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError

from apps.core.command_iteration import announce_link_run, non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.locations.models import Location
from apps.locations.services.what3words import fill_what3words_task

logger = logging.getLogger(__name__)

# What Location.to_string reads, and the pk the task is handed.
_LABEL_FIELDS = ("pk", "name", "kind", "latitude", "longitude", "elevation_m")


class Command(RegisteredCommand):
    """Enqueue a what3words fill for every location that lacks an address.

    Read-only by default; pass --commit to enqueue. One ``convert-to-3wa``
    call per unaddressed location, one task each, paced by --delay.
    """

    help = (
        "Enqueue a conversion of each unaddressed Location's coordinate to "
        "its three word address. Read-only unless --commit is passed."
    )

    # It only enqueues, so it finishes in seconds (SNOW-1398).
    mcp_runnable = True

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Enqueue one conversion per location. Without this flag the "
                "command reports the candidates and converts nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=0.2,
            metavar="SECONDS",
            help=(
                "Seconds between one conversion and the next. Default 0.2 — "
                "conversions are unmetered, but a burst of several hundred "
                "is still a burst, and pacing them costs nothing on an "
                "out-of-band run."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue an address fill for every candidate location."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        candidates = Location.objects.unaddressed().only(*_LABEL_FIELDS)
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

        counts = enqueue_each(
            self,
            candidates,
            fill_what3words_task,
            args=lambda row: (row.pk, delay),
            describe=Location.to_string,
            commit=commit,
            verbosity=verbosity,
            pace=delay,
        )
        logger.info(
            "fill_what3words finished: enqueued=%d already_queued=%d "
            "failed=%d commit=%s",
            counts.enqueued,
            counts.already_queued,
            counts.failed,
            commit,
        )
        if verbosity >= 1:
            verb = "Enqueued" if commit else "Would enqueue"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {counts.enqueued} location(s), "
                    f"{counts.already_queued} already queued."
                )
            )
            if not commit:
                self.stdout.write(
                    self.style.NOTICE("Read-only run — pass --commit to enqueue.")
                )

        # Non-zero on ANY failure, partial batches included — the command
        # contract in CLAUDE.md. Only an inline backend reports one here; a
        # queued task reports its own on the task result.
        if counts.failed:
            raise CommandError(
                f"fill_what3words: {counts.failed} of {total} "
                f"candidate(s) failed to convert. Check logs."
            )
