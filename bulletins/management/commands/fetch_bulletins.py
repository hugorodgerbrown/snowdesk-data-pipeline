"""
bulletins/management/commands/fetch_bulletins.py — Management command: fetch_bulletins.

Unified bulletin-fetch command that consolidates the former ``fetch_bulletins``
(SLF) and ``fetch_euregio_bulletins`` (EUREGIO) commands into a single entry
point. Providers are selected via a required ``--source`` flag; multiple
providers can be supplied in one invocation and are processed in the order
given. Failures are collected and surfaced at the end so a single provider
failure does not abort the others.

The ``--source`` flag is **case-insensitive**: ``--source slf``,
``--source SLF``, and ``--source Slf`` are all accepted and normalised to
the canonical upper-case provider key.

Defaults:
  * ``--source`` is required. Pass ``--source slf``, ``--source euregio``,
    ``--source meteofrance``, or any combination.
  * ``--start-date`` defaults to the ``valid_from`` day of the most recent
    bulletin already in the DB for each requested source — i.e. a one-day
    overlap so earlier-in-day issues (morning updates, prior-evening
    re-issues) are re-fetched. The duplicates are ignored downstream.
    When the DB is empty this falls back to ``settings.SEASON_START_DATE``.
  * End is always today (UTC). There is no ``--end-date`` flag.
  * No writes happen unless ``--commit`` is passed (read-only by default).
  * ``--local-mirror`` replaces the old ``--source local-mirror`` value.
    It switches every requested source to its corresponding dev-mirror URL.
  * ``--stash`` captures fetched bulletins into each source's on-disk archive
    (independent of ``--commit``).

Usage::

    # Read-only walk — the "what would happen?" probe.
    python manage.py fetch_bulletins --source slf

    # Persist today's SLF and EUREGIO bulletins (typical cron shape).
    python manage.py fetch_bulletins --source slf euregio --commit

    # Single-day SLF backfill.
    python manage.py fetch_bulletins --source slf --date 2026-01-15 --commit

    # Narrow to today only.
    python manage.py fetch_bulletins --source euregio --today --commit

    # Explicit window.
    python manage.py fetch_bulletins --source slf
        --start-date 2026-01-01 --commit

    # Re-pull existing rows.
    python manage.py fetch_bulletins --source slf euregio --commit --force

    # Capture fetched bulletins into the on-disk archives without DB writes.
    python manage.py fetch_bulletins --source slf euregio --stash

    # Bootstrap an empty DB against the local mirrors (dev server must be running).
    python manage.py fetch_bulletins --source slf --local-mirror --commit

    # MeteoFrance bulletins via local mirror directory (no API key required).
    python manage.py fetch_bulletins --source meteofrance --local-mirror

    # Multi-year backfill with rate limiting.
    python manage.py fetch_bulletins --source slf
        --start-date 2014-11-01 --delay 5 --commit
"""

import argparse
import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.models import PipelineRun
from bulletins.services.data_fetcher import (
    SOURCE_CHOICES,
    BulletinSource,
    get_sources,
)

logger = logging.getLogger(__name__)

