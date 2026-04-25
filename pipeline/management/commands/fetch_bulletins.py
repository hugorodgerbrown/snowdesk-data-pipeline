"""
pipeline/management/commands/fetch_bulletins.py — Management command: fetch_bulletins.

Fetches SLF bulletins from the CAAML API across a date range and (optionally)
persists them to the database. Supersedes the previous fetch_data and
backfill_data commands.

Defaults are tuned for the unattended scheduled-run case:
  * --start-date defaults to the ``valid_from`` day of the most recent
    bulletin already in the DB — i.e. a one-day overlap so earlier-in-day
    issues (morning updates, prior-evening re-issues) are re-fetched. The
    duplicates are ignored downstream; it's the fetch that's being
    optimised, not the upsert. When the database is empty this falls back
    to settings.SEASON_START_DATE as a first-run backstop.
  * --end-date defaults to today (UTC).
  * No writes happen unless --commit is passed (read-only by default).
  * --source picks the upstream: ``live`` (default, real SLF API) or
    ``local-mirror`` (the dev-only view at ``/dev/slf-mirror/…`` that
    replays ``sample_data/slf_archive.ndjson``). The mirror lets an
    empty DB be re-populated end-to-end through the production fetch
    path against deterministic input — invaluable for tests and
    reproducible local environments.
  * --stash captures every fetched bulletin into the on-disk archive
    (independent of --commit; both ``--commit --stash`` and bare
    ``--stash`` are valid).

Usage:
    # Read-only walk over the whole season so far — the "what would happen?" probe.
    python manage.py fetch_bulletins

    # Persist a specific day (typical scheduled-run shape).
    python manage.py fetch_bulletins --date 2026-01-15 --commit

    # Backfill a window.
    python manage.py fetch_bulletins \
        --start-date 2026-01-01 --end-date 2026-01-31 --commit

    # Re-pull existing rows.
    python manage.py fetch_bulletins --commit --force

    # Capture the season into the on-disk archive without DB writes.
    python manage.py fetch_bulletins --stash

    # Bootstrap an empty DB end-to-end against the local mirror.
    python manage.py fetch_bulletins --source local-mirror --commit
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.models import Bulletin, PipelineRun
from pipeline.services.data_fetcher import run_pipeline
from pipeline.services.slf_archive import merge, read_archive, write_archive

logger = logging.getLogger(__name__)

_START_SOURCE_EXPLICIT = "explicit"
_START_SOURCE_LATEST_BULLETIN = "latest_bulletin"
_START_SOURCE_SEASON_BACKSTOP = "season_backstop"

_SOURCE_LIVE = "live"
_SOURCE_LOCAL_MIRROR = "local-mirror"
_SOURCE_CHOICES = (_SOURCE_LIVE, _SOURCE_LOCAL_MIRROR)


class Command(BaseCommand):
    """Fetch SLF bulletins for a date range; read-only unless --commit."""

    help = (
        "Fetch SLF bulletins for a date range. Defaults: "
        "--start-date=(latest bulletin's valid_from day) with "
        "settings.SEASON_START_DATE as the empty-DB backstop, "
        "--end-date=today. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--start-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "First date to fetch (inclusive). Default: the valid_from "
                "day of the newest bulletin already in the DB, or "
                "settings.SEASON_START_DATE when the DB is empty."
            ),
        )
        parser.add_argument(
            "--end-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help="Last date to fetch (inclusive). Default: today (UTC).",
        )
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Single-day shortcut — sets both start and end to this date. "
                "Mutually exclusive with --start-date / --end-date."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist changes to the database. "
                "Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Upsert existing bulletins instead of skipping them.",
        )
        parser.add_argument(
            "--source",
            choices=_SOURCE_CHOICES,
            default=_SOURCE_LIVE,
            help=(
                "Where to fetch from. 'live' (default) hits the real SLF "
                "CAAML API; 'local-mirror' hits the development-only view "
                "that replays sample_data/slf_archive.ndjson. The mirror "
                "is only available when settings.SLF_API_LOCAL_MIRROR_URL "
                "is configured (development.py)."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched bulletin to "
                "sample_data/slf_archive.ndjson (deduped by bulletinID, "
                "sorted by validTime.startTime). Independent of --commit "
                "— combine them for a full-fidelity capture, or use "
                "--stash alone for a read-only archive refresh."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start, end, start_source = self._resolve_dates(options)
        commit: bool = options["commit"]
        force: bool = options["force"]
        stash: bool = options["stash"]
        source: str = options["source"]
        base_url = self._resolve_source(source)
        days = (end - start).days + 1

        self._announce(
            start,
            end,
            days,
            commit=commit,
            force=force,
            start_source=start_source,
            source=source,
            stash=stash,
        )

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        try:
            run = run_pipeline(
                start=start,
                end=end,
                triggered_by="fetch_bulletins command",
                dry_run=not commit,
                force=force,
                base_url=base_url,
                on_fetched=on_fetched,
            )
        except Exception as exc:
            raise CommandError(f"Pipeline failed: {exc}") from exc

        if run.status == "failed":
            raise CommandError(f"Pipeline run {run.pk} failed: {run.error_message}")

        if stash:
            self._flush_stash(collected)

        self._report_outcome(run, days, commit=commit)

        if run.records_failed > 0:
            raise CommandError(
                f"Run #{run.pk} completed with {run.records_failed} render-model "
                f"failure(s). Bulletins were stored with version=0 error sentinels. "
                f"Run 'rebuild_render_models' after fixing the issue."
            )

    @staticmethod
    def _resolve_source(source: str) -> str | None:
        """
        Map a ``--source`` choice to a base URL (or ``None`` for live).

        Returning ``None`` for the live source lets ``run_pipeline``
        fall back to ``settings.SLF_API_BASE_URL``, keeping the live
        path identical to its pre-flag behaviour.

        Raises:
            CommandError: ``--source local-mirror`` was requested but
                ``settings.SLF_API_LOCAL_MIRROR_URL`` is not configured
                (i.e. running outside development.py).

        """
        if source == _SOURCE_LIVE:
            return None
        mirror_url: str | None = getattr(settings, "SLF_API_LOCAL_MIRROR_URL", None)
        if not mirror_url:
            raise CommandError(
                "--source local-mirror requires settings.SLF_API_LOCAL_MIRROR_URL "
                "to be configured. The mirror is only available in development.py."
            )
        return mirror_url

    def _flush_stash(self, collected: list[dict[str, Any]]) -> None:
        """
        Merge collected bulletins into the on-disk archive.

        Reads the existing archive, overlays the freshly-collected
        records (later wins by ``bulletinID``), sorts by
        ``validTime.startTime``, and atomically writes the result back
        to ``settings.SLF_ARCHIVE_PATH``.
        """
        path = settings.SLF_ARCHIVE_PATH
        existing = list(read_archive(path))
        merged = merge(existing, collected)
        write_archive(path, merged)
        self.stdout.write(
            self.style.SUCCESS(
                f"Stashed {len(collected)} fetched bulletin(s) to {path}; "
                f"archive now contains {len(merged)} record(s)."
            )
        )
        logger.info(
            "fetch_bulletins stash flush: collected=%d archive_total=%d path=%s",
            len(collected),
            len(merged),
            path,
        )

    def _resolve_dates(self, options: dict[str, Any]) -> tuple[date, date, str]:
        """
        Collapse the --date / --start-date / --end-date options into a range.

        When ``--start-date`` is not provided (and ``--date`` is not used),
        the start is chosen dynamically: the ``valid_from`` day of the
        newest bulletin in the DB. An empty DB falls back to
        ``settings.SEASON_START_DATE``.

        Raises:
            CommandError: if --date is combined with the range flags, or if
                the resolved end precedes the resolved start.

        Returns:
            A tuple of ``(start, end, start_source)`` where ``start_source``
            is one of ``_START_SOURCE_EXPLICIT``,
            ``_START_SOURCE_LATEST_BULLETIN``, or
            ``_START_SOURCE_SEASON_BACKSTOP`` — used by ``_announce`` to
            explain how the default was picked.

        """
        single: date | None = options["date"]
        start_arg: date | None = options["start_date"]
        end_arg: date | None = options["end_date"]

        if single is not None and (start_arg is not None or end_arg is not None):
            raise CommandError(
                "--date is mutually exclusive with --start-date / --end-date."
            )

        if single is not None:
            start = end = single
            start_source = _START_SOURCE_EXPLICIT
        else:
            end = end_arg or timezone.localdate()
            if start_arg is not None:
                start = start_arg
                start_source = _START_SOURCE_EXPLICIT
            else:
                start, start_source = self._default_start_date()

        if end < start:
            raise CommandError("--end-date must be on or after --start-date.")
        return start, end, start_source

    def _default_start_date(self) -> tuple[date, str]:
        """
        Pick a default start date when ``--start-date`` is not provided.

        If the DB has bulletins, returns the newest ``valid_from`` day. The
        same-day overlap is deliberate: it re-fetches earlier-in-day issues
        (morning updates, prior-evening re-issues) that may have been
        reissued since the last run. Duplicates are ignored downstream — it
        is the fetch that's being optimised, not the upsert.

        If the DB is empty, returns ``settings.SEASON_START_DATE`` as a
        first-run backstop so the full snowpack build-up is captured.

        Returns:
            ``(start, start_source)`` where ``start_source`` explains which
            branch produced the date.

        """
        latest = Bulletin.objects.latest_valid_from_date()
        if latest is None:
            return settings.SEASON_START_DATE, _START_SOURCE_SEASON_BACKSTOP
        return latest, _START_SOURCE_LATEST_BULLETIN

    def _announce(
        self,
        start: date,
        end: date,
        days: int,
        *,
        commit: bool,
        force: bool,
        start_source: str,
        source: str,
        stash: bool,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if force:
            flags.append("FORCE")
        if stash:
            flags.append("STASH")
        if source != _SOURCE_LIVE:
            flags.append(f"SOURCE={source.upper()}")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        start_label = self._start_source_label(start_source)
        start_suffix = f" — start {start_label}" if start_label else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching bulletins from {start} to {end} "
                f"({days} day(s)){flag_label}{start_suffix}"
            )
        )
        logger.info(
            "fetch_bulletins started: %s to %s, %d day(s), "
            "commit=%s, force=%s, stash=%s, source=%s, start_source=%s",
            start,
            end,
            days,
            commit,
            force,
            stash,
            source,
            start_source,
        )

    @staticmethod
    def _start_source_label(start_source: str) -> str:
        """Render a human-readable tag for the banner."""
        if start_source == _START_SOURCE_LATEST_BULLETIN:
            return "from latest bulletin valid_from day"
        if start_source == _START_SOURCE_SEASON_BACKSTOP:
            return "from SEASON_START_DATE backstop (empty DB)"
        return ""

    def _report_outcome(self, run: PipelineRun, days: int, *, commit: bool) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    "Read-only run complete — no data written. "
                    "Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. Run #{run.pk}: {run.records_created} created, "
                    f"{run.records_updated} updated across {days} day(s)."
                )
            )

        logger.info(
            "fetch_bulletins finished: run=%s status=%s "
            "created=%s updated=%s failed=%s",
            run.pk,
            run.status,
            run.records_created,
            run.records_updated,
            run.records_failed,
        )
