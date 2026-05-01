"""
pipeline/management/commands/export_day_character_csv.py — Day-character CSV export.

Walks the ``Bulletin`` archive and emits one CSV row per bulletin capturing
the canonical day-character key, its explainer, and every render_model
input that feeds the five-rule cascade in
``pipeline.services.render_model.compute_day_character``. The output is
designed so each row can be hand-verified against the cascade — danger
level, subdivision, problem types, aspect counts, and the lowest
elevation lower bound are all surfaced as their own columns.

Pure SELECT — read-only by default, no ``--commit`` flag. Defaults to
stdout; pass ``--output PATH`` to write to a file. Optional ``--lang``,
``--start-date``, and ``--end-date`` filters narrow the archive scan.

Typical use::

    poetry run python manage.py export_day_character_csv --lang de > dc.csv
    poetry run python manage.py export_day_character_csv \
        --start-date 2026-01-01 --end-date 2026-01-31 --lang de
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Bulletin
from pipeline.services.render_model import (
    _DAY_CHARACTER,
    DayCharacter,
    compute_day_character,
)

logger = logging.getLogger(__name__)

CSV_HEADERS: tuple[str, ...] = (
    # Identity
    "bulletin_id",
    "valid_from",
    "valid_until",
    "lang",
    "regions_count",
    "region_ids",
    # Result
    "day_character",
    "day_character_explainer",
    # Cascade inputs
    "danger_number",
    "danger_subdivision",
    "trait_count",
    "problem_count",
    "problem_types",
    "has_persistent_weak_layers",
    "has_gliding_snow",
    "unique_aspects_count",
    "min_lower_elevation",
)


class Command(BaseCommand):
    """Export a CSV of day-character labels and the inputs that fed them."""

    help = (
        "Emit one CSV row per Bulletin capturing the day-character label "
        "and every render_model input that drives the five-rule cascade. "
        "Read-only — defaults to stdout; pass --output to write a file."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--output",
            metavar="PATH",
            dest="output",
            help="Write CSV to this file path (overwrites). Default: stdout.",
        )
        parser.add_argument(
            "--start-date",
            metavar="YYYY-MM-DD",
            dest="start_date",
            help="Filter to bulletins with valid_from on or after this date.",
        )
        parser.add_argument(
            "--end-date",
            metavar="YYYY-MM-DD",
            dest="end_date",
            help="Filter to bulletins with valid_from on or before this date.",
        )
        parser.add_argument(
            "--lang",
            dest="lang",
            help=(
                "Filter to a single bulletin language (e.g. de, fr, it, en). "
                "Default: emit every language."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Build the queryset, write the CSV, exit non-zero on any row failure."""
        start_date = self._parse_date(options.get("start_date"), "--start-date")
        end_date = self._parse_date(options.get("end_date"), "--end-date")
        lang: str | None = options.get("lang")
        output_path: str | None = options.get("output")

        queryset = Bulletin.objects.prefetch_related("regions").order_by("valid_from")
        if start_date is not None:
            queryset = queryset.filter(valid_from__date__gte=start_date)
        if end_date is not None:
            queryset = queryset.filter(valid_from__date__lte=end_date)
        if lang is not None:
            queryset = queryset.filter(lang=lang)

        failures = self._write_csv(queryset, output_path)

        if failures:
            self.stderr.write(
                self.style.ERROR(
                    f"{failures} bulletin(s) raised while computing day_character; "
                    "see warnings above."
                )
            )
            sys.exit(1)

    def _write_csv(
        self,
        queryset: Any,
        output_path: str | None,
    ) -> int:
        """
        Stream rows into a ``csv.writer``, returning a count of failures.

        Opens ``output_path`` for writing if given; otherwise writes to
        ``self.stdout``. Each bulletin that raises while building its row
        is reported to stderr and counted as a failure — the export
        continues so a single malformed render_model can't block the rest.
        """
        rows_written = 0
        failures = 0

        if output_path:
            path = Path(output_path)
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh, lineterminator="\n")
                writer.writerow(CSV_HEADERS)
                rows_written, failures = self._stream_rows(queryset, writer)
            self.stderr.write(f"Wrote {rows_written} row(s) to {path}.")
        else:
            writer = csv.writer(self.stdout, lineterminator="\n")
            writer.writerow(CSV_HEADERS)
            rows_written, failures = self._stream_rows(queryset, writer)

        return failures

    def _stream_rows(
        self,
        queryset: Any,
        writer: Any,
    ) -> tuple[int, int]:
        """
        Walk ``queryset`` and feed one CSV row per bulletin into ``writer``.

        Uses ``iterator(chunk_size=...)`` so the prefetched-regions cache is
        flushed periodically rather than buffered for the whole archive.
        Returns ``(rows_written, failures)``.
        """
        rows_written = 0
        failures = 0
        for bulletin in queryset.iterator(chunk_size=500):
            try:
                writer.writerow(_row_for_bulletin(bulletin))
                rows_written += 1
            except Exception:
                failures += 1
                logger.exception(
                    "Failed to build CSV row for bulletin %s",
                    bulletin.bulletin_id,
                )
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipped bulletin {bulletin.bulletin_id!r}: row build raised."
                    )
                )
        return rows_written, failures

    @staticmethod
    def _parse_date(value: str | None, flag: str) -> dt.date | None:
        """Parse a YYYY-MM-DD string or raise ``CommandError``."""
        if value is None:
            return None
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(
                f"Invalid {flag} value {value!r}; expected YYYY-MM-DD."
            ) from exc


