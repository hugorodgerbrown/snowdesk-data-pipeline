r"""
bulletins/management/commands/fetch_euregio_bulletins.py — Management command.

Fetches EUREGIO/ALBINA avalanche bulletins from the ALBINA CDN across a date
range and (optionally) persists them to the database. Mirrors the flag set of
``fetch_bulletins`` so the two commands share the same operational model.

The ALBINA CDN publishes per-date, per-region CAAMLv6 files. This command
walks the Cartesian product of ``[start..end] × EUREGIO_REGIONS``,
deduplicating bulletins by ``bulletinID`` so cross-region bulletins are only
stored once. A 404 for a given (date, region) slot is treated as "no data
for this slot" — not an error.

Defaults:
  * ``--start-date`` falls back to the ``valid_from`` day of the most recent
    EUREGIO bulletin already in the DB, or ``settings.SEASON_START_DATE`` when
    no EUREGIO bulletin has been stored yet.
  * ``--end-date`` defaults to today (UTC).
  * No writes happen unless ``--commit`` is passed (read-only by default).
  * ``--source`` picks ``live`` (default) or ``local-mirror``.
  * ``--stash`` captures fetched bulletins into ``EUREGIO_ARCHIVE_PATH``.

Usage::

    # Read-only walk — the "what would happen?" probe.
    python manage.py fetch_euregio_bulletins

    # Persist today's bulletins.
    python manage.py fetch_euregio_bulletins --date 2026-01-15 --commit

    # Backfill a window.
    python manage.py fetch_euregio_bulletins \\
        --start-date 2026-01-01 --end-date 2026-01-31 --commit

    # Re-pull existing rows.
    python manage.py fetch_euregio_bulletins --commit --force

    # Bootstrap an empty DB end-to-end against the local mirror.
    python manage.py fetch_euregio_bulletins --source local-mirror --commit

    # Multi-year backfill with rate limiting.
    python manage.py fetch_euregio_bulletins \\
        --start-date 2025-11-01 --end-date 2026-01-15 \\
        --delay 0.5 --commit
"""

import argparse
import json
import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.services.euregio_fetcher import latest_euregio_date, run_euregio_pipeline
from bulletins.services.weather_fetcher import SOURCE_LIVE, SOURCE_LOCAL_MIRROR

logger = logging.getLogger(__name__)

_START_SOURCE_EXPLICIT = "explicit"
_START_SOURCE_LATEST_EUREGIO = "latest_euregio_bulletin"
_START_SOURCE_SEASON_BACKSTOP = "season_backstop"

_SOURCE_CHOICES = (SOURCE_LIVE, SOURCE_LOCAL_MIRROR)


