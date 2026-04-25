"""
pipeline/management/commands/extract_bulletin_fixture.py — Bulletin fixture extractor.

Walks the local Bulletin table and writes a JSON file shaped like the
SLF CAAML payload — a JSON array of GeoJSON Feature envelopes, each
preserving the bulletin's ``raw_data`` byte-for-byte. The output is
checked into ``sample_data/test_fixtures/`` so downstream test fixtures
(SNOW-52) can re-use the existing CAAML parsing path with no shape
gymnastics.

Two modes:

  --mode=season --region CH-xxxx --season YYYY-MM
      One region's full season — every bulletin whose ``valid_from``
      falls in the 6-month window starting at ``YYYY-MM-01`` UTC and
      whose ``regions`` M2M includes ``CH-xxxx``.

  --mode=day --date YYYY-MM-DD
      All regions for one calendar day — every bulletin whose
      ``valid_from`` lands on that date (UTC).

Pure SELECT against the database. Refuses to overwrite an existing
output file unless ``--force`` is passed. On every successful run the
command appends a manifest row to ``sample_data/test_fixtures/README.md``
so the source season/date and extraction time stay reproducible.

Typical use::

    poetry run python manage.py extract_bulletin_fixture \
        --mode season --region CH-4115 --season 2025-11
    poetry run python manage.py extract_bulletin_fixture \
        --mode day --date 2026-04-15
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Bulletin

logger = logging.getLogger(__name__)

MODE_SEASON = "season"
MODE_DAY = "day"
SEASON_WINDOW_MONTHS = 6

README_HEADER = """\
# Test fixtures

Real CAAML bulletin payloads extracted from a populated dev database by
the ``extract_bulletin_fixture`` management command. Files are checked
into the repo so test runs are reproducible.