def _row_for_bulletin(bulletin: Bulletin) -> list[Any]:
    """
    Build one CSV row from a ``Bulletin`` instance.

    Reuses :func:`compute_day_character` for the label so the export
    cannot drift from the live cascade. The remaining columns expose the
    raw render_model inputs the cascade reads (danger, subdivision,
    problem types, aspects, elevation), so each row stays
    hand-verifiable against the rules without consulting the source
    bulletin.
    """
    render_model: dict[str, Any] = bulletin.render_model or {}

    danger_info = render_model.get("danger") or {}
    danger_number = str(danger_info.get("number") or "")
    danger_subdivision = danger_info.get("subdivision") or ""

    traits: list[dict[str, Any]] = render_model.get("traits") or []
    problems: list[dict[str, Any]] = [
        p for trait in traits for p in (trait.get("problems") or [])
    ]
    problem_types_seen: list[str] = sorted(
        {str(pt) for p in problems if (pt := p.get("problem_type"))}
    )

    unique_aspects: set[str] = set()
    lower_elevations: list[int] = []
    for problem in problems:
        for aspect in problem.get("aspects") or []:
            if aspect:
                unique_aspects.add(aspect)
        elevation = problem.get("elevation")
        if isinstance(elevation, dict):
            lower = elevation.get("lower")
            if isinstance(lower, int):
                lower_elevations.append(lower)

    min_lower = min(lower_elevations) if lower_elevations else ""

    day_char = compute_day_character(render_model)
    region_ids = sorted(bulletin.regions.values_list("region_id", flat=True))

    return [
        bulletin.bulletin_id,
        bulletin.valid_from.date().isoformat() if bulletin.valid_from else "",
        bulletin.valid_to.date().isoformat() if bulletin.valid_to else "",
        bulletin.lang,
        len(region_ids),
        ";".join(region_ids),
        _day_character_key(day_char),
        str(day_char.explainer),
        danger_number,
        danger_subdivision,
        len(traits),
        len(problems),
        ";".join(problem_types_seen),
        _bool_csv(any(t == "persistent_weak_layers" for t in problem_types_seen)),
        _bool_csv(any(t == "gliding_snow" for t in problem_types_seen)),
        len(unique_aspects),
        min_lower,
    ]


def _day_character_key(dc: DayCharacter) -> str:
    """
    Return the canonical key for a ``DayCharacter`` instance.

    ``compute_day_character`` returns one of the values in
    ``_DAY_CHARACTER`` directly, so identity comparison is safe and
    avoids depending on the localised ``label`` proxy that varies with
    the active gettext locale.
    """
    for key, value in _DAY_CHARACTER.items():
        if value is dc:
            return key
    return "unknown"


def _bool_csv(value: bool) -> str:
    """Render a bool as ``true``/``false`` for CSV consumers."""
    return "true" if value else "false"
