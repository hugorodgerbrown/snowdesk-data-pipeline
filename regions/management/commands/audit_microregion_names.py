"""audit_microregion_names — detect and fix stale MicroRegion names.

Compares the stored ``MicroRegion.name`` for every L4 region against the
name last seen in a ``RegionBulletin.region_name_at_time`` (the SLF-
authoritative label, captured at every bulletin ingest). Regions where the
two differ are reported as mismatches. Regions with no bulletin coverage
are skipped and reported separately (no regression).

Read-only by default. Exits non-zero when mismatches are present and
``--commit`` was not passed — this makes the command usable as a scheduled
drift-detector (e.g. on Render cron).

``--commit`` performs two writes:
  1. Patches ``docs/eaws_regions_ch.csv`` in place — only the
     ``region_name`` column for rows whose ``region_id`` is in the
     mismatch set.
  2. Calls ``scripts.build_regions_fixture.build_fixture(...)`` to
     regenerate the ``regions.microregion`` (L4) entries in
     ``regions/fixtures/eaws_CH.json``.

The caller should then run:
  poetry run python manage.py refresh_eaws_fixtures --commit
  poetry run python manage.py loaddata regions/fixtures/eaws_CH.json

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed; no dry-run flag needed.

Usage:
    # Preview mismatches (default — no writes, exits non-zero if any found).
    poetry run python manage.py audit_microregion_names

    # Patch the CSV + regenerate the L4 fixture.
    poetry run python manage.py audit_microregion_names --commit
"""

from __future__ import annotations

import csv
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Max

from bulletins.models import RegionBulletin
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CSV_PATH = _REPO_ROOT / "docs" / "eaws_regions_ch.csv"
_FIXTURE_PATH = _REPO_ROOT / "regions" / "fixtures" / "eaws_CH.json"


