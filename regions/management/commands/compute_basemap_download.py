"""
compute_basemap_download — precompute per-region offline-basemap tile coverage.

Populates ``basemap_download`` on every ``MicroRegion`` row via
``regions.services.basemap_tiles.build_blob``, at the download's single
zoom band (``MICRO_BAND``). Region boundaries are static reference data
and the basemap tile grid never changes, so this is a pure function of
geometry the command can safely recompute in full on every run — there
is no incremental/dirty-tracking state to maintain.

``MicroRegion`` has no stored ``bbox`` field, so its bbox is derived on
the fly from ``boundary`` via
``regions.services.basemap_tiles.bbox_from_boundary``. A region with no
``boundary`` is skipped and counted as a failure — there's nothing to
compute tile coverage from.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation reports what would change and exits 0 without writing
anything. Wired into ``build.sh``'s post-deploy step, alongside the
other derived-data commands, so a boundary change (a rare hand-edit,
not part of ordinary deploys) is picked up automatically.

Usage:
    # Preview what would change (default — no writes).
    uv run python manage.py compute_basemap_download

    # Actually write the computed blobs.
    uv run python manage.py compute_basemap_download --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from regions.models import MicroRegion
from regions.services.basemap_tiles import MICRO_BAND, bbox_from_boundary, build_blob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Recompute basemap_download on every MicroRegion. Read-only unless --commit."""

    help = (
        "Precompute per-region offline-basemap tile coverage "
        "(regions.services.basemap_tiles) on MicroRegion. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the computed basemap_download blobs. Without this "
                "flag the command only reports what would change and "
                "writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Recompute basemap_download for every MicroRegion."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Computing basemap_download for MicroRegion{flag_label}"
            )
        )

        totals = _compute_micro_regions(commit, verbosity)

        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {totals['updated']} updated, "
                        f"{totals['unchanged']} unchanged, "
                        f"{totals['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {totals['updated']} region(s) "
                        f"would be updated, {totals['failed']} failed. "
                        "No data written. Pass --commit to persist."
                    )
                )

        logger.info(
            "compute_basemap_download finished: updated=%d unchanged=%d "
            "failed=%d commit=%s",
            totals["updated"],
            totals["unchanged"],
            totals["failed"],
            commit,
        )

        if totals["failed"] > 0:
            raise CommandError(
                f"compute_basemap_download completed with "
                f"{totals['failed']} region failure(s). Check logs for details."
            )


def _compute_micro_regions(commit: bool, verbosity: int) -> dict[str, int]:
    """Recompute basemap_download for every MicroRegion.

    Args:
        commit: Whether to persist the computed blob.
        verbosity: Django's ``--verbosity`` level.

    Returns:
        Counts dict with ``updated``, ``unchanged``, and ``failed`` keys.

    """
    min_z, max_z = MICRO_BAND
    counts = {"updated": 0, "unchanged": 0, "failed": 0}
    to_save: list[MicroRegion] = []

    for region in MicroRegion.objects.all().iterator():
        bbox = _try_region_bbox(region)
        if bbox is None:
            counts["failed"] += 1
            continue

        blob = build_blob(bbox, min_z, max_z)
        if region.basemap_download == blob:
            counts["unchanged"] += 1
            continue

        counts["updated"] += 1
        if verbosity >= 2:
            logger.info(
                "compute_basemap_download: %s count=%d mb=%d",
                region.region_id,
                blob["count"],
                blob["mb"],
            )
        if commit:
            region.basemap_download = blob
            to_save.append(region)

    if commit and to_save:
        MicroRegion.objects.bulk_update(to_save, ["basemap_download"], batch_size=200)

    return counts


def _try_region_bbox(region: MicroRegion) -> list[float] | None:
    """Resolve a MicroRegion's bbox from its boundary, or None on failure.

    Args:
        region: A MicroRegion instance.

    Returns:
        ``[west, south, east, north]``, or ``None`` if ``boundary`` is
        unset or malformed.

    """
    boundary = region.boundary
    if not boundary:
        logger.warning(
            "compute_basemap_download: %s has no boundary — skipping",
            region.region_id,
        )
        return None
    try:
        return bbox_from_boundary(boundary)
    except KeyError, ValueError, TypeError:
        logger.exception(
            "compute_basemap_download: failed to derive bbox for %s",
            region.region_id,
        )
        return None
