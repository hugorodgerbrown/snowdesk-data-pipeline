"""
pipeline/management/commands/diagnose_region_coverage.py — Diagnostic command.

Reports which Swiss regions are missing from ``RegionDayRating``, partitioning
all regions in the fixture into three buckets:

  A. Has at least one ``RegionDayRating`` row.
  B. Appears in at least one raw SLF bulletin's
     ``properties.regions`` list but has no ``RegionDayRating`` row
     (suggests a local backfill bug).
  C. Never appears in any raw SLF bulletin
     (suggests an upstream SLF gap — SLF does not publish for the region).

The default invocation scans the entire bulletin archive. Pass
``--date YYYY-MM-DD`` to restrict the analysis to bulletins targeting a
single calendar day (using the same morning/prior-evening targeting rule
that ``recompute_region_day`` applies). ``--verbose-table`` additionally
prints the full per-region bucket table.

Pure SELECT — no ``--commit`` flag, the command never writes to the
database.

Typical use::

    poetry run python manage.py diagnose_region_coverage
    poetry run python manage.py diagnose_region_coverage --verbose-table
    poetry run python manage.py diagnose_region_coverage --date 2026-04-15
"""

from __future__ import annotations

import datetime as dt
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Bulletin, Region, RegionDayRating
from pipeline.services.day_rating import _target_day

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Diagnose RegionDayRating coverage gaps across the Swiss region fixture."""

    help = (
        "Partition all regions in the fixture into three buckets per their "
        "RegionDayRating coverage: (A) has rating row(s), (B) appears in raw "
        "SLF bulletins but has no rating row (local-bug suspect), (C) never "
        "appears in any raw bulletin (upstream-gap suspect). Pure SELECT."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            metavar="YYYY-MM-DD",
            dest="target_date",
            help=(
                "Restrict the analysis to bulletins targeting a single "
                "calendar day. Without this flag the whole bulletin "
                "archive is scanned."
            ),
        )
        parser.add_argument(
            "--verbose-table",
            action="store_true",
            dest="verbose_table",
            help="Also print the full per-region bucket table in fixture order.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the diagnostic and print the partition."""
        date_arg: str | None = options["target_date"]
        verbose_table: bool = options["verbose_table"]
        target_date = self._parse_date(date_arg) if date_arg else None

        all_regions = self._all_regions()
        rated_ids = self._rated_region_ids(target_date)
        seen_ids = self._seen_in_bulletins_region_ids(target_date)

        # Buckets are computed against the fixture ("all_regions") so any
        # rated/seen ID that isn't in the fixture (shouldn't happen, but
        # defensive) is silently dropped from the partition rather than
        # inflating the counts.
        bucket_a = sorted(rated_ids & all_regions)
        bucket_b = sorted((seen_ids - rated_ids) & all_regions)
        bucket_c = sorted(all_regions - rated_ids - seen_ids)

        scope = f"date={target_date.isoformat()}" if target_date else "whole archive"
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Region coverage diagnostic ({scope})")
        )
        self.stdout.write(f"Regions in fixture: {len(all_regions)}")
        self.stdout.write(f"  A. Has rating row(s):                  {len(bucket_a)}")
        self.stdout.write(f"  B. In raw bulletin but no rating row:  {len(bucket_b)}")
        self.stdout.write(f"  C. Never in any raw bulletin:          {len(bucket_c)}")

        if bucket_b:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("Bucket B (local-bug suspects) — region IDs:")
            )
            for region_id in bucket_b:
                self.stdout.write(f"  {region_id}")

        if bucket_c:
            self.stdout.write("")
            self.stdout.write(
                self.style.NOTICE("Bucket C (upstream-gap suspects) — region IDs:")
            )
            for region_id in bucket_c:
                self.stdout.write(f"  {region_id}")

        if verbose_table:
            self._print_full_table(rated_ids, seen_ids)

        logger.info(
            "diagnose_region_coverage finished: scope=%s a=%d b=%d c=%d",
            scope,
            len(bucket_a),
            len(bucket_b),
            len(bucket_c),
        )

    @staticmethod
    def _parse_date(value: str) -> dt.date:
        """Parse a YYYY-MM-DD string or raise ``CommandError``."""
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(
                f"Invalid --date value {value!r}; expected YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _all_regions() -> set[str]:
        """Return the set of every ``region_id`` known to the system."""
        return set(Region.objects.values_list("region_id", flat=True))

    @staticmethod
    def _rated_region_ids(target_date: dt.date | None) -> set[str]:
        """
        Return ``region_id``\u200bs with at least one ``RegionDayRating`` row.

        If ``target_date`` is given, restrict to rows whose ``date`` matches.
        """
        qs = RegionDayRating.objects.all()
        if target_date is not None:
            qs = qs.filter(date=target_date)
        return set(qs.values_list("region__region_id", flat=True).distinct())

    @staticmethod
    def _seen_in_bulletins_region_ids(target_date: dt.date | None) -> set[str]:
        """
        Return ``region_id``\u200bs appearing in any bulletin's raw payload.

        Reads the regions list from each bulletin's
        ``raw_data["properties"]["regions"]`` directly, rather than via the
        ``RegionBulletin`` join, so the answer is independent of any local
        linking step that might be the source of the gap under investigation.

        If ``target_date`` is given, restrict to bulletins whose
        ``_target_day`` equals that date — mirroring the candidate filter in
        ``recompute_region_day``.
        """
        if target_date is None:
            seen: set[str] = set()
            for raw_data in Bulletin.objects.values_list(
                "raw_data", flat=True
            ).iterator():
                _collect_region_ids(raw_data, seen)
            return seen

        # Target-date scope: a bulletin can target ``target_date`` if it was
        # issued on the morning of that day or on the prior evening. Pull
        # both candidate days then filter by ``_target_day`` exactly.
        candidates = Bulletin.objects.filter(
            valid_from__date__in=[
                target_date,
                target_date - dt.timedelta(days=1),
            ],
        )
        seen = set()
        for bulletin in candidates.iterator():
            if _target_day(bulletin) != target_date:
                continue
            _collect_region_ids(bulletin.raw_data, seen)
        return seen

    def _print_full_table(
        self,
        rated_ids: set[str],
        seen_ids: set[str],
    ) -> None:
        """Print a per-region row showing each region's bucket and name."""
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Per-region table:"))
        for region in Region.objects.order_by("region_id"):
            if region.region_id in rated_ids:
                bucket = "A"
            elif region.region_id in seen_ids:
                bucket = "B"
            else:
                bucket = "C"
            self.stdout.write(f"  [{bucket}] {region.region_id}  {region.name}")


def _collect_region_ids(raw_data: Any, into: set[str]) -> None:
    """Extract ``regionID`` strings from a bulletin ``raw_data`` blob.

    Tolerates missing/empty payloads silently — the bulletin archive may
    contain partially populated rows from earlier ingest shapes, and the
    diagnostic shouldn't crash on them.
    """
    if not isinstance(raw_data, dict):
        return
    properties = raw_data.get("properties")
    if not isinstance(properties, dict):
        return
    for entry in properties.get("regions") or []:
        if isinstance(entry, dict):
            region_id = entry.get("regionID")
            if isinstance(region_id, str) and region_id:
                into.add(region_id)