class Command(BaseCommand):
    """Detect stale MicroRegion names; optionally patch the CSV and fixture."""

    help = (
        "Compare MicroRegion.name against the most-recent RegionBulletin "
        "region_name_at_time (the SLF-authoritative name). Report mismatches. "
        "With --commit: patch docs/eaws_regions_ch.csv and regenerate the "
        "regions/fixtures/eaws_CH.json L4 entries. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Patch docs/eaws_regions_ch.csv and regenerate the L4 fixture. "
                "Without this flag the command only reports mismatches and "
                "exits non-zero when any are found."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Compare names, report, and optionally commit patches."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        mismatches, no_data = _find_mismatches()
        self._report(mismatches, no_data, verbosity)

        if not mismatches:
            return

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run (no --commit) — not writing any changes. "
                    "Pass --commit to patch the CSV and regenerate the fixture."
                )
            )
            sys.exit(1)

        self._apply_commit(mismatches, verbosity)

    def _report(
        self,
        mismatches: dict[str, tuple[str, str, str]],
        no_data: list[str],
        verbosity: int,
    ) -> None:
        """Print the audit summary to stdout."""
        if verbosity >= 1:
            self.stdout.write(
                f"Audit complete: {len(mismatches)} mismatch(es), "
                f"{len(no_data)} region(s) with no bulletin coverage."
            )

        if mismatches:
            _print_mismatch_table(self.stdout, mismatches)

        if no_data and verbosity >= 1:
            self.stdout.write(
                self.style.WARNING(
                    f"No bulletin data for {len(no_data)} region(s) — "
                    "skipped (names left unchanged):"
                )
            )
            for region_id in sorted(no_data):
                self.stdout.write(f"  {region_id}")

        if not mismatches and verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("All names are up to date."))

    def _apply_commit(
        self,
        mismatches: dict[str, tuple[str, str, str]],
        verbosity: int,
    ) -> None:
        """Patch the CSV and regenerate the fixture."""
        _patch_csv(_CSV_PATH, mismatches)
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Patched {_CSV_PATH.name} ({len(mismatches)} row(s))."
                )
            )

        _rebuild_fixture(_CSV_PATH, _FIXTURE_PATH)
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Regenerated {_FIXTURE_PATH.name} L4 entries "
                    f"({len(mismatches)} name(s) updated)."
                )
            )
            self.stdout.write("\nNext steps:")
            self.stdout.write(
                "  poetry run python manage.py refresh_eaws_fixtures --commit"
            )
            self.stdout.write(
                "  poetry run python manage.py loaddata regions/fixtures/eaws_CH.json"
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _latest_slf_names() -> dict[str, tuple[str, str]]:
    """Return {region_id: (slf_name, date_str)} sourced from the most-recent bulletin.

    For each MicroRegion, picks the RegionBulletin linked to the Bulletin with
    the largest ``valid_from``. When multiple RegionBulletins for the same
    region share the same latest ``valid_from``, the one with the highest
    ``id`` is used — deterministic and consistent.

    Regions with no RegionBulletin rows are absent from the result.
    """
    # Aggregate the latest valid_from per region.
    # Use ``region__region_id`` to get the string identifier, not the numeric PK.
    latest_dates = (
        RegionBulletin.objects.select_related("bulletin", "region")
        .values("region__region_id")
        .annotate(max_valid_from=Max("bulletin__valid_from"))
    )
    latest_by_region: dict[str, Any] = {
        row["region__region_id"]: row["max_valid_from"] for row in latest_dates
    }

    result: dict[str, tuple[str, str]] = {}
    for region_str_id, max_valid_from in latest_by_region.items():
        # Pick the RegionBulletin with that exact region + valid_from + highest id.
        rb = (
            RegionBulletin.objects.filter(
                region__region_id=region_str_id,
                bulletin__valid_from=max_valid_from,
            )
            .select_related("bulletin")
            .order_by("-id")
            .first()
        )
        if rb is not None and rb.region_name_at_time:
            valid_from = rb.bulletin.valid_from
            date_str = valid_from.date().isoformat() if valid_from else "unknown"
            result[region_str_id] = (rb.region_name_at_time, date_str)

    return result


def _find_mismatches() -> tuple[dict[str, tuple[str, str, str]], list[str]]:
    """Return (mismatches, no_data_region_ids).

    mismatches maps region_id → (current_name, slf_name, last_seen_date).
    no_data is the sorted list of region_ids with no RegionBulletin coverage.
    """
    slf_names = _latest_slf_names()

    all_regions = list(MicroRegion.objects.values_list("region_id", "name"))

    mismatches: dict[str, tuple[str, str, str]] = {}
    no_data: list[str] = []

    for region_id, current_name in all_regions:
        if region_id not in slf_names:
            no_data.append(region_id)
            continue
        slf_name, last_seen = slf_names[region_id]
        if current_name != slf_name:
            mismatches[region_id] = (current_name, slf_name, last_seen)
            logger.debug(
                "Mismatch %s: %r → %r (last seen %s)",
                region_id,
                current_name,
                slf_name,
                last_seen,
            )

    return mismatches, no_data


def _print_mismatch_table(
    stdout: Any,
    mismatches: dict[str, tuple[str, str, str]],
) -> None:
    """Print a human-readable table of mismatches to stdout."""
    stdout.write(
        f"\n{'region_id':<12}  {'current name':<40}  {'SLF name':<40}  "
        f"{'last seen':<12}"
    )
    stdout.write("-" * 110)
    for region_id, (current_name, slf_name, last_seen) in sorted(mismatches.items()):
        stdout.write(
            f"{region_id:<12}  {current_name:<40}  {slf_name:<40}  {last_seen:<12}"
        )
    stdout.write("")


def _patch_csv(
    csv_path: Path,
    mismatches: dict[str, tuple[str, str, str]],
) -> None:
    """Patch the region_name column in the CSV for each mismatched region_id.

    Reads the full CSV, updates only the ``region_name`` column for rows
    whose ``region_id`` appears in ``mismatches``, then writes back in place.
    All other columns (slug, centre, boundary) are left untouched.
    """
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            fieldnames = list(reader.fieldnames)
        for row in reader:
            rid = row["region_id"]
            if rid in mismatches:
                _, slf_name, _ = mismatches[rid]
                row["region_name"] = slf_name
            rows.append(row)

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(
        "_patch_csv: wrote %d rows to %s (%d name(s) patched)",
        len(rows),
        csv_path,
        len(mismatches),
    )


def _rebuild_fixture(csv_path: Path, fixture_path: Path) -> None:
    """Regenerate the L4 entries in eaws_CH.json from the patched CSV.

    The existing fixture may also contain L1 (MajorRegion) and L2 (SubRegion)
    entries. This helper:
      1. Reads any existing L1/L2 entries from ``fixture_path``.
      2. Delegates to ``scripts.build_regions_fixture.build_fixture`` to
         regenerate the L4 (MicroRegion) entries into a temporary file.
      3. Writes the combined L1/L2 + fresh L4 back to ``fixture_path``.

    This preserves the L1/L2 entries so that ``refresh_eaws_fixtures``
    can continue to re-derive their geometry.
    """
    import json as _json

    try:
        from scripts.build_regions_fixture import build_fixture
    except ImportError as exc:
        raise RuntimeError(
            "scripts.build_regions_fixture is not importable. "
            "Ensure you are running from the repo root with the dev venv."
        ) from exc

    # Read existing L1/L2 entries from the current fixture (if any).
    l1_l2_entries: list[dict] = []
    if fixture_path.exists():
        existing = _json.loads(fixture_path.read_text(encoding="utf-8"))
        l1_l2_entries = [
            e
            for e in existing
            if e.get("model") in ("regions.majorregion", "regions.subregion")
        ]

    # build_fixture writes L4-only records to a temporary path.
    tmp_fixture = fixture_path.with_suffix(".tmp.json")
    try:
        build_fixture(csv_path, tmp_fixture)
        l4_entries = _json.loads(tmp_fixture.read_text(encoding="utf-8"))
    finally:
        if tmp_fixture.exists():
            tmp_fixture.unlink()

    # Combine: L1 + L2 + L4.
    combined = l1_l2_entries + l4_entries
    fixture_path.write_text(
        _json.dumps(combined, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "_rebuild_fixture: wrote %d entries (%d L1/L2 + %d L4) to %s",
        len(combined),
        len(l1_l2_entries),
        len(l4_entries),
        fixture_path,
    )
