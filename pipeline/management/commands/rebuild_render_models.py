"""
pipeline/management/commands/rebuild_render_models.py — Management command.

Rebuilds the ``render_model`` JSONField on Bulletin rows whose
``render_model_version`` is older than the current ``RENDER_MODEL_VERSION``
constant, or on a specific bulletin, or on all bulletins.

Typical use after a builder logic change::

    python manage.py rebuild_render_models

Backfill everything::

    python manage.py rebuild_render_models --all

Single bulletin::

    python manage.py rebuild_render_models --bulletin-id <id>

Dry run (build but do not write)::

    python manage.py rebuild_render_models --dry-run
"""

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Bulletin
from pipeline.services.render_model import RENDER_MODEL_VERSION, build_render_model

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


class Command(BaseCommand):
    """Rebuild the render_model field on stale or specified Bulletin rows."""

    help = (
        "Rebuild render_model on Bulletin rows that are stale "
        "(render_model_version < RENDER_MODEL_VERSION)."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build render models but do not write them to the database.",
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

    def _process_bulletin(self, bulletin: Bulletin, dry_run: bool) -> tuple[bool, int]:
        """
        Build and optionally write the render model for one bulletin.

        Args:
            bulletin: The Bulletin to rebuild.
            dry_run: If True, do not write to the database.

        Returns:
            A ``(success, new_version)`` tuple. ``success`` is False on error.

        """
        props = bulletin.raw_data.get("properties", {}) if bulletin.raw_data else {}
        try:
            new_render_model = build_render_model(props)
            new_version = RENDER_MODEL_VERSION
            success = True
        except Exception as exc:
            logger.error(
                "Failed to build render model for bulletin %s: %s",
                bulletin.bulletin_id,
                exc,
                exc_info=True,
            )
            new_render_model = {
                "version": 0,
                "traits": [],
                "error": str(exc),
            }
            new_version = 0
            success = False

        if dry_run:
            logger.info(
                "[dry-run] Would update bulletin %s render_model_version=%d",
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
        """Execute the command."""
        dry_run: bool = options["dry_run"]
        rebuild_all: bool = options["rebuild_all"]
        bulletin_id_arg: str | None = options["bulletin_id"]
        batch_size: int = options["batch_size"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding render models (version={RENDER_MODEL_VERSION})"
                + (" [ALL]" if rebuild_all else "")
                + (f" [bulletin-id={bulletin_id_arg}]" if bulletin_id_arg else "")
                + (" [DRY RUN]" if dry_run else "")
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
                success, _ = self._process_bulletin(bulletin, dry_run)
                rebuilt += 1
                if not success:
                    errored += 1

            offset += batch_size

        suffix = (f", {errored} error(s)" if errored else "") + "."
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete — would have rebuilt {rebuilt} bulletin(s)"
                    + suffix
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Done. Rebuilt {rebuilt} bulletin(s)" + suffix)
            )

        logger.info(
            "rebuild_render_models finished: rebuilt=%d errored=%d dry_run=%s",
            rebuilt,
            errored,
            dry_run,
        )
