"""
bulletins/management/commands/parse_wet_snow_coverage.py — Diagnostic command.

Reports the hit rate of the wet-snow prose parser over the local bulletin
archive.  For each (lang, source) combination that contains wet-snow or
gliding-snow problems the command prints one summary line showing:

  wet_snow_total   — all wet-snow / gliding-snow problems encountered
  unstructured     — problems with no aspects AND no elevation in the JSON
  parser_match     — unstructured problems where the prose parser returned
                     a non-None ParsedScope
  aspect_match     — parser matches that also contain at least one aspect
  elevation_match  — parser matches that also contain a real elevation bound

The command reads ``raw_data`` (not ``render_model``) so the counts are
independent of whether the render model has been rebuilt after the v6 bump.

Pure SELECT — no ``--commit`` flag; the command never writes to the database.

Typical use::

    poetry run python manage.py parse_wet_snow_coverage
    poetry run python manage.py parse_wet_snow_coverage --verbosity 2
"""

from __future__ import annotations

import dataclasses
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

import bulletins.services.prose.en  # noqa: F401 — registers the "en" parser
from bulletins.models import Bulletin
from bulletins.services.prose import parse_for
from bulletins.services.render_model import WET_PROBLEM_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stats accumulator
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Stats:
    """Per-(lang, source) accumulator for wet-snow coverage metrics."""

    wet_snow_total: int = 0
    unstructured: int = 0
    parser_match: int = 0
    aspect_match: int = 0
    elevation_match: int = 0


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    """Report wet-snow prose parser hit rate over the local bulletin archive."""

    help = (
        "Scan the bulletin archive for wet-snow/gliding-snow problems and report "
        "the prose parser hit rate by (lang, source). Pure SELECT — no data is written."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        # No --commit needed — this command is read-only by design.
        # --verbosity is inherited from BaseCommand.
        parser.add_argument(
            "--source",
            dest="filter_source",
            choices=["slf", "albina", "meteofrance"],
            default=None,
            help=(
                "Restrict the scan to bulletins from a single source. "
                "Without this flag all sources are scanned."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the diagnostic and print the coverage report."""
        verbosity: int = options["verbosity"]
        filter_source: str | None = options.get("filter_source")

        stats, bulletin_count, problem_count = self._scan_archive(filter_source)

        if verbosity >= 2:
            self.stdout.write(
                f"Scanned {bulletin_count} bulletins, "
                f"{problem_count} wet-snow/gliding-snow problems."
            )

        if not stats:
            self.stdout.write(
                self.style.WARNING(
                    "No wet-snow or gliding-snow problems found in the archive."
                )
            )
            return

        self._print_report(stats, verbosity)

        logger.info(
            "parse_wet_snow_coverage finished: bulletins=%d "
            "wet_snow_problems=%d keys=%d",
            bulletin_count,
            problem_count,
            len(stats),
        )

    def _scan_archive(
        self,
        filter_source: str | None,
    ) -> tuple[dict[tuple[str, str], _Stats], int, int]:
        """
        Iterate the bulletin archive and accumulate wet-snow coverage stats.

        Source is detected from ``raw_data.properties.customData`` keys
        (the same logic as ``detect_source`` in render_model.py) rather
        than from a model field, because the Bulletin model does not store
        source as a dedicated database column.

        Args:
            filter_source: Optional source slug (``"slf"``, ``"albina"``,
                ``"meteofrance"``) to restrict the scan. When given, bulletins
                whose detected source does not match are silently skipped.

        Returns:
            A 3-tuple of (stats_dict, bulletin_count, problem_count).

        """
        stats: dict[tuple[str, str], _Stats] = {}
        bulletin_count = 0
        problem_count = 0

        for raw_data, lang in Bulletin.objects.values_list(
            "raw_data", "lang"
        ).iterator():
            properties = _get_properties(raw_data)
            if not properties:
                continue

            bulletin_source: str = _detect_source_slug(properties)
            if filter_source and bulletin_source != filter_source:
                continue

            bulletin_count += 1
            bulletin_lang: str = lang or properties.get("lang") or "en"
            key = (bulletin_lang, bulletin_source)

            for problem in properties.get("avalancheProblems") or []:
                pt: str = problem.get("problemType", "")
                if pt not in WET_PROBLEM_TYPES:
                    continue
                problem_count += 1
                _score_problem(problem, bulletin_lang, key, stats)

        return stats, bulletin_count, problem_count

    def _print_report(
        self,
        stats: dict[tuple[str, str], _Stats],
        verbosity: int,
    ) -> None:
        """Print the per-(lang, source) coverage table to stdout."""
        self.stdout.write(
            self.style.MIGRATE_HEADING("Wet-snow prose parser coverage report")
        )
        self.stdout.write("")

        header = (
            f"{'lang':<6}  {'source':<12}  "
            f"{'total':>7}  {'unstruct':>8}  "
            f"{'parsed':>7} {'(%)':>5}  "
            f"{'aspect':>7} {'(%)':>5}  "
            f"{'elev':>6} {'(%)':>5}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for (lang, source), s in sorted(stats.items()):
            parsed_pct = _pct(s.parser_match, s.unstructured)
            aspect_pct = _pct(s.aspect_match, s.unstructured)
            elev_pct = _pct(s.elevation_match, s.unstructured)

            line = (
                f"{lang:<6}  {source:<12}  "
                f"{s.wet_snow_total:>7}  {s.unstructured:>8}  "
                f"{s.parser_match:>7} {parsed_pct:>4}%  "
                f"{s.aspect_match:>7} {aspect_pct:>4}%  "
                f"{s.elevation_match:>6} {elev_pct:>4}%"
            )
            self.stdout.write(line)

            if verbosity >= 2:
                # Verbose: also show structured count for context.
                structured = s.wet_snow_total - s.unstructured
                self.stdout.write(
                    f"         already structured: {structured} "
                    f"({_pct(structured, s.wet_snow_total)}%)"
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_source_slug(properties: dict[str, Any]) -> str:
    """
    Detect the bulletin source from ``customData`` keys.

    Mirrors the logic in ``render_model.detect_source`` but returns a plain
    string slug instead of a ``Bulletin.Source`` enum member, so this module
    stays free of Django ORM imports beyond what the management command base
    already requires.

    Returns ``"unknown"`` when no recognised key is found (rather than
    raising, so the scanner is lenient over partial/test data).

    Args:
        properties: The CAAML properties dict.

    Returns:
        One of ``"slf"``, ``"albina"``, ``"meteofrance"``, or ``"unknown"``.

    """
    custom_data: dict[str, Any] = properties.get("customData") or {}
    if "ALBINA" in custom_data:
        return "albina"
    for key in custom_data:
        if key.startswith("LWD_"):
            return "albina"
    if "MF" in custom_data:
        return "meteofrance"
    if "CH" in custom_data:
        return "slf"
    return "unknown"


def _score_problem(
    problem: dict[str, Any],
    bulletin_lang: str,
    key: tuple[str, str],
    stats: dict[tuple[str, str], _Stats],
) -> None:
    """
    Score a single wet-snow problem against the prose parser and update stats.

    Args:
        problem: The raw CAAML avalanche problem dict.
        bulletin_lang: The bulletin's BCP-47 language code.
        key: The ``(lang, source)`` stats bucket key.
        stats: Mutable stats dict to update.

    """
    bucket = stats.setdefault(key, _Stats())
    bucket.wet_snow_total += 1

    aspects_raw: list[str] = problem.get("aspects") or []
    elevation_raw: Any = problem.get("elevation") or None
    comment_html: str = problem.get("comment") or ""

    # Already structured → count only, skip prose attempt.
    if aspects_raw or elevation_raw:
        return

    bucket.unstructured += 1

    if not comment_html.strip():
        return  # blank comment — parser cannot help

    parsed = parse_for(bulletin_lang, comment_html)
    if parsed is None:
        return

    bucket.parser_match += 1
    if parsed.aspects:
        bucket.aspect_match += 1
    elev = parsed.elevation
    if (
        elev.get("lower") is not None
        or elev.get("upper") is not None
        or elev.get("treeline")
    ):
        bucket.elevation_match += 1


def _get_properties(raw_data: Any) -> dict[str, Any] | None:
    """
    Extract the CAAML properties dict from a bulletin raw_data blob.

    Tolerates missing/malformed payloads silently.

    Args:
        raw_data: The ``Bulletin.raw_data`` value (GeoJSON Feature envelope).

    Returns:
        The ``"properties"`` dict, or ``None`` on failure.

    """
    if not isinstance(raw_data, dict):
        return None
    properties = raw_data.get("properties")
    if not isinstance(properties, dict):
        return None
    return properties


def _pct(numerator: int, denominator: int) -> int:
    """
    Return integer percentage of numerator/denominator, or 0 when denominator is 0.

    Args:
        numerator: The count to express as a percentage.
        denominator: The total count.

    Returns:
        Integer percentage in range 0–100.

    """
    if denominator == 0:
        return 0
    return round(100 * numerator / denominator)
