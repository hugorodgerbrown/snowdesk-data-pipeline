"""backfill_ski_tour_slope_samples — walk the terrain under SAC's tours ahead of time.

SNOW-1288. A ``SkiTour``'s terrain record (``slope_samples``) is what its
panel's profile, legs and point card read. Until this command, the first
reader to open a tour walked it (``routes:ski_tour_feature?sample=1``),
which takes seconds on a long tour, so every tour opened slowly once. This
walks every tour that has no current record so that none does — a tour
opens with its profile at once, as a saved route does.

**Candidates** are ``tours_to_sample()``: a tour never walked (a fresh
import, or one the weekly sync rewrote, which clears the record) and a
tour walked over SNOW-1286's doubled line, which ``tour_slope_samples`` no
longer holds. The walk is ``tour_slope_samples(tour, sample=True)``, the
same one the panel's fallback runs, over SAC's own points (SNOW-1287).

**A record walked before SNOW-1306** (a way down now runs from its top) is
a candidate once. On a tour whose order did not change it is only marked,
counted ``marked``, with no request and no pause; on one whose order
flipped it is walked again.

**A record walked before SNOW-1310** has no legs stored. It is a candidate
once too, and is only cut (``cut_legs``), counted ``marked``, with no
request and no pause: the tours layer then draws it as its legs.

**Null stays honest**, as in ``backfill_route_slope_samples``: a tour the
terrain model has no answer for is left null, counted ``unanswered``, and
tried again next run. Not a failure.

**It makes real outbound requests, so it paces itself** — a second between
tours by default, politeness towards the tile origin. The first run over
the whole estate (2,941 tours) is a long one; the nightly
``reconcile_slope_samples`` job then finds only what a sync rewrote.

Usage:
    # Preview — how many tours would be walked; no request, no write.
    uv run python manage.py backfill_ski_tour_slope_samples

    # Walk and keep.
    uv run python manage.py backfill_ski_tour_slope_samples --commit

    # A first batch, to watch it.
    uv run python manage.py backfill_ski_tour_slope_samples --commit --limit 20
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.routes.models import SkiTour
from apps.routes.services.ski_tours import (
    needs_only_marking,
    tour_slope_samples,
    tours_to_sample,
)

logger = logging.getLogger(__name__)

# Seconds between tours: politeness towards the tile origin, as
# backfill_route_slope_samples' own pause is.
_DEFAULT_DELAY_S = 1.0


class Command(RegisteredCommand):
    """Walk the terrain under every SAC tour with no current record.

    Read-only by default; pass --commit to walk and persist. Exits non-zero
    if any tour failed, so a partial run is detectable.
    """

    help = (
        "Sample the terrain under every SAC ski tour with no current "
        "slope_samples, so its profile opens at once. Read-only unless --commit."
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
                "Walk the terrain and keep the records. Without this flag "
                "the command reports the candidates, makes no request and "
                "writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds to wait between tours. Defaults to "
                f"{_DEFAULT_DELAY_S}; 0 disables the pause."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many tours. Defaults to 0, meaning every candidate.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Walk every candidate tour.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any tour failed, so cron/CI sees non-zero.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]

        candidates = tours_to_sample()
        total = candidates.count()
        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Sampling the terrain under {total} SAC tour(s){flag_label}"
            )
        )
        logger.info(
            "backfill_ski_tour_slope_samples started: candidates=%d commit=%s "
            "delay=%s limit=%s",
            total,
            commit,
            delay,
            limit,
        )

        counts = {"sampled": 0, "marked": 0, "unanswered": 0, "failed": 0}
        processed = 0
        for tour in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"SAC {row.sac_id} {row.target_name} · {row.name}",
        ):
            walked = self._sample_one(tour, counts, commit=commit)
            processed += 1
            # After the work, as backfill_route_slope_samples does: a break
            # on the way in would count a row down and then skip it.
            if limit and processed >= limit:
                break
            if commit and delay and walked:
                time.sleep(delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)
        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_ski_tour_slope_samples completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _sample_one(
        self, tour: SkiTour, counts: dict[str, int], *, commit: bool
    ) -> bool:
        """Walk one tour and keep its record.

        Args:
            tour: The tour to walk.
            counts: The running tally, mutated in place.
            commit: Whether to make the requests and persist.

        Returns:
            Whether the terrain was walked (or would be), and so whether
            the pause between tours is owed.

        """
        if needs_only_marking(tour):
            if commit:
                tour_slope_samples(tour, sample=True)
            counts["marked"] += 1
            return False
        if not commit:
            # No request either: a read-only run reports what the queryset
            # already counted.
            counts["sampled"] += 1
            return True
        try:
            record = tour_slope_samples(tour, sample=True)
        except Exception:  # noqa: BLE001 — broad catch intentional: one tour must not abort the batch
            logger.exception(
                "backfill_ski_tour_slope_samples: failed on tour id=%s sac_id=%s",
                tour.pk,
                tour.sac_id,
            )
            counts["failed"] += 1
            return True
        if record is None:
            # Left null on purpose: a later run tries again.
            counts["unanswered"] += 1
            return True
        counts["sampled"] += 1
        return True

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout, the register and the log.

        Args:
            counts: The final tally.
            commit: Whether --commit was passed.
            verbosity: Django's --verbosity level.

        """
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['sampled']} tour(s) sampled, "
                        f"{counts['marked']} marked as matching their line, "
                        f"{counts['unanswered']} left null (nothing learnable), "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['sampled']} tour(s) "
                        f"would be sampled, {counts['marked']} marked. "
                        "No requests made, no data written. "
                        "Pass --commit to sample."
                    )
                )
        self.tally("sampled", counts["sampled"])
        self.tally("marked", counts["marked"])
        self.tally("unanswered", counts["unanswered"])
        self.tally("failed", counts["failed"])
        logger.info(
            "backfill_ski_tour_slope_samples finished: sampled=%d marked=%d "
            "unanswered=%d failed=%d commit=%s",
            counts["sampled"],
            counts["marked"],
            counts["unanswered"],
            counts["failed"],
            commit,
        )
