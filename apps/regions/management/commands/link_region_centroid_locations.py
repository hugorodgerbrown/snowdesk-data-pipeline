"""link_region_centroid_locations — anchor each MicroRegion to a Location.

Backfill for SNOW-696. Gives every ``MicroRegion`` with a ``boundary`` a
``Location`` at that region's centroid, with its elevation resolved — which
is what anchors the region in the location estate, so any surface wanting to
say something about the region has a place to hang it on.

The centroid is derived here from ``boundary`` rather than read from the
``centre`` column (SNOW-765). ``centre`` was itself computed from the same
polygon at fixture-build time, so this reproduces the stored value rather
than approximating it — verified across all 461 L4 regions in the four EAWS
fixtures. Reading ``centre`` instead would make the column an input to the
thing that replaces it, and nothing could then drop it; see SNOW-766.

**One Open-Meteo elevation call per region**, up to 461 of them across AT
(153), CH (149), IT (124) and FR (35). The ``--delay`` default paces the
run inside the free-tier rate limit. The call is made on the dry-run path
too — it is what proves the region *can* resolve — but nothing is written
(SNOW-719).

**A centroid is not a place anyone goes.** The minted location carries no
``name`` and no ``kind``: it represents the region, and it sits at whatever
elevation the polygon's centre happens to fall at rather than at a
meaningful one. Any surface showing it must say which elevation it
represents — see ``docs/locations.md``.

SNOW-762 stripped the weather app, and with it this command's forecast-cell
resolution half. What remains is location work: the centroid ``Location``
and its elevation. The weather rebuild (SNOW-757) decides for itself which
locations it polls.

A region that fails to resolve is logged and counted; it never aborts the
batch, and the command exits non-zero when any failed.

Idempotent — regions that already have a ``centroid_location`` are excluded,
so a second run selects zero.

Usage:
    # Preview — resolves and reports, writes nothing.
    uv run python manage.py link_region_centroid_locations

    # Persist.
    uv run python manage.py link_region_centroid_locations --commit

    # Loosen pacing between the per-region lookups (default 1.0s).
    uv run python manage.py link_region_centroid_locations --commit --delay 2
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import (
    announce_link_run,
    iterate_rows,
    non_negative_float,
)
from apps.locations.models import Location
from apps.locations.services.elevation import fetch_elevation
from apps.regions.fixture_utils import centre_from_bbox
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)


def _centre_of(region: MicroRegion) -> tuple[float, float] | None:
    """Derive a region's centroid as a ``(latitude, longitude)`` pair.

    Computed from ``MicroRegion.boundary`` with ``centre_from_bbox`` — the
    same function the four ``build_*_fixture`` commands use to compute the
    ``centre`` column, so this returns the stored value rather than an
    approximation of it.

    ``boundary`` is a JSONField, so its shape is not schema-guaranteed and
    ``centre_from_bbox`` raises on a geometry it cannot read. Those raises
    are converted to ``None`` here, which the caller reports as a skip: a
    malformed fixture row is a problem to surface, not a reason to fail the
    whole batch.

    Args:
        region: The region to read.

    Returns:
        The ``(latitude, longitude)`` pair, or ``None`` when the region's
        boundary cannot be read.

    """
    boundary = region.boundary
    if not isinstance(boundary, dict):
        return None
    try:
        centre = centre_from_bbox(boundary)
    except KeyError, TypeError, ValueError, IndexError:
        # Every way a malformed geometry can fail: no "type" key, a type
        # centre_from_bbox does not support, non-numeric or short
        # coordinate pairs, or an empty coordinate list.
        return None
    return float(centre["lat"]), float(centre["lon"])


class Command(BaseCommand):
    """Give every MicroRegion with a boundary a resolved centroid Location.

    Read-only by default; pass --commit to persist. Regions already linked,
    and regions with no ``boundary``, are excluded from the candidate set.
    Per-region failures are caught, logged and counted — they never abort
    the batch — and the command exits non-zero when any failed.
    """

    help = (
        "Mint a Location at each MicroRegion's centroid and resolve its "
        "elevation, anchoring the region in the location estate. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted Locations and the centroid_location "
                "FKs. Without this flag the command resolves (and reports) "
                "but writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive regions. "
                "Default 1.0 — paces the run inside Open-Meteo's free-tier "
                "rate limit. Pass 0 to disable pacing for a tiny count."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve a centroid location for every candidate region."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        # Bounded by the region fixture rather than by user activity, but
        # streamed anyway — the fixture is ~1,500 rows and growing as EAWS
        # adds countries.
        candidates = MicroRegion.objects.filter(
            centroid_location__isnull=True, boundary__isnull=False
        )
        total = candidates.count()

        announce_link_run(
            self,
            logger=logger,
            command_name="link_region_centroid_locations",
            banner=f"Resolving a centroid Location for {total} region(s)",
            candidate_count=total,
            commit=commit,
            delay=delay,
        )

        counts = {"linked": 0, "skipped": 0, "failed": 0}
        for index, region in enumerate(
            iterate_rows(
                self,
                candidates,
                verbosity=verbosity,
                describe=lambda row: f"{row.pk} {row.region_id}",
            )
        ):
            self._resolve_one(region, counts, commit=commit, verbosity=verbosity)
            if delay > 0 and index < total - 1:
                time.sleep(delay)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"link_region_centroid_locations completed with "
                f"{counts['failed']} region failure(s). Check logs for details."
            )

    def _resolve_one(
        self,
        region: MicroRegion,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Mint and resolve one region's centroid location.

        Args:
            region: The region to anchor.
            counts: The running tally, mutated in place.
            commit: Whether to persist.
            verbosity: Django's ``--verbosity`` level.

        """
        centre = _centre_of(region)
        if centre is None:
            # The queryset excludes a null ``boundary``, but not one
            # holding a shape this cannot read — that is a fixture problem
            # to surface rather than a failure to exit non-zero on.
            logger.warning(
                "link_region_centroid_locations: region %s has an unreadable "
                "boundary %r; skipped.",
                region.region_id,
                region.boundary,
            )
            counts["skipped"] += 1
            return

        latitude, longitude = centre
        try:
            # One external HTTP call — kept outside any transaction so a
            # slow or failing request never holds a DB lock. Made on the
            # dry-run path too: it is what proves the region can resolve.
            elevation = fetch_elevation(latitude, longitude)
        except Exception:  # noqa: BLE001 — broad catch intentional: one region must not abort the batch
            logger.exception(
                "link_region_centroid_locations: failed to resolve region %s",
                region.region_id,
            )
            counts["failed"] += 1
            return

        if commit:
            with transaction.atomic():
                # Anonymous: a centroid represents the region, not a place
                # anyone goes, and naming it would put it in the curated
                # estate that import_locations owns.
                location = Location.objects.create(
                    latitude=latitude,
                    longitude=longitude,
                    elevation_m=elevation,
                )
                region.centroid_location = location
                region.save(update_fields=["centroid_location", "updated_at"])
        counts["linked"] += 1
        if verbosity >= 2:
            logger.info(
                "Resolved region %s -> %.0fm",
                region.region_id,
                elevation,
            )

    def _report_outcome(
        self,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['linked']} region(s) linked, "
                        f"{counts['skipped']} skipped, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['linked']} "
                        f"region(s) would be linked, {counts['skipped']} "
                        f"skipped, {counts['failed']} failed. "
                        "No data written. Pass --commit to persist."
                    )
                )
        logger.info(
            "link_region_centroid_locations finished: linked=%d skipped=%d "
            "failed=%d commit=%s",
            counts["linked"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
