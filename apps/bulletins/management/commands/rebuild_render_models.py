"""
apps/bulletins/management/commands/rebuild_render_models.py — Management command.

Rebuilds the ``render_model`` JSONField on Bulletin rows whose
``render_model_version`` is older than the current ``RENDER_MODEL_VERSION``
constant, or on a specific bulletin, or on all bulletins. After a successful
render-model rebuild it also refreshes the RegionDayRating rows for every
(region, day) covered by the rebuilt bulletins (pass ``--skip-day-ratings``
to suppress this step).

Streams the queryset newest-id-first via ``apps.core.command_iteration
.iterate_rows`` rather than hand-rolled OFFSET/LIMIT batching (SNOW-602) —
the old batching re-queried an offset slice of the *same* filtered queryset
on every page, which is O(n²) and can skip rows when the loop body mutates
the column the queryset filters on. The (region, day) pairs touched by a
successful rebuild are accumulated into a bounded ``set`` as each bulletin is
processed, rather than collecting the full ``Bulletin`` instances in a list
held until the end of the run.

Read-only by default — pass ``--commit`` to persist changes (per the
project-wide management command convention).

Typical use after a builder logic change::

    # Read-only walk over stale rows (counts what would change).
    python manage.py rebuild_render_models

    # Persist.
    python manage.py rebuild_render_models --commit

Backfill everything::

    python manage.py rebuild_render_models --all --commit

Single bulletin::

    python manage.py rebuild_render_models --bulletin-id <id> --commit

Skip day-rating refresh::

    python manage.py rebuild_render_models --commit --skip-day-ratings
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import day_rating_pairs, refresh_day_ratings
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from apps.core.command_iteration import iterate_rows
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

_PREFETCH_CHUNK_SIZE = 500


class Command(BaseCommand):
    """Rebuild the render_model field on stale or specified Bulletin rows."""

    help = (
        "Rebuild render_model on Bulletin rows that are stale "
        "(render_model_version < RENDER_MODEL_VERSION), then refresh "
        "RegionDayRating rows for every covered (region, day). "
        "Read-only unless --commit is passed. "
        "Pass --skip-day-ratings to suppress the day-rating refresh step."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist rebuilt render models to the database. "
                "Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="Rebuild every bulletin, not just stale ones.",
        )
        parser.add_argument(
            "--bulletin-id",
            metavar="ID",
            help="Rebuild a single bulletin by its bulletin_id.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_PREFETCH_CHUNK_SIZE,
            metavar="N",
            help=(
                "Chunk size for the streamed queryset iterator "
                f"(default: {_PREFETCH_CHUNK_SIZE})."
            ),
        )
        parser.add_argument(
            "--skip-day-ratings",
            action="store_true",
            help="Skip the RegionDayRating refresh step after rebuilding.",
        )

    def _build_queryset(self, bulletin_id_arg: str | None, rebuild_all: bool) -> Any:
        """
        Build the queryset of bulletins to rebuild.

        Args:
            bulletin_id_arg: Optional bulletin_id to restrict to a single row.
            rebuild_all: If True, return all bulletins.

        Returns:
            A Bulletin queryset.

        Raises:
            CommandError: If bulletin_id_arg is specified but not found.

        """
        if bulletin_id_arg:
            qs = Bulletin.objects.filter(bulletin_id=bulletin_id_arg)
            if not qs.exists():
                raise CommandError(
                    f"No bulletin found with bulletin_id={bulletin_id_arg!r}"
                )
            return qs.prefetch_related("regions")
        if rebuild_all:
            return Bulletin.objects.all().prefetch_related("regions")
        return Bulletin.objects.needs_render_model_rebuild(
            RENDER_MODEL_VERSION
        ).prefetch_related("regions")

    def _process_bulletin(
        self, bulletin: Bulletin, *, commit: bool
    ) -> tuple[bool, int]:
        """
        Build and optionally write the render model for one bulletin.

        Args:
            bulletin: The Bulletin to rebuild.
            commit: If False, build the model but do not write to the database.

        Returns:
            A ``(success, new_version)`` tuple. ``success`` is False on error.

        """
        props = bulletin.raw_data.get("properties", {}) if bulletin.raw_data else {}
        try:
            new_render_model = build_render_model(props)
            new_version = RENDER_MODEL_VERSION
            success = True
        except RenderModelBuildError as exc:
            logger.exception(
                "Failed to build render model for bulletin %s: %s",
                bulletin.bulletin_id,
                exc,
            )
            new_render_model = {
                "version": 0,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
            new_version = 0
            success = False

        if not commit:
            logger.info(
                "[read-only] Would update bulletin %s render_model_version=%d",
                bulletin.bulletin_id,
                new_version,
            )
        else:
            Bulletin.objects.filter(pk=bulletin.pk).update(
                render_model=new_render_model,
                render_model_version=new_version,
            )
        return success, new_version

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the rebuild command.

        Default behaviour: rebuilds all Bulletin rows whose
        ``render_model_version`` is less than ``RENDER_MODEL_VERSION``,
        streamed newest-id-first via ``iterate_rows`` in chunks of
        ``--batch-size`` (default 500). Read-only unless ``--commit`` is
        passed. After rebuild, refreshes RegionDayRating for every covered
        (region, day) unless ``--skip-day-ratings`` is passed — the pairs are
        accumulated into a bounded set as each bulletin is processed, not
        collected as a list of full ``Bulletin`` instances.

        Flags:
            --commit: Persist rebuilt models to the database.
            --all: Rebuild every bulletin regardless of stored version.
            --bulletin-id: Rebuild a single bulletin by its bulletin_id.
            --batch-size N: Chunk size for the streamed queryset iterator.
            --skip-day-ratings: Skip the RegionDayRating refresh step.

        """
        commit: bool = options["commit"]
        rebuild_all: bool = options["rebuild_all"]
        bulletin_id_arg: str | None = options["bulletin_id"]
        chunk_size: int = options["batch_size"]
        skip_day_ratings: bool = options["skip_day_ratings"]
        verbosity: int = options["verbosity"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding render models (version={RENDER_MODEL_VERSION})"
                + (" [ALL]" if rebuild_all else "")
                + (f" [bulletin-id={bulletin_id_arg}]" if bulletin_id_arg else "")
                + ("" if commit else " [READ-ONLY]")
            )
        )

        qs = self._build_queryset(bulletin_id_arg, rebuild_all)

        total = qs.count()
        self.stdout.write(f"Bulletins to process: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        rebuilt = 0
        errored = 0
        pairs: set[tuple[MicroRegion, date]] = set()

        for bulletin in iterate_rows(
            self,
            qs,
            verbosity=verbosity,
            chunk_size=chunk_size,
            describe=lambda b: b.bulletin_id,
        ):
            success, _ = self._process_bulletin(bulletin, commit=commit)
            rebuilt += 1
            if not success:
                errored += 1
            elif commit and not skip_day_ratings:
                pairs |= day_rating_pairs([bulletin])

        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Read-only run complete — would have rebuilt {rebuilt} "
                    f"bulletin(s), {errored} would have failed. "
                    f"Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Rebuilt {rebuilt} bulletin(s), {errored} failed.")
            )

        logger.info(
            "rebuild_render_models finished: rebuilt=%d errored=%d commit=%s",
            rebuilt,
            errored,
            commit,
        )

        # Refresh day ratings for all (region, day) pairs covered by
        # successfully rebuilt bulletins.
        rating_failures = 0
        if commit and not skip_day_ratings and pairs:
            self.stdout.write(
                f"Refreshing day ratings for {len(pairs)} (region, day) pair(s)."
            )
            # A failed recompute counts towards the exit code, matching the
            # other three bulletin-rewriting commands (design rule 5).
            rating_failures = refresh_day_ratings(pairs)
            self.stdout.write(self.style.SUCCESS("Day ratings refreshed."))

        if errored > 0 or rating_failures > 0:
            raise CommandError(
                f"{errored} bulletin(s) failed render-model rebuild "
                f"(stored with version=0 error sentinels); "
                f"{rating_failures} (region, day) pair(s) failed to recompute."
            )
