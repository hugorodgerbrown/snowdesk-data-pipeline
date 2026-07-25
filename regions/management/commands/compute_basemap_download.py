"""
compute_basemap_download — precompute per-region offline-basemap tile coverage.

Populates ``basemap_download`` on every ``MajorRegion``/``SubRegion``/
``MicroRegion`` row via ``regions.services.basemap_tiles.build_blob``, at
each tier's zoom band (``TIER_BANDS``). Region boundaries are static
reference data and the basemap tile grid never changes, so this is a
pure function of geometry the command can safely recompute in full on
every run — there is no incremental/dirty-tracking state to maintain.

L1 (``MajorRegion``) and L2 (``SubRegion``) already store a derived
``bbox`` (from ``refresh_eaws_fixtures``); L4 (``MicroRegion``) has no
``bbox`` field, so its bbox is derived on the fly from ``boundary`` via
``regions.services.basemap_tiles.bbox_from_boundary``. A region with
neither ``bbox`` nor ``boundary`` is skipped and counted as a failure —
there's nothing to compute tile coverage from.

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

    # Restrict to one tier (default: all three).
    uv run python manage.py compute_basemap_download --commit --level micro
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any, TypeVar

from django.core.management.base import BaseCommand, CommandError

from regions.models import MajorRegion, MicroRegion, SubRegion
from regions.services.basemap_tiles import TIER_BANDS, bbox_from_boundary, build_blob

logger = logging.getLogger(__name__)

_LEVEL_CHOICES = ("major", "minor", "micro")

_RegionT = TypeVar("_RegionT", MajorRegion, SubRegion, MicroRegion)


class Command(BaseCommand):
    """Recompute basemap_download on every region tier. Read-only unless --commit."""

    help = (
        "Precompute per-region offline-basemap tile coverage "
        "(regions.services.basemap_tiles) on MajorRegion/SubRegion/MicroRegion. "
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
        parser.add_argument(
            "--level",
            choices=_LEVEL_CHOICES,
            default=None,
            help=(
                "Restrict the run to one tier (major|minor|micro). Default: "
                "all three tiers."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Recompute basemap_download for the requested tier(s)."""
        commit: bool = options["commit"]
        level: str | None = options["level"]
        verbosity: int = options["verbosity"]

        levels = [level] if level else list(_LEVEL_CHOICES)

        flag_label = "" if commit else " [READ-ONLY]"
        tiers = ", ".join(levels)
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Computing basemap_download for tier(s) {tiers}{flag_label}"
            )
        )

        totals = {"updated": 0, "unchanged": 0, "failed": 0}
        if "major" in levels:
            _accumulate(totals, _compute_tier(MajorRegion, "major", commit, verbosity))
        if "minor" in levels:
            _accumulate(totals, _compute_tier(SubRegion, "minor", commit, verbosity))
        if "micro" in levels:
            _accumulate(totals, _compute_tier(MicroRegion, "micro", commit, verbosity))

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
            "compute_basemap_download finished: levels=%s updated=%d "
            "unchanged=%d failed=%d commit=%s",
            levels,
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


def _accumulate(totals: dict[str, int], counts: dict[str, int]) -> None:
    """Add ``counts`` into ``totals`` in place."""
    for key in totals:
        totals[key] += counts[key]


def _compute_tier(
    model: type[_RegionT], tier: str, commit: bool, verbosity: int
) -> dict[str, int]:
    """Recompute basemap_download for every region in one tier.

    Args:
        model: The Django model for this tier (MajorRegion/SubRegion/MicroRegion).
        tier: One of ``"major"``, ``"minor"``, ``"micro"`` — indexes ``TIER_BANDS``.
        commit: Whether to persist the computed blob.
        verbosity: Django's ``--verbosity`` level.

    Returns:
        Counts dict with ``updated``, ``unchanged``, and ``failed`` keys.

    """
    min_z, max_z = TIER_BANDS[tier]
    counts = {"updated": 0, "unchanged": 0, "failed": 0}
    to_save: list[_RegionT] = []

    for region in model.objects.all().iterator():
        label = _region_label(region)
        bbox = _try_region_bbox(region, tier, label)
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
                "compute_basemap_download: %s tier=%s count=%d mb=%d",
                label,
                tier,
                blob["count"],
                blob["mb"],
            )
        if commit:
            region.basemap_download = blob
            to_save.append(region)

    if commit and to_save:
        model.objects.bulk_update(to_save, ["basemap_download"], batch_size=200)

    return counts


def _region_label(region: MajorRegion | SubRegion | MicroRegion) -> str:
    """Return a human-readable identifier for a region, for logging."""
    if isinstance(region, MicroRegion):
        return region.region_id
    return region.prefix


def _try_region_bbox(
    region: MajorRegion | SubRegion | MicroRegion, tier: str, label: str
) -> list[float] | None:
    """Resolve a region's bbox, logging and returning None on failure.

    Args:
        region: A MajorRegion, SubRegion, or MicroRegion instance.
        tier: The tier being processed, for the log message.
        label: The region's human-readable identifier, for the log message.

    Returns:
        ``[west, south, east, north]``, or ``None`` if neither ``bbox`` nor
        ``boundary`` is available.

    """
    bbox = getattr(region, "bbox", None)
    if bbox:
        return list(bbox)
    boundary = region.boundary
    if boundary:
        try:
            return bbox_from_boundary(boundary)
        except KeyError, ValueError, TypeError:
            logger.exception(
                "compute_basemap_download: failed to derive bbox for %s tier=%s",
                label,
                tier,
            )
            return None
    logger.warning(
        "compute_basemap_download: %s tier=%s has neither bbox nor boundary — skipping",
        label,
        tier,
    )
    return None