def _non_negative_float(raw: str) -> float:
    """
    Argparse ``type=`` helper for non-negative float arguments.

    Raises:
        argparse.ArgumentTypeError: if the value is unparseable or negative.

    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"delay must be non-negative (got {value})")
    return value


class Command(BaseCommand):
    """Fetch EUREGIO bulletins for a date range; read-only unless --commit."""

    help = (
        "Fetch EUREGIO/ALBINA bulletins for a date range. Defaults: "
        "--start-date=(latest EUREGIO bulletin's valid_from day) with "
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
                "day of the newest EUREGIO bulletin already in the DB, or "
                "settings.SEASON_START_DATE when no EUREGIO bulletin exists."
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
            default=SOURCE_LIVE,
            help=(
                "Where to fetch from. 'live' (default) hits the real ALBINA "
                "CDN; 'local-mirror' hits the development-only view at "
                "/dev/euregio-mirror/ that replays "
                "bulletins/local_mirrors/euregio_archive.ndjson."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched bulletin to "
                "bulletins/local_mirrors/euregio_archive.ndjson (deduped by "
                "bulletinID, sorted by validTime.startTime). Independent of "
                "--commit — combine them for a full-fidelity capture, or use "
                "--stash alone for a read-only archive refresh."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=0.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive CDN requests. "
                "Default 0 (no delay). Intended for multi-year backfills where "
                "being a good citizen on the public ALBINA CDN matters."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start, end, start_source = self._resolve_dates(options)
        commit: bool = options["commit"]
        force: bool = options["force"]
        stash: bool = options["stash"]
        source: str = options["source"]
        delay: float = options["delay"]
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
            delay=delay,
        )

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        try:
            run = run_euregio_pipeline(
                start=start,
                end=end,
                triggered_by="fetch_euregio_bulletins command",
                dry_run=not commit,
                force=force,
                base_url=base_url,
                on_fetched=on_fetched,
                delay=delay,
            )
        except Exception as exc:
            raise CommandError(f"EUREGIO pipeline failed: {exc}") from exc

        if run.status == "failed":
            raise CommandError(
                f"EUREGIO pipeline run {run.pk} failed: {run.error_message}"
            )

        if stash:
            self._flush_stash(collected)

        self._report_outcome(run, days, commit=commit)

        if run.records_failed > 0:
            raise CommandError(
                f"Run #{run.pk} completed with {run.records_failed} failure(s)."
            )

    @staticmethod
    def _resolve_source(source: str) -> str | None:
        """
        Map a ``--source`` choice to a base URL (or ``None`` for live).

        Returning ``None`` for the live source lets ``run_euregio_pipeline``
        fall back to ``settings.EUREGIO_API_BASE_URL``.

        Raises:
            CommandError: ``--source local-mirror`` was requested but
                ``settings.EUREGIO_API_LOCAL_MIRROR_URL`` is not configured.

        """
        if source == SOURCE_LIVE:
            return None
        mirror_url: str | None = getattr(settings, "EUREGIO_API_LOCAL_MIRROR_URL", None)
        if not mirror_url:
            raise CommandError(
                "--source local-mirror requires "
                "settings.EUREGIO_API_LOCAL_MIRROR_URL to be configured. "
                "The mirror is only available in development.py."
            )
        return mirror_url

    def _flush_stash(self, collected: list[dict[str, Any]]) -> None:
        """
        Merge collected bulletins into the on-disk EUREGIO archive.

        Reads the existing archive, overlays the freshly-collected records
        (later ``bulletinID`` wins), sorts ascending by
        ``validTime.startTime``, and writes the result back atomically.

        """
        path = settings.EUREGIO_ARCHIVE_PATH

        # Read existing records.
        existing: dict[str, dict[str, Any]] = {}
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        record = json.loads(stripped)
                        bid = record.get("bulletinID", "")
                        if bid:
                            existing[bid] = record

        # Overlay new records (newer wins).
        for record in collected:
            bid = record.get("bulletinID", "")
            if bid:
                existing[bid] = record

        # Sort by validTime.startTime ascending.
        merged = sorted(
            existing.values(),
            key=lambda r: (r.get("validTime") or {}).get("startTime", ""),
        )

        with path.open("w", encoding="utf-8") as fh:
            for record in merged:
                fh.write(json.dumps(record) + "\n")

        self.stdout.write(
            self.style.SUCCESS(
                f"Stashed {len(collected)} fetched bulletin(s) to {path}; "
                f"archive now contains {len(merged)} record(s)."
            )
        )
        logger.info(
            "fetch_euregio_bulletins stash flush: collected=%d "
            "archive_total=%d path=%s",
            len(collected),
            len(merged),
            path,
        )

    def _resolve_dates(self, options: dict[str, Any]) -> tuple[date, date, str]:
        """
        Collapse the --date / --start-date / --end-date options into a range.

        When ``--start-date`` is not provided (and ``--date`` is not used),
        the start is chosen dynamically: the ``valid_from`` day of the
        newest EUREGIO bulletin in the DB. An empty DB falls back to
        ``settings.SEASON_START_DATE``.

        Raises:
            CommandError: if ``--date`` is combined with the range flags, or
                if the resolved end precedes the resolved start.

        Returns:
            A tuple of ``(start, end, start_source)`` where ``start_source``
            describes how the default was picked.

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
            raise CommandError(
                f"--end-date ({end}) must not precede --start-date ({start})."
            )

        return start, end, start_source

    def _default_start_date(self) -> tuple[date, str]:
        """
        Derive the default start date for an unattended run.

        Uses the ``valid_from`` day of the most recent EUREGIO bulletin if
        one exists; otherwise falls back to ``settings.SEASON_START_DATE``.

        Returns:
            A ``(start_date, source_label)`` tuple.

        """
        latest = latest_euregio_date()
        if latest is not None:
            return latest, _START_SOURCE_LATEST_EUREGIO
        return settings.SEASON_START_DATE, _START_SOURCE_SEASON_BACKSTOP

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
        delay: float,
    ) -> None:
        """
        Print a heading summarising what this run will do.

        Args:
            start: Start date (inclusive).
            end: End date (inclusive).
            days: Total number of days in the range.
            commit: Whether writes are enabled.
            force: Whether existing bulletins will be re-fetched.
            start_source: How the start date was determined.
            source: "live" or "local-mirror".
            stash: Whether fetched bulletins will be appended to the archive.
            delay: Seconds between CDN requests.

        """
        mode = "COMMIT" if commit else "DRY-RUN"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching EUREGIO bulletins {start} → {end} ({days} day(s)) [{mode}]"
            )
        )

        start_hint = {
            _START_SOURCE_EXPLICIT: "explicit",
            _START_SOURCE_LATEST_EUREGIO: "latest EUREGIO bulletin in DB",
            _START_SOURCE_SEASON_BACKSTOP: "SEASON_START_DATE (empty DB)",
        }.get(start_source, start_source)

        self.stdout.write(
            f"  source={source}  start_from={start_hint}  "
            f"force={force}  stash={stash}  delay={delay}s"
        )
        logger.info(
            "fetch_euregio_bulletins: range=%s–%s days=%d "
            "source=%s start_source=%s commit=%s force=%s stash=%s delay=%s",
            start,
            end,
            days,
            source,
            start_source,
            commit,
            force,
            stash,
            delay,
        )

    def _report_outcome(self, run: Any, days: int, *, commit: bool) -> None:
        """
        Print a success summary after the pipeline finishes.

        Args:
            run: The completed ``PipelineRun`` instance.
            days: Total days in the requested range.
            commit: Whether writes were enabled.

        """
        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry-run complete — pass --commit to persist changes."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {run.records_created} created, "
                f"{run.records_updated} updated, "
                f"{run.records_failed} failed "
                f"across {days} day(s)."
            )
        )
        logger.info(
            "fetch_euregio_bulletins done: run=%s created=%d updated=%d failed=%d",
            run.pk,
            run.records_created,
            run.records_updated,
            run.records_failed,
        )
