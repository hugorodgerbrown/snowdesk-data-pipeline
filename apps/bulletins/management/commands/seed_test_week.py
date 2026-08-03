"""
apps/bulletins/management/commands/seed_test_week.py — Seed the "golden week".

Loads seven consecutive real days of bulletins — all three providers, full
regional coverage — from the committed archives under
``apps/bulletins/local_mirrors/`` into the current database (SNOW-528).

This is the realistic-corpus counterpart to ``seed_test_data``, which builds a
navigable CH-only dataset from factories. The two are deliberately separate
commands rather than one command with a ``--include golden-week`` layer:
``seed_test_data``'s ``SeedModel`` enumeration names *models*, while the golden
week seeds the same models from a different *source*, so folding it in would
have made ``--include golden-week bulletin`` a contradiction and left
``--all`` carrying a special-case exclusion to protect the SNOW-13 query-count
baseline. Keeping the commands apart makes that baseline safe structurally.

Nothing is fetched: the full 2025/26 season is already committed and
git-tracked, so this command needs no network access and no running dev server.
See ``apps/bulletins/services/golden_week.py`` for how the week was chosen and how
each provider's target day is resolved.

Region reference data is a **prerequisite** — the week spans Switzerland,
Austria, South Tyrol and the French massifs::

    uv run python manage.py loaddata eaws_CH eaws_AT eaws_IT eaws_FR resorts
    uv run python manage.py seed_test_week --commit

Read-only by default (reports what would be loaded); pass ``--commit`` to
persist. Exits non-zero when any record fails to ingest, so CI and cron
surface a partially-failed load.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.services.golden_week import (
    GOLDEN_WEEK_START,
    REQUIRED_REGION_FIXTURES,
    SOURCES,
    GoldenWeekResult,
    golden_week_dates,
    load_golden_week,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Seed the golden week of real bulletins; read-only unless --commit.

    SNOW-602 exempt: a fixed-size seed spec (seven days, three providers),
    not a queryset over a growable table.
    """

    help = (
        "Load seven consecutive real days of bulletins (SLF, ALBINA, "
        "Météo-France) from the committed local_mirrors archives. Read-only "
        "by default; pass --commit to persist. Requires the eaws_CH/AT/IT/FR "
        "region fixtures to be loaded first."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist rows to the DB. Without this flag the command is read-only.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command.

        Raises:
            CommandError: If ``DEBUG`` is off, if the region reference data is
                missing, or if any record failed to ingest.

        """
        if not settings.DEBUG:
            raise CommandError("This command is only available when DEBUG=True.")

        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        self._assert_regions_loaded()

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] Dry-run — no rows written. Pass --commit to persist."
                )
            )

        dates = golden_week_dates()
        if verbosity >= 1:
            self.stdout.write(
                f"Golden week: {dates[0]:%Y-%m-%d} → {dates[-1]:%Y-%m-%d} "
                f"({len(dates)} days, {len(SOURCES)} providers)"
            )

        run = self._start_run(commit)
        try:
            result = load_golden_week(run=run, commit=commit)
        except Exception as exc:
            if run is not None:
                run.mark_failed(exc)
            raise CommandError(f"Golden-week load failed: {exc}") from exc

        if run is not None:
            run.mark_success(result.created, result.updated)

        self._report(result, commit=commit, verbosity=verbosity)

    def _report(
        self, result: GoldenWeekResult, *, commit: bool, verbosity: int
    ) -> None:
        """Print the load summary and fail the command on a partial batch.

        Args:
            result: Counters returned by ``load_golden_week``.
            commit: Whether rows were persisted.
            verbosity: Verbosity level.

        Raises:
            CommandError: If any record failed to ingest.

        """
        if verbosity >= 1:
            self.stdout.write("Records selected:")
            for source in SOURCES:
                self.stdout.write(f"  {source}: {result.selected.get(source, 0)}")

        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry-run complete — {result.total_selected} record(s) would be "
                    "loaded. Pass --commit to persist."
                )
            )
            return

        # A partially-failed load is still a failure: cron and CI must be able
        # to detect it (management-command contract rule 4).
        if result.failed:
            raise CommandError(
                f"Golden-week load completed with failures — {result.as_summary()}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Loaded golden week: {result.as_summary()}")
        )

    def _assert_regions_loaded(self) -> None:
        """Fail early with a copy-pasteable fix when region data is missing.

        ``upsert_bulletin`` skips regions it cannot resolve and raises only when
        a bulletin resolves *none* of them, so an unseeded database would
        otherwise produce a confusing partial load rather than a clear error.

        Raises:
            CommandError: If no MicroRegion rows exist.

        """
        from apps.regions.models import MicroRegion

        if MicroRegion.objects.exists():
            return
        fixtures = " ".join(REQUIRED_REGION_FIXTURES)
        raise CommandError(
            "No MicroRegion rows found. The golden week spans CH, AT, IT and FR, "
            "so load the region reference data first: "
            f"uv run python manage.py loaddata {fixtures} resorts"
        )

    def _start_run(self, commit: bool) -> Any:
        """Create and start a PipelineRun, or return ``None`` in dry-run mode.

        Args:
            commit: Whether the caller is persisting. A dry run writes nothing
                at all, including no PipelineRun row.

        Returns:
            The started ``PipelineRun``, or ``None`` when ``commit`` is False.

        """
        if not commit:
            return None

        from apps.bulletins.models import PipelineRun

        run = PipelineRun.objects.create(
            triggered_by=f"seed_test_week:{GOLDEN_WEEK_START:%Y-%m-%d}"
        )
        run.mark_running()
        return run
