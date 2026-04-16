"""
pipeline/management/commands/rebuild_render_models.py — Management command.

Rebuilds the ``render_model`` JSONField on Bulletin rows whose
``render_model_version`` is older than the current ``RENDER_MODEL_VERSION``
constant, or on a specific bulletin, or on all bulletins. After a successful
render-model rebuild it also refreshes the RegionDayRating rows for every
(region, day) covered by the rebuilt bulletins (pass ``--skip-day-ratings``
to suppress this step).

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
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Bulletin
from pipeline.services.day_rating import recompute_region_day
from pipeline.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


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
            default=_DEFAULT_BATCH_SIZE,
            metavar="N",
            help=(
                f"Process bulletins in batches of N (default: {_DEFAULT_BATCH_SIZE})."
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
            return qs
        if rebuild_all:
            return Bulletin.objects.all()
        return Bulletin.objects.needs_render_model_rebuild(RENDER_MODEL_VERSION)

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
            logger.error(
                "Failed to build render model for bulletin %s: %s",
                bulletin.bulletin_id,
                exc,
                exc_info=True,
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
        processed in batches of ``--batch-size`` (default 500). Read-only
        unless ``--commit`` is passed. After rebuild, refreshes
        RegionDayRating for every covered (region, day) unless
        ``--skip-day-ratings`` is passed.

        Flags:
            --commit: Persist rebuilt models to the database.
            --all: Rebuild every bulletin regardless of stored version.
            --bulletin-id: Rebuild a single bulletin by its bulletin_id.
            --batch-size N: Override the default batch size.
            --skip-day-ratings: Skip the RegionDayRating refresh step.

        """
        commit: bool = options["commit"]
        rebuild_all: bool = options["rebuild_all"]
        bulletin_id_arg: str | None = options["bulletin_id"]
        batch_size: int = options["batch_size"]
        skip_day_ratings: bool = options["skip_day_ratings"]

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

        rebuilt, errored, rebuilt_bulletins = self._process_in_batches(
            qs, total, batch_size, commit
        )

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
        if commit and not skip_day_ratings and rebuilt_bulletins:
            self._refresh_day_ratings(rebuilt_bulletins)

        if errored > 0:
            raise CommandError(
                f"{errored} bulletin(s) failed render-model rebuild. "
                f"They are stored with version=0 error sentinels."
            )

    def _refresh_day_ratings(self, bulletins: list[Bulletin]) -> None:
        """
        Recompute RegionDayRating for every (region, day) touched by ``bulletins``.

        Deduplicates pairs before calling ``recompute_region_day`` so a region+day
        covered by several bulletins is only recomputed once.

        Args:
            bulletins: Bulletins whose day ratings should be refreshed.

        """
        from datetime import date

        from pipeline.services.day_rating import _target_day

        pairs: set[tuple[Any, date]] = set()
        for bulletin in bulletins:
            regions = list(bulletin.regions.all())
            day = _target_day(bulletin)
            for region in regions:
                pairs.add((region, day))

        self.stdout.write(
            f"Refreshing day ratings for {len(pairs)} (region, day) pairs."
        )
        for region, day in pairs:
            try:
                recompute_region_day(region, day, commit=True)
            except Exception:
                logger.exception(
                    "Failed to refresh day rating for region=%s day=%s",
                    region.region_id,
                    day,
                )
        self.stdout.write(self.style.SUCCESS("Day ratings refreshed."))

    def _process_in_batches(
        self, qs: Any, total: int, batch_size: int, commit: bool
    ) -> tuple[int, int, list[Bulletin]]:
        """
        Iterate the queryset in pk-ordered batches, processing each bulletin.

        Returns:
            A ``(rebuilt, errored, successfully_rebuilt_bulletins)`` tuple.
            ``successfully_rebuilt_bulletins`` contains only bulletins that
            succeeded (version > 0) so day ratings are not refreshed for
            error sentinels.

        """
        rebuilt = 0
        errored = 0
        rebuilt_bulletins: list[Bulletin] = []
        offset = 0

        while offset < total:
            batch_ids = list(
                qs.order_by("pk").values_list("pk", flat=True)[
                    offset : offset + batch_size
                ]
            )
            if not batch_ids:
                break

            batch = Bulletin.objects.filter(pk__in=batch_ids).order_by("pk")
            for bulletin in batch:
                success, _ = self._process_bulletin(bulletin, commit=commit)
                rebuilt += 1
                if not success:
                    errored += 1
                else:
                    rebuilt_bulletins.append(bulletin)

            offset += batch_size

        return rebuilt, errored, rebuilt_bulletins