_START_SOURCE_EXPLICIT = "explicit"
_START_SOURCE_LATEST_BULLETIN = "latest_bulletin"
_START_SOURCE_SEASON_BACKSTOP = "season_backstop"


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
    """Fetch bulletins from one or more providers; read-only unless --commit."""

    help = (
        "Fetch avalanche bulletins for a date range. "
        "--source is required; pass 'slf', 'euregio', 'meteofrance', or "
        "any combination (case-insensitive). "
        "Start defaults to the latest bulletin's valid_from day per source, "
        "or settings.SEASON_START_DATE when the DB is empty. "
        "End is always today (UTC). Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        _choice_display = "/".join(c.lower() for c in SOURCE_CHOICES)
        parser.add_argument(
            "--source",
            nargs="+",
            action="append",
            required=True,
            metavar="{" + _choice_display + "}",
            help=(
                "Provider(s) to fetch from. Accepts one or more of: "
                f"{_choice_display} (case-insensitive). "
                "Pass multiple values space-separated (``--source slf euregio``) "
                "or repeat the flag (``--source slf --source euregio``). "
                "Duplicates are silently deduplicated."
            ),
        )
        parser.add_argument(
            "--start-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "First date to fetch (inclusive). Default: the valid_from "
                "day of the newest bulletin already in the DB for each source, "
                "or settings.SEASON_START_DATE when the DB is empty. "
                "Mutually exclusive with --date and --today."
            ),
        )
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Single-day shortcut — sets both start and end to this date. "
                "Mutually exclusive with --start-date and --today."
            ),
        )
        parser.add_argument(
            "--today",
            action="store_true",
            help=(
                "Fetch today only (UTC). Equivalent to --date <today>. "
                "Mutually exclusive with --start-date and --date."
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
            "--local-mirror",
            action="store_true",
            help=(
                "Fetch from the dev-only local mirror for every requested source "
                "instead of the live API. Each source's mirror URL must be "
                "configured in settings (SLF_API_LOCAL_MIRROR_URL, "
                "EUREGIO_API_LOCAL_MIRROR_URL, "
                "METEOFRANCE_API_LOCAL_MIRROR_URL). Only available in development."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched bulletin to its source's on-disk archive "
                "(deduped by bulletinID, sorted by validTime.startTime). "
                "Independent of --commit — combine them for a full-fidelity "
                "capture, or use --stash alone for a read-only archive refresh."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=0.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive API/CDN page "
                "requests. Default 0 (no delay). Intended for multi-year "
                "backfills where pacing matters."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        sources_registry = get_sources()
        requested = _resolve_sources(options)
        commit: bool = options["commit"]
        force: bool = options["force"]
        stash: bool = options["stash"]
        local_mirror: bool = options["local_mirror"]
        delay: float = options["delay"]

        failures: list[str] = []

        for source_name in requested:
            source = sources_registry[source_name]
            try:
                self._run_source(
                    source,
                    options=options,
                    commit=commit,
                    force=force,
                    stash=stash,
                    local_mirror=local_mirror,
                    delay=delay,
                )
            except CommandError as exc:
                # Record the failure but continue with remaining sources.
                failures.append(f"{source_name}: {exc}")
                logger.error("fetch_bulletins source=%s failed: %s", source_name, exc)

        if failures:
            joined = "; ".join(failures)
            raise CommandError(f"One or more sources failed — {joined}")

    def _run_source(
        self,
        source: BulletinSource,
        *,
        options: dict[str, Any],
        commit: bool,
        force: bool,
        stash: bool,
        local_mirror: bool,
        delay: float,
    ) -> None:
        """
        Run the full fetch-and-persist cycle for a single provider.

        Resolves the date range and base URL for this source, invokes the
        pipeline function, flushes the stash if requested, and raises
        ``CommandError`` if the run failed or had records_failed > 0.

        Args:
            source: The ``BulletinSource`` registry entry for this provider.
            options: The raw ``options`` dict from ``handle()``.
            commit: Whether to persist to the database.
            force: Whether to upsert existing bulletins.
            stash: Whether to flush fetched records to the on-disk archive.
            local_mirror: Whether to use the dev-mirror URL.
            delay: Seconds to sleep between requests.

        Raises:
            CommandError: If the pipeline raises, returns a failed status, or
                completes with ``records_failed > 0``.

        """
        start, end, start_source = self._resolve_dates(options, source)
        base_url = self._resolve_base_url(source, local_mirror)
        days = (end - start).days + 1

        self._announce(
            source,
            start,
            end,
            days,
            commit=commit,
            force=force,
            start_source=start_source,
            local_mirror=local_mirror,
            stash=stash,
            delay=delay,
        )

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        try:
            run = source.pipeline_fn(
                start=start,
                end=end,
                triggered_by=f"fetch_bulletins command [{source.name}]",
                dry_run=not commit,
                force=force,
                base_url=base_url,
                on_fetched=on_fetched,
                delay=delay,
            )
        except Exception as exc:
            raise CommandError(f"{source.name} pipeline failed: {exc}") from exc

        if run.status == PipelineRun.Status.FAILED:
            raise CommandError(
                f"{source.name} pipeline run {run.pk} failed: {run.error_message}"
            )

        if stash:
            try:
                self._flush_stash(source, collected)
            except Exception as exc:
                raise CommandError(f"{source.name} stash flush failed: {exc}") from exc

        self._report_outcome(source, run, days, commit=commit)

        if run.records_failed > 0:
            raise CommandError(
                f"{source.name} run #{run.pk} completed with "
                f"{run.records_failed} render-model failure(s). "
                f"Bulletins were stored with version=0 error sentinels. "
                f"Run 'rebuild_render_models' after fixing the issue."
            )

    def _resolve_dates(
        self,
        options: dict[str, Any],
        source: BulletinSource,
    ) -> tuple[date, date, str]:
        """
        Resolve the start/end date range from the command options.

        Validates mutual exclusion between ``--date``, ``--start-date``,
        and ``--today``. End is always today (UTC). Start is derived from
        ``--date`` / ``--today`` / ``--start-date`` / the source's
        ``latest_date_fn`` / ``settings.SEASON_START_DATE`` in that order.

        Args:
            options: The raw options dict from ``handle()``.
            source: The provider whose ``latest_date_fn`` is used as
                the default start fallback.

        Returns:
            A ``(start, end, start_source)`` triple where ``start_source``
            is one of ``_START_SOURCE_EXPLICIT``,
            ``_START_SOURCE_LATEST_BULLETIN``, or
            ``_START_SOURCE_SEASON_BACKSTOP``.

        Raises:
            CommandError: if more than one of ``--date``, ``--start-date``,
                and ``--today`` are supplied together.

        """
        single: date | None = options["date"]
        start_arg: date | None = options["start_date"]
        today_flag: bool = options["today"]

        # Mutual exclusion check.
        exclusive_flags = sum(
            [
                single is not None,
                start_arg is not None,
                today_flag,
            ]
        )
        if exclusive_flags > 1:
            raise CommandError(
                "--date, --start-date, and --today are mutually exclusive."
            )

        end = timezone.localdate()

        if single is not None:
            return single, single, _START_SOURCE_EXPLICIT

        if today_flag:
            return end, end, _START_SOURCE_EXPLICIT

        if start_arg is not None:
            if end < start_arg:
                raise CommandError(
                    f"--start-date ({start_arg}) is after today ({end})."
                )
            return start_arg, end, _START_SOURCE_EXPLICIT

        # Dynamic default: latest bulletin date or season backstop.
        start, start_source = self._default_start_date(source)
        if end < start:
            raise CommandError(f"Resolved start ({start}) is after today ({end}).")
        return start, end, start_source

    @staticmethod
    def _default_start_date(source: BulletinSource) -> tuple[date, str]:
        """
        Derive the default start date using the source's latest-date function.

        Args:
            source: The provider whose ``latest_date_fn`` to call.

        Returns:
            ``(start_date, start_source)`` where ``start_source`` is
            ``_START_SOURCE_LATEST_BULLETIN`` when a bulletin exists, or
            ``_START_SOURCE_SEASON_BACKSTOP`` when the DB is empty.

        """
        latest = source.latest_date_fn()
        if latest is None:
            return settings.SEASON_START_DATE, _START_SOURCE_SEASON_BACKSTOP
        return latest, _START_SOURCE_LATEST_BULLETIN

    @staticmethod
    def _resolve_base_url(source: BulletinSource, local_mirror: bool) -> str | None:
        """
        Resolve the base URL to pass to the pipeline function.

        Returns ``None`` for the live path so the pipeline falls back to
        the provider's ``live_url_setting``. Returns the mirror URL when
        ``--local-mirror`` is set, raising ``CommandError`` if the mirror
        URL setting is missing.

        Args:
            source: The provider whose URL settings to read.
            local_mirror: Whether the user requested the dev mirror.

        Returns:
            The mirror URL string, or ``None`` for the live path.

        Raises:
            CommandError: ``--local-mirror`` was requested but the mirror
                URL setting is absent or falsy.

        """
        if not local_mirror:
            return None
        mirror_url: str | None = getattr(settings, source.mirror_url_setting, None)
        if not mirror_url:
            raise CommandError(
                f"--local-mirror requires settings.{source.mirror_url_setting} "
                f"to be configured (only available in development)."
            )
        return mirror_url

    def _flush_stash(
        self,
        source: BulletinSource,
        collected: list[dict[str, Any]],
    ) -> None:
        """
        Merge collected bulletins into the source's on-disk archive.

        Delegates to ``source.stash_writer`` which handles the
        provider-specific merge and write logic.

        Args:
            source: The provider whose archive to update.
            collected: Raw bulletin dicts collected by the ``--stash``
                callback during the pipeline run.

        """
        path = getattr(settings, source.archive_path_setting)
        archive_size = source.stash_writer(collected, path)
        self.stdout.write(
            self.style.SUCCESS(
                f"[{source.name}] Stashed {len(collected)} fetched bulletin(s) "
                f"to {path}; archive now contains {archive_size} record(s)."
            )
        )
        logger.info(
            "fetch_bulletins stash flush: source=%s collected=%d "
            "archive_total=%d path=%s",
            source.name,
            len(collected),
            archive_size,
            path,
        )

    def _announce(
        self,
        source: BulletinSource,
        start: date,
        end: date,
        days: int,
        *,
        commit: bool,
        force: bool,
        start_source: str,
        local_mirror: bool,
        stash: bool,
        delay: float,
    ) -> None:
        """
        Write the start-of-run banner and matching log line for one source.

        Args:
            source: The provider being fetched.
            start: Start date (inclusive).
            end: End date (inclusive).
            days: Total number of days in the range.
            commit: Whether writes are enabled.
            force: Whether existing bulletins will be re-fetched.
            start_source: How the start date was determined.
            local_mirror: Whether the dev mirror is active.
            stash: Whether fetched bulletins will be appended to the archive.
            delay: Seconds between requests.

        """
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if force:
            flags.append("FORCE")
        if stash:
            flags.append("STASH")
        if local_mirror:
            flags.append("LOCAL-MIRROR")
        if delay > 0:
            flags.append(f"DELAY={delay:g}s")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        start_label = _start_source_label(start_source)
        start_suffix = f" — start {start_label}" if start_label else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"[{source.name}] Fetching bulletins from {start} to {end} "
                f"({days} day(s)){flag_label}{start_suffix}"
            )
        )
        logger.info(
            "fetch_bulletins started: source=%s %s to %s, %d day(s), "
            "commit=%s, force=%s, stash=%s, local_mirror=%s, delay=%s, "
            "start_source=%s",
            source.name,
            start,
            end,
            days,
            commit,
            force,
            stash,
            local_mirror,
            delay,
            start_source,
        )

    def _report_outcome(
        self,
        source: BulletinSource,
        run: PipelineRun,
        days: int,
        *,
        commit: bool,
    ) -> None:
        """
        Emit the post-run summary to stdout and the structured log.

        Args:
            source: The provider that was fetched.
            run: The completed ``PipelineRun`` instance.
            days: Total days in the requested range.
            commit: Whether writes were enabled.

        """
        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{source.name}] Read-only run complete — no data written. "
                    f"Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{source.name}] Done. Run #{run.pk}: "
                    f"{run.records_created} created, "
                    f"{run.records_updated} updated across {days} day(s)."
                )
            )

        logger.info(
            "fetch_bulletins finished: source=%s run=%s status=%s "
            "created=%s updated=%s failed=%s",
            source.name,
            run.pk,
            run.status,
            run.records_created,
            run.records_updated,
            run.records_failed,
        )