| File | Source | Extracted at |
|------|--------|--------------|
"""


class Command(BaseCommand):
    """Extract bulletin fixtures from the local DB into sample_data/test_fixtures/."""

    help = (
        "Read-only fixture extractor. Writes a JSON array of CAAML Feature "
        "envelopes (one per matching Bulletin row) to sample_data/test_fixtures/ "
        "and appends a manifest row to the sibling README.md. Two modes: "
        "--mode=season (one region's full season) or --mode=day (all regions "
        "for one calendar day)."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--mode",
            required=True,
            choices=(MODE_SEASON, MODE_DAY),
            help=(
                "Which fixture shape to extract. 'season' = one region across "
                "a 6-month window; 'day' = every region for one calendar day."
            ),
        )
        parser.add_argument(
            "--region",
            metavar="CH-xxxx",
            dest="region_id",
            help="Region ID. Required when --mode=season; rejected otherwise.",
        )
        parser.add_argument(
            "--season",
            metavar="YYYY-MM",
            help=(
                "Start month of the season (UTC). The window covers six "
                "calendar months from YYYY-MM-01. Required when --mode=season."
            ),
        )
        parser.add_argument(
            "--date",
            metavar="YYYY-MM-DD",
            dest="target_date",
            help="Calendar day (UTC). Required when --mode=day; rejected otherwise.",
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            default=settings.BASE_DIR / "sample_data" / "test_fixtures",
            help=(
                "Directory to write the fixture file and update the manifest. "
                "Defaults to sample_data/test_fixtures/ at the repo root."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Overwrite an existing output file. Default: refuse and exit non-zero."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Validate args, run the extraction, and update the manifest."""
        mode: str = options["mode"]
        output_dir: Path = options["output_dir"]
        force: bool = options["force"]

        if mode == MODE_SEASON:
            region_id = self._require_for_season(options)
            window_start, window_end, season_label = self._season_window(
                options["season"]
            )
            filename = f"season_{region_id.lower()}_{season_label}.json"
            source = f"season {season_label} / {region_id}"
            features = self._extract_season(region_id, window_start, window_end)
        else:
            target_date = self._require_for_day(options)
            filename = f"day_{target_date.isoformat()}.json"
            source = f"day {target_date.isoformat()}"
            features = self._extract_day(target_date)

        if not features:
            raise CommandError(
                f"No bulletins matched (mode={mode}, source={source}). "
                "Refusing to write an empty fixture."
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        if output_path.exists() and not force:
            raise CommandError(
                f"Output file already exists: {output_path}. Pass --force to overwrite."
            )

        output_path.write_text(json.dumps(features, indent=2, sort_keys=True) + "\n")
        self._append_manifest_row(output_dir / "README.md", filename, source)

        self.stdout.write(
            self.style.SUCCESS(f"Extracted {len(features)} bulletin(s) → {output_path}")
        )
        self.stdout.write(f"Manifest updated: {output_dir / 'README.md'}")
        logger.info(
            "extract_bulletin_fixture finished: mode=%s source=%s count=%d path=%s",
            mode,
            source,
            len(features),
            output_path,
        )

    # ------------------------------------------------------------------
    # Argument validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_for_season(options: dict[str, Any]) -> str:
        """Return ``region_id`` for season mode; raise on misuse."""
        region_id: str | None = options["region_id"]
        season: str | None = options["season"]
        target_date: str | None = options["target_date"]
        if not region_id or not season:
            raise CommandError(
                "--mode=season requires both --region CH-xxxx and --season YYYY-MM."
            )
        if target_date is not None:
            raise CommandError("--date is not valid with --mode=season.")
        return region_id

    @staticmethod
    def _require_for_day(options: dict[str, Any]) -> dt.date:
        """Return the target ``date`` for day mode; raise on misuse."""
        target_date: str | None = options["target_date"]
        region_id: str | None = options["region_id"]
        season: str | None = options["season"]
        if not target_date:
            raise CommandError("--mode=day requires --date YYYY-MM-DD.")
        if region_id is not None or season is not None:
            raise CommandError("--region and --season are not valid with --mode=day.")
        try:
            return dt.date.fromisoformat(target_date)
        except ValueError as exc:
            raise CommandError(
                f"Invalid --date value {target_date!r}; expected YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _season_window(season: str) -> tuple[dt.datetime, dt.datetime, str]:
        """
        Parse ``YYYY-MM`` and return (start_dt, end_dt, label).

        The window is six calendar months wide, half-open
        ``[YYYY-MM-01 00:00 UTC, +6 months)``, matching the typical
        Nov-Apr Swiss avalanche season when ``YYYY-MM`` is November.
        """
        try:
            start_date = dt.date.fromisoformat(f"{season}-01")
        except ValueError as exc:
            raise CommandError(
                f"Invalid --season value {season!r}; expected YYYY-MM."
            ) from exc
        end_month_total = start_date.month + SEASON_WINDOW_MONTHS
        end_year = start_date.year + (end_month_total - 1) // 12
        end_month = ((end_month_total - 1) % 12) + 1
        end_date = dt.date(end_year, end_month, 1)
        start_dt = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.UTC)
        end_dt = dt.datetime.combine(end_date, dt.time.min, tzinfo=dt.UTC)
        return start_dt, end_dt, season

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_season(
        region_id: str,
        window_start: dt.datetime,
        window_end: dt.datetime,
    ) -> list[dict[str, Any]]:
        """Return ``raw_data`` for every bulletin in the window for the region."""
        qs = (
            Bulletin.objects.filter(
                regions__region_id=region_id,
                valid_from__gte=window_start,
                valid_from__lt=window_end,
            )
            .order_by("valid_from", "bulletin_id")
            .distinct()
        )
        return [bulletin.raw_data for bulletin in qs]

    @staticmethod
    def _extract_day(target_date: dt.date) -> list[dict[str, Any]]:
        """Return ``raw_data`` for every bulletin valid on the given date."""
        qs = Bulletin.objects.filter(valid_from__date=target_date).order_by(
            "bulletin_id"
        )
        return [bulletin.raw_data for bulletin in qs]

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _append_manifest_row(readme_path: Path, filename: str, source: str) -> None:
        """
        Append a single manifest row to the sibling README.

        Creates the file with the standard header on first run if absent.
        Re-runs (e.g. with ``--force``) deliberately append duplicate rows
        rather than rewrite — the manifest is a record of every extraction,
        not a deduplicated index.
        """
        extracted_at = dt.datetime.now(tz=dt.UTC).replace(microsecond=0).isoformat()
        row = f"| {filename} | {source} | {extracted_at} |\n"
        if not readme_path.exists():
            readme_path.write_text(README_HEADER + row)
            return
        with readme_path.open("a") as handle:
            handle.write(row)
