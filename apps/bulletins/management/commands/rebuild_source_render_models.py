"""
apps/bulletins/management/commands/rebuild_source_render_models.py — Service models.

The service's counterpart of ``rebuild_render_models`` (SNOW-1270). The
weather service builds a render model for every bulletin it stores
(``SourceBulletin.render_model``), indexes its target day and region codes,
and computes its day ratings (``SourceDayRating``) from them; it serves
all of it. After a builder change bumps ``RENDER_MODEL_VERSION``, or a
rule change bumps ``DAY_RATING_VERSION``, the stored values are stale: a
bulletin is rebuilt when it is next fetched, but the history is not, so
this command rebuilds it.

Two passes. The first walks the bulletins whose model predates the current
version, failed builds and never-indexed rows included (``--all`` walks
every row, for a builder fix that did not bump the version), rebuilding
each. The second recomputes the day ratings of every (region, day) a
rebuilt bulletin governed before or governs now, plus every rating
computed under an older ``DAY_RATING_VERSION``. A changed row's
``updated_at`` moves, so the feeds serve it again; a rebuild that comes out
the same writes nothing.

Run it once per source environment after deploying a version bump, and
once when SNOW-1270 first deploys: rows stored before it have no model, no
index and no ratings. Read-only unless ``--commit`` is passed.

Usage:
    uv run python manage.py rebuild_source_render_models           # preview
    uv run python manage.py rebuild_source_render_models --commit  # rebuild
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import (
    DAY_RATING_VERSION,
    REBUILD_FAILED,
    REBUILT,
    RENDER_MODEL_VERSION,
    SourceBulletin,
    SourceDayRating,
    rebuild_render_model,
    refresh_source_day_ratings,
)

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Rebuild the weather service's stored render models and day ratings."""

    help = (
        "Rebuild the render model the service stores for each bulletin whose "
        "model predates RENDER_MODEL_VERSION (or every bulletin, with --all), "
        "then the day ratings they touch and any computed under an older "
        "DAY_RATING_VERSION. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Save what is rebuilt. Without it the command counts it.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="Rebuild every bulletin, not only the stale ones.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Rebuild the stale bulletins, then the day ratings they touch."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        rows = SourceBulletin.objects.only(
            "pk",
            "bulletin_id",
            "provider",
            "caaml",
            "render_model",
            "render_model_version",
            "valid_from",
            "target_date",
            "region_codes",
        )
        if not options["rebuild_all"]:
            rows = rows.stale_render_model(RENDER_MODEL_VERSION)
        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding {rows.count()} service render model(s) at "
                f"version {RENDER_MODEL_VERSION}{label}"
            )
        )

        rebuilt = unchanged = failed = 0
        # Keys only (code, day), never rows: the walk can be the whole archive.
        touched: set[tuple[str, date]] = set()
        for row in iterate_rows(
            self, rows, verbosity=verbosity, describe=SourceBulletin.to_string
        ):
            outcome = rebuild_render_model(row, commit=commit, touched=touched)
            if outcome == REBUILT:
                rebuilt += 1
            elif outcome == REBUILD_FAILED:
                failed += 1
                self.stderr.write(f"  render model failed: {row}")
            else:
                unchanged += 1

        touched |= set(
            SourceDayRating.objects.stale(DAY_RATING_VERSION).values_list(
                "region_code", "date"
            )
        )
        # A preview saved no rebuilt bulletin, so its count reads the stored
        # ones: an estimate of the pairs, not of their new values.
        ratings = refresh_source_day_ratings(touched, commit=commit)

        self.tally("rebuilt", rebuilt)
        self.tally("unchanged", unchanged)
        self.tally("failed", failed)
        self.tally("day_ratings", ratings)
        if verbosity >= 1:
            verb = "Rebuilt" if commit else "Would rebuild"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {rebuilt}; {unchanged} unchanged; {failed} failed; "
                    f"{ratings} day rating(s) over {len(touched)} pair(s)."
                )
            )
        logger.info(
            "rebuild_source_render_models: rebuilt=%d unchanged=%d failed=%d "
            "day_ratings=%d commit=%s",
            rebuilt,
            unchanged,
            failed,
            ratings,
            commit,
        )
        if failed:
            raise CommandError(f"{failed} render model build(s) failed")
