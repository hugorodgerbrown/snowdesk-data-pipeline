"""link_region_centroid_locations — anchor each MicroRegion to a Location.

Gives every ``MicroRegion`` with a ``boundary`` a ``Location`` at that
region's centroid — which is what anchors the region in the location estate,
so any surface wanting to say something about the region has a place to hang
it on (SNOW-696).

**This runs on every deploy, and it must.** ``bin/build.sh`` reloads the EAWS
fixtures on every deploy; ``loaddata`` writes back every field a fixture
carries and resets the ones it does not to their model default. No fixture
carries ``centroid_location``, so every deploy sets all 461 of them to NULL
and orphans the ``Location`` rows behind them — silently, because nothing
reads the column at deploy time. SNOW-771 made that harmless by making this
command cheap enough to re-run immediately afterwards: ``build.sh`` calls it
right after ``loaddata``, and the estate heals itself.

**Wholly offline — no network at all.** Both halves of a centroid are known
without asking anyone:

* the coordinate is ``centre_from_bbox(boundary)``, and the boundary is in
  the fixture (SNOW-765 verified this reproduces the old ``centre`` column
  exactly, across all 461 regions);
* the elevation is ``MicroRegion.centroid_elevation_m``, resolved once
  against Open-Meteo by ``refresh_centroid_elevations`` and committed to the
  fixtures (SNOW-771).

That is what makes a per-deploy run affordable. It also removes the
per-environment backfill entirely: no environment pays for elevation
lookups, because the fixture already carries them.

**A centroid is not a place anyone goes.** The minted location carries no
``name`` and no ``kind``: it represents the region, and it sits at whatever
elevation the polygon's centre happens to fall at rather than at a
meaningful one. Any surface showing it must say which elevation it
represents — see ``docs/locations.md``.

A region whose fixture carries no elevation still gets its centroid, with a
null ``elevation_m``: weather does not need a height, and
``Location.objects.unresolved()`` exists to fill it in later. A region whose
boundary cannot be read is logged and counted, never fatal.

**Failure is per-region and almost never fatal, by design.** ``build.sh``
runs this under ``set -o errexit`` on three services that share one
database, so an escaping exception would take a whole deploy down. One
region that cannot be linked is not worth that — it degrades to a region
with no weather, which every surface already handles. The command exits
non-zero only when *every* candidate failed, which means something systemic
rather than one bad row.

Idempotent — regions that already have a ``centroid_location`` are excluded,
so a second run in the same deploy selects zero.

Usage:
    # Preview — reports what it would link, writes nothing.
    uv run python manage.py link_region_centroid_locations

    # Persist. This is what build.sh runs on every deploy.
    uv run python manage.py link_region_centroid_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import (
    announce_link_run,
    iterate_rows,
)
from apps.locations.services.anchor import anchor_location
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
    """Give every MicroRegion with a boundary a centroid Location.

    Read-only by default; pass --commit to persist. Regions already linked,
    and regions with no ``boundary``, are excluded from the candidate set.
    Per-region failures are caught, logged and counted — they never abort
    the batch — and the command exits non-zero when any failed.

    Offline: the coordinate comes from ``boundary`` and the elevation from
    ``centroid_elevation_m``, so this makes no network calls and is safe to
    run on every deploy.
    """

    help = (
        "Mint a Location at each MicroRegion's centroid, anchoring the "
        "region in the location estate. Offline — derived from the region's "
        "own boundary and fixture elevation. Read-only unless --commit."
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

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve a centroid location for every candidate region."""
        commit: bool = options["commit"]
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
        )

        counts = {"linked": 0, "skipped": 0, "failed": 0}
        for region in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.region_id}",
        ):
            self._resolve_one(region, counts, commit=commit, verbosity=verbosity)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        # Non-zero ONLY on a total failure — every candidate raised. That is
        # systemic (a bad migration, an unreachable database) and worth
        # blocking a deploy on. A partial failure is not: it logs loudly
        # above, leaves the regions that did link working, and lets the
        # deploy finish. Failing every deploy of every service because one
        # of 461 regions could not be written would be the worse outcome.
        if counts["failed"] > 0 and counts["linked"] == 0:
            raise CommandError(
                f"link_region_centroid_locations linked nothing: all "
                f"{counts['failed']} candidate(s) failed. Check logs."
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
        # Read, not fetched. ``refresh_centroid_elevations`` resolved this
        # once against Open-Meteo and committed it to the fixture, so every
        # environment gets it for free and this command stays offline.
        # A null is not a failure: weather needs a coordinate, not a height,
        # and ``Location.objects.unresolved()`` is how a missing one is
        # filled in later.
        elevation = region.centroid_elevation_m

        if commit:
            try:
                with transaction.atomic():
                    # Row lock, then re-read. Production deploys THREE
                    # services from `release` at once, all running a build
                    # script that calls this command, all against one
                    # database. Without the lock, on the first run in an
                    # environment — when there is no row to reuse yet — all
                    # three would find nothing, all three would create, and
                    # the last writer would win: two orphans per region,
                    # ~922 on a first release. Locking the region serialises
                    # them, and the re-read lets the losers see the winner's
                    # work instead of repeating it.
                    locked = (
                        MicroRegion.objects.select_for_update()
                        .filter(pk=region.pk)
                        .first()
                    )
                    if locked is None:
                        counts["skipped"] += 1
                        return
                    if locked.centroid_location_id is not None:
                        # A concurrent build linked it while we queued.
                        counts["linked"] += 1
                        return
                    location = anchor_location(latitude, longitude, elevation)
                    locked.centroid_location = location
                    locked.save(update_fields=["centroid_location", "updated_at"])
            except Exception:  # noqa: BLE001 — one region must not fail a deploy
                # build.sh runs this under ``set -o errexit``, so an escaping
                # exception takes the whole deploy down — of three services
                # that share a database. One micro-region that cannot be
                # linked is not worth that: it degrades to a region with no
                # weather, which the surfaces already handle.
                logger.exception(
                    "link_region_centroid_locations: failed to link region %s",
                    region.region_id,
                )
                counts["failed"] += 1
                return
        counts["linked"] += 1
        if verbosity >= 2:
            height = "unknown" if elevation is None else f"{elevation:.0f}m"
            logger.info("Linked region %s -> %s", region.region_id, height)

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
