"""render_ski_tours — the service walks and draws every SAC tour, once.

SNOW-1316, replacing SNOW-1288's ``backfill_ski_tour_slope_samples``. A
tour's walk (the terrain under its line) and its drawing (its legs, slope
and point-card rows) are deterministic from SAC's line and the terrain
model, so the reference-data service makes them once and every instance
copies them from the v2 ``ski-tours`` feed, rather than each walking 2,941
tours itself. Runs where the service runs: production.

**Candidates** are ``tours_to_render()``: a ``SourceSkiTour`` never walked
from its current document and terrain model (a fresh import, or one the
weekly sync rewrote, which clears its walk), or one whose drawing a
builder change has made stale. A stale drawing is redrawn from the walk it
holds, with no request.

**Idempotent.** Each tour carries the fingerprint of what it was walked
from and its drawing the fingerprint of what it was drawn from; a tour
whose both match is not even a candidate, so a second run writes nothing.

**An outage stores nothing.** A tour the tile origin does not answer for is
counted ``unavailable`` and tried again next run, and the run exits
non-zero; three in a row is the origin down, and the run stops there.
No terrain grid at all is the same. A line too short to walk is ``no_terrain``, settled.

**It makes real outbound requests, so it paces itself** — a second between
walked tours by default, politeness towards the tile origin.

Then, on ``--commit``, this host's own ``SkiTour`` rows take what changed
(``apply_from_service``), as every other instance's do through
``pull_reference_data``.

Usage:
    # Preview — how many tours would be walked or redrawn; no request.
    uv run python manage.py render_ski_tours

    # Walk, draw and keep.
    uv run python manage.py render_ski_tours --commit

    # A first batch, to watch it.
    uv run python manage.py render_ski_tours --commit --limit 20
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import (
    RenderOutcome,
    SourceSkiTour,
    render_source_tour,
    terrain_grid,
    tour_summits,
    tours_to_render,
)
from apps.routes.services.ski_network import FAILED, OUTCOMES, apply_from_service

logger = logging.getLogger(__name__)

# Seconds between walked tours: politeness towards the tile origin.
_DEFAULT_DELAY_S = 1.0

# Unanswered tours in a row that mean the origin is down, not one tile.
_UNAVAILABLE_RUN_LIMIT = 3

# What a run counts, in the order it reports them.
COUNTS: tuple[str, ...] = (
    *(outcome.value for outcome in RenderOutcome),
    "failed",
)


class Command(RegisteredCommand):
    """Walk and draw every SAC tour the service has not, and apply them here.

    Read-only by default; pass --commit to walk and persist. Exits non-zero
    if any tour failed or the terrain origin was down.
    """

    help = (
        "Walk the terrain under every SAC ski tour the reference-data "
        "service has not walked, store its drawing, and apply the result to "
        "this instance. Read-only unless --commit."
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
                "Walk, draw and keep. Without this flag the command reports "
                "the candidates, makes no request and writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=_DEFAULT_DELAY_S,
            help=(
                f"Seconds to wait between walked tours. Defaults to "
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
        """Walk and draw every candidate tour, then apply them here.

        Args:
            args: Unused positional arguments.
            options: Parsed command-line options.

        Raises:
            CommandError: When any tour failed or the origin was down.

        """
        commit: bool = options["commit"]
        delay: float = options["delay"]
        limit: int = options["limit"]
        verbosity: int = options["verbosity"]
        counts: Counter[str] = Counter({name: 0 for name in COUNTS})

        grid = terrain_grid()
        if grid is None:
            self._report(counts, commit=commit)
            raise CommandError(
                "render_ski_tours: the terrain grid could not be loaded; "
                "nothing was walked."
            )

        candidates = tours_to_render(grid)
        total = candidates.count()
        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Rendering {total} SAC tour(s){flag_label}")
        )
        summits = tour_summits()
        unavailable_run = 0
        processed = 0
        for row in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=_describe,
        ):
            outcome = self._render_one(row, summits, grid, counts, commit=commit)
            processed += 1
            unavailable_run = (
                unavailable_run + 1 if outcome == RenderOutcome.UNAVAILABLE else 0
            )
            if unavailable_run >= _UNAVAILABLE_RUN_LIMIT:
                self.stderr.write(
                    f"{unavailable_run} tours in a row unanswered: the terrain "
                    "origin is down. Stopping."
                )
                break
            if limit and processed >= limit:
                break
            walked = outcome in (RenderOutcome.RENDERED, RenderOutcome.UNAVAILABLE)
            if commit and delay and walked:
                time.sleep(delay)

        if commit:
            self._apply_here(counts)
        self._report(counts, commit=commit)
        # An unanswered tour is a partial failure: it is retried next run,
        # but the scheduler and the register must see this run fell short.
        failures = counts["failed"] + counts[RenderOutcome.UNAVAILABLE.value]
        if failures:
            raise CommandError(
                f"render_ski_tours completed with {failures} failure(s) "
                f"({counts['failed']} failed, "
                f"{counts[RenderOutcome.UNAVAILABLE.value]} unavailable). "
                "Check logs for details."
            )

    def _render_one(
        self,
        row: SourceSkiTour,
        summits: dict[str, Any],
        grid: Any,
        counts: Counter[str],
        *,
        commit: bool,
    ) -> RenderOutcome | None:
        """Render one tour and count what it did.

        Args:
            row: The tour.
            summits: Every summit by key.
            grid: The terrain model's definition.
            counts: The running tally, mutated in place.
            commit: Whether to walk and persist.

        Returns:
            The outcome, or None when the tour failed.

        """
        try:
            outcome = render_source_tour(row, summits, grid, commit=commit)
        except Exception:  # noqa: BLE001 — broad catch intentional: one tour must not abort the batch
            logger.exception("render_ski_tours: failed on tour id=%s", row.pk)
            counts["failed"] += 1
            return None
        counts[outcome.value] += 1
        return outcome

    def _apply_here(self, counts: Counter[str]) -> None:
        """Apply what changed to this host's own ``SkiTour`` rows, and tally it.

        Args:
            counts: The running tally; a failed apply counts as a failure.

        """
        applied = apply_from_service(commit=True)
        for kind, outcomes in applied.items():
            for outcome in OUTCOMES:
                self.tally(f"applied_{kind}_{outcome}", outcomes[outcome])
            counts["failed"] += outcomes[FAILED]

    def _report(self, counts: Counter[str], *, commit: bool) -> None:
        """Emit the post-run summary to stdout, the register and the log.

        Args:
            counts: The final tally.
            commit: Whether --commit was passed.

        """
        summary = ", ".join(f"{counts[name]} {name}" for name in COUNTS)
        verb = "Done" if commit else "Read-only run complete (no requests made)"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {summary}."))
        for name in COUNTS:
            self.tally(name, counts[name])
        logger.info("render_ski_tours finished: commit=%s %s", commit, dict(counts))


def _describe(row: SourceSkiTour) -> str:
    """Return a tour's label for the countdown: its SAC id and name."""
    document = row.document or {}
    return f"SAC {document.get('sac_id')} {document.get('name', '')}"