def _resolve_sources(options: dict[str, Any]) -> list[str]:
    """
    Flatten, normalise, validate, and deduplicate the ``--source`` list-of-lists.

    ``action="append"`` with ``nargs="+"`` means ``options["source"]`` is
    a list of lists, e.g. ``[["slf", "euregio"]]`` or
    ``[["slf"], ["euregio"]]``. Flatten to a plain list, upper-case each
    value so the flag is case-insensitive (``--source meteofrance`` and
    ``--source METEOFRANCE`` both work), then validate against
    ``SOURCE_CHOICES`` and raise ``CommandError`` for unrecognised names.
    Deduplication preserves first-seen order.

    Args:
        options: The raw options dict from ``handle()``.

    Returns:
        An ordered, deduplicated list of upper-case provider name strings.

    Raises:
        CommandError: An unrecognised provider name was supplied.

    """
    raw: list[list[str]] = options.get("source") or []
    seen: dict[str, None] = {}
    for group in raw:
        for raw_name in group:
            normalised = raw_name.upper()
            if normalised not in SOURCE_CHOICES:
                valid = ", ".join(c.lower() for c in SOURCE_CHOICES)
                raise CommandError(
                    f"Unknown --source value: {raw_name!r}. "
                    f"Valid choices are: {valid} (case-insensitive)."
                )
            seen[normalised] = None
    return list(seen)


def _start_source_label(start_source: str) -> str:
    """
    Render a human-readable tag for the start-of-run banner.

    Args:
        start_source: One of the ``_START_SOURCE_*`` constants.

    Returns:
        A short descriptive string, or an empty string for the explicit case.

    """
    if start_source == _START_SOURCE_LATEST_BULLETIN:
        return "from latest bulletin valid_from day"
    if start_source == _START_SOURCE_SEASON_BACKSTOP:
        return "from SEASON_START_DATE backstop (empty DB)"
    return ""
