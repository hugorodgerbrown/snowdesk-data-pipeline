"""
pipeline/management/commands/monitor_query_counts.py — Query-count monitor.

Hits a fixed list of representative URLs via the Django test client,
counts the SQL statements each one executes, and compares the numbers
against the committed baseline in ``perf/query_counts.txt``. This is the
SNOW-13 lightweight-monitoring tool — we store the numbers in-repo so a
PR diff surfaces any query-count regression the same way Lighthouse
diffs surface score regressions.

Read-only by default (per the project-wide management command
convention). ``--commit`` rewrites the baseline file; any other mismatch
exits non-zero so CI fails on an un-approved regression.

Typical use::

    # CI / local gate: fail on regression.
    poetry run python manage.py monitor_query_counts

    # After an intentional change to a monitored page:
    poetry run python manage.py monitor_query_counts --commit

The URL list is defined in ``MONITORED_URLS`` below — add an entry when
introducing a new page whose query count is worth watching.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

logger = logging.getLogger(__name__)

# Each tuple is (name, url_path). Names must be unique, stable, and safe
# for plain-text keys (no spaces). URLs are hit as-is; redirects are not
# followed so the measured count is for the immediate response only.
MONITORED_URLS: list[tuple[str, str]] = [
    ("home", "/"),
    ("map", "/map/"),
    ("api_today_summaries", "/api/today-summaries/"),
    ("api_resorts_by_region", "/api/resorts-by-region/"),
    ("api_regions_geojson", "/api/regions.geojson"),
    ("api_offline_manifest_map", "/api/offline-manifest/map/"),
    ("region_redirect", "/CH-4115/"),
    ("bulletin_historic", "/CH-4115/martigny-verbier/2026-04-01/"),
]

BASELINE_PATH = Path(settings.BASE_DIR) / "perf" / "query_counts.txt"

BASELINE_HEADER = (
    "# SNOW-13 query-count baseline — committed artefact.\n"
    "# Do not edit by hand; regenerate with:\n"
    "#   poetry run python manage.py monitor_query_counts --commit\n"
    "# Format: <name> <count>\n"
)


def _display_path(path: Path) -> str:
    """Return ``path`` relative to the repo root when possible."""
    try:
        return str(path.relative_to(settings.BASE_DIR))
    except ValueError:
        return str(path)


class Command(BaseCommand):
    """Measure per-URL query counts; compare to or overwrite the baseline."""

    help = (
        "Measure the SQL query count for a fixed list of monitored URLs "
        "and compare against perf/query_counts.txt. Read-only unless "
        "--commit is passed; any mismatch exits non-zero so CI fails on "
        "a query-count regression."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Overwrite perf/query_counts.txt with the newly measured "
                "counts. Without this flag the command is read-only and "
                "exits non-zero on any mismatch against the baseline."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the monitor and print a per-URL comparison report."""
        commit: bool = options["commit"]

        measured = self._measure_all()
        baseline = self._load_baseline()

        self._print_report(measured, baseline)

        if commit:
            self._write_baseline(measured)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {len(measured)} entries to {_display_path(BASELINE_PATH)}."
                )
            )
            return

        if baseline is None:
            raise CommandError(
                f"No baseline at {_display_path(BASELINE_PATH)}. "
                "Run with --commit to create it."
            )

        regressions = self._regressions(measured, baseline)
        if regressions:
            raise CommandError(
                f"{len(regressions)} URL(s) differ from the baseline. "
                "Fix the regression, or re-run with --commit to accept "
                "the new numbers."
            )

        self.stdout.write(self.style.SUCCESS("All counts match the baseline."))

    def _measure_all(self) -> dict[str, int]:
        """Hit every monitored URL and return ``{name: query_count}``."""
        # ``localhost`` is the universally-allowed host across our settings
        # modules; the test-client default of ``testserver`` is rejected
        # by ``CommonMiddleware`` under non-dev settings.
        client = Client(HTTP_HOST="localhost")
        results: dict[str, int] = {}
        for name, url in MONITORED_URLS:
            # Clear cache so the count is for a cold request; otherwise
            # the second run in a row would see fewer queries than the
            # first, making the baseline order-sensitive.
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                response = client.get(url, follow=False)
            results[name] = len(ctx)
            logger.info(
                "monitor_query_counts: %s -> %s queries (status=%d)",
                name,
                len(ctx),
                response.status_code,
            )
        return results

    def _load_baseline(self) -> dict[str, int] | None:
        """
        Parse ``perf/query_counts.txt`` into a ``{name: count}`` dict.

        Returns ``None`` if the file does not exist. Blank lines and
        lines starting with ``#`` are ignored.
        """
        if not BASELINE_PATH.exists():
            return None
        baseline: dict[str, int] = {}
        for raw in BASELINE_PATH.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name, _, count_str = line.partition(" ")
            if not name or not count_str:
                raise CommandError(f"Malformed line in {BASELINE_PATH}: {raw!r}")
            baseline[name] = int(count_str.strip())
        return baseline

    def _write_baseline(self, measured: dict[str, int]) -> None:
        """Overwrite the baseline file with ``measured``."""
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = [BASELINE_HEADER]
        for name, count in measured.items():
            lines.append(f"{name} {count}\n")
        BASELINE_PATH.write_text("".join(lines))

    def _regressions(
        self,
        measured: dict[str, int],
        baseline: dict[str, int],
    ) -> list[tuple[str, int | None, int]]:
        """
        Return ``(name, baseline, measured)`` for every differing URL.

        A URL is considered regressed if: the name is not in the baseline,
        the name is not in ``measured`` (URL was removed), or the counts
        differ. Any of these is grounds to require ``--commit``.
        """
        diffs: list[tuple[str, int | None, int]] = []
        for name, count in measured.items():
            if baseline.get(name) != count:
                diffs.append((name, baseline.get(name), count))
        for name in baseline:
            if name not in measured:
                diffs.append((name, baseline[name], 0))
        return diffs

    def _print_report(
        self,
        measured: dict[str, int],
        baseline: dict[str, int] | None,
    ) -> None:
        """Print a per-URL comparison table to stdout."""
        width = max((len(n) for n in measured), default=10)
        self.stdout.write(f"{'url':<{width}}  {'baseline':>9}  {'measured':>9}  status")
        self.stdout.write("-" * (width + 30))
        for name, count in measured.items():
            base = baseline.get(name) if baseline else None
            if base is None:
                status = "NEW"
            elif base == count:
                status = "OK"
            elif count > base:
                status = f"+{count - base}"
            else:
                status = f"-{base - count}"
            base_str = str(base) if base is not None else "—"
            self.stdout.write(f"{name:<{width}}  {base_str:>9}  {count:>9}  {status}")
