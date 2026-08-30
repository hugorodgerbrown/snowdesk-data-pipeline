"""refresh_centroid_elevations — resolve each region's centroid elevation once.

Fills ``centroid_elevation_m`` on every L4 entry in the four committed EAWS
fixtures. This is the **only** part of a region's centroid that cannot be
derived offline: the coordinate is ``centre_from_bbox(boundary)`` and the
boundary is already in the fixture, but the ground height at that point has
to be asked of Open-Meteo.

**Why it lives in the fixture at all** (SNOW-771). ``bin/build.sh`` reloads
these fixtures on every deploy, and ``loaddata`` writes back every field a
fixture carries — resetting the ones it does not to their model default.
``MicroRegion.centroid_location`` is one of those, so a deploy unlinks every
region and orphans the ``Location`` rows behind it. The fix is not to defend
the FK but to make it cheap to rebuild: with the elevation in the fixture,
``link_region_centroid_locations`` becomes a pure offline derivation that
``build.sh`` can re-run immediately after ``loaddata``, on every deploy, for
free. That turns a silent data-loss bug into a self-healing step.

The consequence for this command is that it is run **by a developer, once,
against the committed fixtures** — not per environment. Every environment
then gets the elevations for nothing.

Re-runnable: an entry that already carries an elevation is skipped, so a run
interrupted halfway costs only the regions it had not reached. Pass
``--force`` to re-resolve every entry — which is what a fixture rebuild that
moved a boundary needs, since a moved centroid leaves a stale elevation
behind and nothing else would notice.

Usage:
    # Preview — reports what it would resolve, writes nothing.
    uv run python manage.py refresh_centroid_elevations

    # Resolve the missing ones and write the fixtures.
    uv run python manage.py refresh_centroid_elevations --commit

    # Re-resolve everything after a fixture rebuild moved boundaries.
    uv run python manage.py refresh_centroid_elevations --commit --force
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.command_iteration import non_negative_float
from apps.locations.services.elevation import fetch_elevation
from apps.regions.fixture_utils import centre_from_bbox, load_fixture, write_fixture

logger = logging.getLogger(__name__)

FIXTURE_NAMES = ["eaws_CH.json", "eaws_FR.json", "eaws_AT.json", "eaws_IT.json"]

MICRO_REGION_MODEL = "regions.microregion"


class Command(BaseCommand):
    """Resolve and store each micro-region's centroid elevation in the fixtures.

    Read-only by default; pass --commit to rewrite the fixture files. One
    Open-Meteo elevation call per unresolved region, paced by --delay.
    """

    help = (
        "Fill centroid_elevation_m on every L4 entry in the EAWS fixtures, "
        "so each environment can derive its region centroids offline. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write the resolved elevations back to the fixture files. "
                "Without this flag the command resolves and reports but "
                "writes nothing."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-resolve entries that already carry an elevation. Needed "
                "after a fixture rebuild moves a boundary, since the stored "
                "elevation is then for the old centroid."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive lookups. "
                "Default 1.0 — paces the run inside Open-Meteo's free-tier "
                "rate limit."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve the centroid elevation for every candidate fixture entry."""
        commit: bool = options["commit"]
        force: bool = options["force"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        fixtures_dir = Path(settings.BASE_DIR) / "apps" / "regions" / "fixtures"
        counts = {"resolved": 0, "skipped": 0, "failed": 0, "unchanged": 0}

        for name in FIXTURE_NAMES:
            path = fixtures_dir / name
            if not path.exists():
                raise CommandError(f"Fixture not found: {path}")
            self._process_fixture(
                path,
                counts,
                commit=commit,
                force=force,
                delay=delay,
                verbosity=verbosity,
            )

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"refresh_centroid_elevations completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _process_fixture(
        self,
        path: Path,
        counts: dict[str, int],
        *,
        commit: bool,
        force: bool,
        delay: float,
        verbosity: int,
    ) -> None:
        """Resolve every candidate entry in one fixture file.

        Args:
            path: The fixture file to walk.
            counts: The running tally, mutated in place.
            commit: Whether to write the file back.
            force: Whether to re-resolve entries that already have a value.
            delay: Seconds to sleep between lookups.
            verbosity: Django's ``--verbosity`` level.

        """
        entries = load_fixture(path, missing_ok=False)
        micro_regions = [e for e in entries if e["model"] == MICRO_REGION_MODEL]
        changed = False

        for entry in micro_regions:
            if self._resolve_one(
                entry["fields"],
                counts,
                force=force,
                delay=delay,
                verbosity=verbosity,
            ):
                changed = True

        if commit and changed:
            # Shared writer, so a refresh produces no incidental diff noise
            # against what the build_*_fixture commands emit.
            write_fixture(path, entries)
        if verbosity >= 1:
            self.stdout.write(
                f"{path.name}: {len(micro_regions)} micro-region(s) examined."
            )

    def _resolve_one(
        self,
        fields: dict[str, Any],
        counts: dict[str, int],
        *,
        force: bool,
        delay: float,
        verbosity: int,
    ) -> bool:
        """Resolve one fixture entry's centroid elevation, in place.

        Args:
            fields: The entry's ``fields`` dict, mutated on success.
            counts: The running tally, mutated in place.
            force: Whether to re-resolve an entry that already has a value.
            delay: Seconds to sleep after a lookup.
            verbosity: Django's ``--verbosity`` level.

        Returns:
            ``True`` when the entry was changed and the file needs writing.

        """
        region_id = fields.get("region_id", "?")

        if fields.get("centroid_elevation_m") is not None and not force:
            counts["unchanged"] += 1
            return False

        centre = self._centre_of(fields, region_id)
        if centre is None:
            counts["skipped"] += 1
            return False

        latitude, longitude = centre
        try:
            elevation = fetch_elevation(latitude, longitude)
        except Exception:  # noqa: BLE001 — one region must not abort the run
            logger.exception(
                "refresh_centroid_elevations: failed to resolve %s", region_id
            )
            counts["failed"] += 1
            return False

        fields["centroid_elevation_m"] = elevation
        counts["resolved"] += 1
        if verbosity >= 2:
            self.stdout.write(f"{region_id} -> {elevation:.0f}m")
        if delay > 0:
            time.sleep(delay)
        return True

    def _centre_of(
        self, fields: dict[str, Any], region_id: str
    ) -> tuple[float, float] | None:
        """Derive a fixture entry's centroid as ``(latitude, longitude)``.

        Args:
            fields: The entry's ``fields`` dict.
            region_id: The region's id, for the warning message.

        Returns:
            The pair, or ``None`` when the boundary cannot be read.

        """
        boundary = fields.get("boundary")
        if not isinstance(boundary, dict):
            logger.warning(
                "refresh_centroid_elevations: %s has no usable boundary; skipped.",
                region_id,
            )
            return None
        try:
            centre = centre_from_bbox(boundary)
        except KeyError, TypeError, ValueError, IndexError:
            logger.warning(
                "refresh_centroid_elevations: %s has an unreadable boundary; skipped.",
                region_id,
            )
            return None
        return float(centre["lat"]), float(centre["lon"])

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary."""
        if verbosity >= 1:
            suffix = (
                "Fixtures written."
                if commit
                else "No data written. Pass --commit to persist."
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts['resolved']} resolved, "
                    f"{counts['unchanged']} already had one, "
                    f"{counts['skipped']} skipped, "
                    f"{counts['failed']} failed. {suffix}"
                )
            )
        logger.info(
            "refresh_centroid_elevations finished: resolved=%d unchanged=%d "
            "skipped=%d failed=%d commit=%s",
            counts["resolved"],
            counts["unchanged"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
