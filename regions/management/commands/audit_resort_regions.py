"""audit_resort_regions — detect and fix Resort → MicroRegion FK mismatches.

For every ``Resort`` with non-null ``latitude``/``longitude``, builds a
``shapely.geometry.Point(lon, lat)`` and tests which ``MicroRegion.boundary``
Polygon contains it. Resorts are partitioned into three buckets:

  (a) FK matches the containing polygon — silent unless ``--verbosity 2``.
  (b) FK is wrong but a correct region is found — reported as actionable
      mismatch (resort name, current FK region_id, suggested region_id).
  (c) Point falls outside every CH-* polygon — warning; never auto-fixed.

Read-only by default. Exits non-zero when any bucket-(b) resorts are found
and ``--commit`` was not passed.

``--commit`` re-FKs every bucket-(b) resort and calls
``dump_resorts_fixture._write_resorts_fixture()`` to refresh
``regions/fixtures/resorts.json``. Bucket-(c) resorts are left untouched
even with ``--commit`` — manual cleanup via the map editor is the right path.

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed.

Shapely is imported lazily (same pattern as
``refresh_eaws_fixtures._boundary_from_children``) so this command does not
add a hard runtime dependency on the production path.

Usage:
    # Preview mismatches (default — no writes, exits non-zero if any found).
    poetry run python manage.py audit_resort_regions

    # Re-FK the mismatched resorts and refresh the fixture.
    poetry run python manage.py audit_resort_regions --commit
"""

from __future__ import annotations

import logging
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)

# Module-level path — can be patched in tests.
_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "resorts.json"
)


class Command(BaseCommand):
    """Detect Resort→MicroRegion FK mismatches using polygon containment."""

    help = (
        "For every geocoded Resort, check that its region FK polygon "
        "contains its (lat, lon). Report mismatches. "
        "With --commit: re-FK mismatched resorts and refresh resorts.json. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Re-FK bucket-(b) resorts and refresh regions/fixtures/resorts.json. "
                "Without this flag the command only reports and exits non-zero "
                "when mismatches are found."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the containment audit, report, and optionally commit fixes."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        bucket_b, bucket_c = _audit_resort_regions(verbosity)

        self._report(bucket_b, bucket_c, verbosity)

        if not bucket_b:
            return

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run (no --commit) — not writing any changes. "
                    "Pass --commit to re-FK the resorts and refresh the fixture."
                )
            )
            sys.exit(1)

        self._apply_commit(bucket_b, verbosity)

    def _report(
        self,
        bucket_b: list[tuple[Resort, str]],
        bucket_c: list[Resort],
        verbosity: int,
    ) -> None:
        """Print the audit summary to stdout."""
        if verbosity >= 1:
            self.stdout.write(
                f"Audit complete: {len(bucket_b)} FK mismatch(es) (fixable), "
                f"{len(bucket_c)} resort(s) outside every polygon (manual)."
            )

        if bucket_b:
            self.stdout.write(
                f"\n{'resort':<35}  {'current region':<12}  {'suggested region':<12}"
            )
            self.stdout.write("-" * 65)
            for resort, suggested_id in bucket_b:
                current_id = resort.region.region_id
                self.stdout.write(
                    f"{resort.name:<35}  {current_id:<12}  {suggested_id:<12}"
                )
            self.stdout.write("")

        for resort in bucket_c:
            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: {resort.name!r} (lat={resort.latitude}, "
                    f"lon={resort.longitude}) is outside every polygon — "
                    "skipped even with --commit."
                )
            )

        if not bucket_b and verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS("All geocoded resort FKs are consistent.")
            )

    def _apply_commit(
        self,
        bucket_b: list[tuple[Resort, str]],
        verbosity: int,
    ) -> None:
        """Re-FK bucket-(b) resorts and refresh the fixture."""
        for resort, suggested_id in bucket_b:
            old_region_id = resort.region.region_id
            new_region = MicroRegion.objects.get(region_id=suggested_id)
            resort.region = new_region
            resort.save(update_fields=["region", "updated_at"])
            if verbosity >= 2:
                logger.info(
                    "Re-FKed %r: %s → %s",
                    resort.name,
                    old_region_id,
                    suggested_id,
                )

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"Re-FKed {len(bucket_b)} resort(s)."))

        # Refresh the fixture using the shared helper from dump_resorts_fixture.
        from regions.management.commands.dump_resorts_fixture import (
            _write_resorts_fixture,
        )

        _write_resorts_fixture(_FIXTURE_PATH, verbosity=verbosity)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    "Refreshed regions/fixtures/resorts.json. "
                    "Run: poetry run python manage.py loaddata "
                    "regions/fixtures/resorts.json"
                )
            )


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------


def _build_region_polygons() -> list[tuple[str, Any]]:
    """Return [(region_id, shapely_polygon), ...] for all MicroRegions with boundary.

    Imports shapely lazily so the runtime path (which never calls this
    function directly) doesn't need shapely installed.
    """
    try:
        from shapely.geometry import shape
    except ImportError as exc:
        raise RuntimeError(
            "audit_resort_regions requires the dev-only `shapely` dependency. "
            "Install it with `poetry install --with dev`."
        ) from exc

    polygons: list[tuple[str, Any]] = []
    for region in MicroRegion.objects.filter(boundary__isnull=False).only(
        "region_id", "boundary"
    ):
        if region.boundary:
            polygons.append((region.region_id, shape(region.boundary)))
    return polygons


def _find_containing_region(
    lon: float,
    lat: float,
    region_polygons: list[tuple[str, Any]],
) -> str | None:
    """Return the region_id of the first polygon that contains (lon, lat), or None.

    Uses ``shapely.geometry.Point`` for the containment test.
    """
    try:
        from shapely.geometry import Point
    except ImportError as exc:  # pragma: no cover — tested via _build_region_polygons
        raise RuntimeError(
            "audit_resort_regions requires the dev-only `shapely` dependency."
        ) from exc

    point = Point(lon, lat)
    for region_id, polygon in region_polygons:
        if polygon.contains(point):
            return region_id
    return None


def _audit_resort_regions(
    verbosity: int = 1,
) -> tuple[list[tuple[Resort, str]], list[Resort]]:
    """Return (bucket_b, bucket_c) for the geocoded resort set.

    bucket_b: [(resort, suggested_region_id), ...] — FK is wrong but
              a correct region was found.
    bucket_c: [resort, ...] — point outside every polygon (manual).
    """
    region_polygons = _build_region_polygons()

    geocoded = list(
        Resort.objects.select_related("region")
        .filter(latitude__isnull=False, longitude__isnull=False)
        .order_by("name")
    )

    bucket_b: list[tuple[Resort, str]] = []
    bucket_c: list[Resort] = []

    for resort in geocoded:
        lon = resort.longitude
        lat = resort.latitude
        if lon is None or lat is None:
            continue

        containing = _find_containing_region(lon, lat, region_polygons)

        if containing is None:
            # Bucket (c): outside every polygon.
            bucket_c.append(resort)
        elif containing == resort.region.region_id:
            # Bucket (a): FK is correct.
            if verbosity >= 2:
                logger.info(
                    "OK: %r is inside its region %s",
                    resort.name,
                    resort.region.region_id,
                )
        else:
            # Bucket (b): FK is wrong, correct region found.
            bucket_b.append((resort, containing))

    return bucket_b, bucket_c
