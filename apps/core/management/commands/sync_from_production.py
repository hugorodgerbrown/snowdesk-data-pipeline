"""
apps/core/management/commands/sync_from_production.py — Refresh staging's data.

Staging has no scheduler and no task worker (``render.yaml``), so it never
ingests a bulletin or a weather forecast of its own. This command copies the
provider-derived tables out of the production database and upserts them
locally, so staging shows the same bulletins the live site does.

No user data is copied — see
:mod:`apps.core.services.production_sync` for the table plan and the reason
each excluded table is excluded.

Read-only by default (prints what it would copy); pass ``--commit`` to
write. Runs unattended as the ``snowdesk-staging-data-sync`` Render cron
job; run by hand from the staging shell for a first full load:

.. code-block:: bash

    python manage.py sync_from_production --all --commit

Requires ``PRODUCTION_DATABASE_URL`` in the environment, pointed at a
**read-only** production role. Nothing here writes to production, but the
role is what guarantees that rather than this module's good intentions.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connections, transaction
from django.utils import timezone

from apps.core.services.production_sync import (
    CONNECTION_ATTEMPTS,
    ID_MAP_SPECS,
    SYNC_PLAN,
    IdMap,
    ProductionSyncError,
    TableResult,
    build_id_map,
    check_safe_to_write,
    sync_table,
)

logger = logging.getLogger(__name__)

# Rows are upserted on their natural key, so re-copying one is harmless.
# A week is wide enough to absorb a few days of failed cron runs without an
# operator noticing, and narrow enough that the daily run stays cheap.
DEFAULT_SINCE_DAYS = 7


class Command(BaseCommand):
    """Copy provider-derived tables from production into this database.

    Read-only by default; ``--commit`` persists. Refuses to run unless a
    ``production`` database alias is configured (only
    ``config.settings.staging`` registers one) and this process is not
    itself production.

    SNOW-602 exempt: the unit of work is a batch of rows streamed straight
    from a second database, not a local queryset — ``read_rows`` does the
    keyset pagination ``iterate_rows`` would otherwise provide, and the
    countdown is per table rather than per row.
    """

    help = (
        "Copy bulletins, region ratings and weather from the production "
        "database into this one, upserting on each table's natural key. "
        "Copies no user data. Read-only by default; pass --commit to write."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the copied rows. Without this the command is read-only.",
        )
        parser.add_argument(
            "--since-days",
            type=int,
            default=DEFAULT_SINCE_DAYS,
            help=(
                "Only copy production rows updated within this many days "
                f"(default: {DEFAULT_SINCE_DAYS}). Ignored when --all is given."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Copy every row rather than a recent window. Required for the "
                "first load into an empty staging database."
            ),
        )
        parser.add_argument(
            "--only",
            action="append",
            metavar="APP.MODEL",
            help=(
                "Limit the run to these model labels (repeatable), e.g. "
                "--only bulletins.Bulletin. Parent tables are still needed "
                "for their id maps, so a child-only run can skip rows."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        copy_all: bool = options["all"]
        only: list[str] | None = options["only"]

        try:
            check_safe_to_write(site_environment=settings.SITE_ENVIRONMENT)
        except ProductionSyncError as exc:
            raise CommandError(str(exc)) from exc

        since = None if copy_all else self._window_start(options["since_days"])
        plan = self._select_plan(only)

        flag_label = "" if commit else " [READ-ONLY]"
        window = "all rows" if since is None else f"rows updated since {since:%Y-%m-%d}"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Syncing {len(plan)} table(s) from production — {window}{flag_label}"
            )
        )
        logger.info(
            "sync_from_production started: tables=%d since=%s commit=%s",
            len(plan),
            since,
            commit,
        )

        id_maps = {spec.name: build_id_map(spec) for spec in ID_MAP_SPECS}
        results: list[TableResult] = []

        remaining = len(plan)
        for spec in plan:
            if verbosity >= 1:
                self.stdout.write(f"{remaining} table(s) remaining: {spec.model_label}")
            remaining -= 1

            results.append(
                self._sync_table_with_retry(
                    spec, id_maps=id_maps, since=since, commit=commit
                )
            )

            # Rebuild this table's id map so the children that follow can see
            # the rows it just inserted.
            if spec.provides is not None:
                id_maps[spec.provides] = build_id_map(
                    self._map_spec(id_maps, spec.provides)
                )

        self._report(results, commit=commit, verbosity=verbosity)

    def _sync_table_with_retry(
        self,
        spec: Any,
        *,
        id_maps: dict[str, IdMap],
        since: datetime | None,
        commit: bool,
    ) -> TableResult:
        """Copy one table, restarting it if the write connection dies.

        The read side reconnects per batch (``production_sync.fetch_all``),
        but the write connection can go stale too — it sits idle for as long
        as each production read takes. There is no batch to resume from on
        that side, because the whole table is one transaction, so the retry
        restarts the table.

        That is safe rather than merely tolerable: the failed attempt rolled
        back, and every table upserts on its natural key, so a fresh attempt
        writes the same rows to the same place.

        Args:
            spec: The table's copy rules.
            id_maps: The available foreign-key translations, by name.
            since: Update-window start, or ``None`` for the whole table.
            commit: Whether to write.

        Returns:
            The per-table counts.

        Raises:
            CommandError: if the table could not be copied within
                :data:`CONNECTION_ATTEMPTS` attempts, or the plan is wrong.

        """
        last: OperationalError | None = None
        for attempt in range(1, CONNECTION_ATTEMPTS + 1):
            try:
                with transaction.atomic():
                    return sync_table(spec, id_maps=id_maps, since=since, commit=commit)
            except ProductionSyncError as exc:
                raise CommandError(f"{spec.model_label}: {exc}") from exc
            except OperationalError as exc:
                last = exc
                logger.warning(
                    "sync_from_production: %s lost its write connection on "
                    "attempt %d/%d (%s); restarting the table",
                    spec.model_label,
                    attempt,
                    CONNECTION_ATTEMPTS,
                    exc,
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"  {spec.model_label}: connection lost, retrying "
                        f"({attempt}/{CONNECTION_ATTEMPTS})"
                    )
                )
                connections["default"].close()

        raise CommandError(
            f"{spec.model_label}: lost the write connection "
            f"{CONNECTION_ATTEMPTS} times in a row: {last}"
        )

    def _window_start(self, since_days: int) -> datetime:
        """Return the start of the update window.

        Args:
            since_days: How many days back to reach.

        Returns:
            An aware datetime ``since_days`` before now.

        Raises:
            CommandError: if ``since_days`` is not positive.

        """
        if since_days <= 0:
            raise CommandError("--since-days must be a positive number of days.")
        return timezone.now() - timedelta(days=since_days)

    def _select_plan(self, only: list[str] | None) -> list[Any]:
        """Return the table specs to run, honouring ``--only``.

        Args:
            only: Model labels to restrict to, or ``None`` for the whole
                plan. Matched case-insensitively.

        Returns:
            The selected specs, in plan order.

        Raises:
            CommandError: if a label does not appear in the plan.

        """
        if not only:
            return list(SYNC_PLAN)

        wanted = {label.lower() for label in only}
        known = {spec.model_label.lower() for spec in SYNC_PLAN}
        unknown = wanted - known
        if unknown:
            raise CommandError(
                f"Not in the sync plan: {', '.join(sorted(unknown))}. "
                f"Known tables: {', '.join(spec.model_label for spec in SYNC_PLAN)}."
            )
        return [spec for spec in SYNC_PLAN if spec.model_label.lower() in wanted]

    def _map_spec(self, id_maps: dict[str, IdMap], name: str) -> IdMap:
        """Return the un-resolved spec for an id map, for rebuilding.

        Args:
            id_maps: The current maps, used only as a source of the spec's
                identity fields.
            name: The map's name.

        Returns:
            An :class:`IdMap` carrying the map's identity but no mapping,
            ready for :func:`build_id_map`.

        """
        current = id_maps[name]
        return IdMap(
            name=current.name,
            model_label=current.model_label,
            natural_key=current.natural_key,
        )

    def _report(
        self, results: list[TableResult], *, commit: bool, verbosity: int
    ) -> None:
        """Write the run summary and raise on any skipped row.

        Args:
            results: One entry per table copied.
            commit: Whether rows were actually written.
            verbosity: Django's ``--verbosity`` level.

        Raises:
            CommandError: if any row was skipped for an unresolvable foreign
                key, so an unattended cron run exits non-zero.

        """
        total_read = sum(r.read for r in results)
        total_written = sum(r.written for r in results)
        total_skipped = sum(r.skipped for r in results)

        if verbosity >= 1:
            for result in results:
                self.stdout.write(
                    f"  {result.label}: read={result.read} "
                    f"written={result.written} skipped={result.skipped}"
                )

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    f"[READ-ONLY] dry-run — {total_read} row(s) read, "
                    f"{total_written} would be written. Pass --commit to persist. "
                    "Skip counts are not meaningful in a dry run: parent tables "
                    "were not written, so children cannot resolve new parents."
                )
            )
            return

        logger.info(
            "sync_from_production finished: read=%d written=%d skipped=%d",
            total_read,
            total_written,
            total_skipped,
        )

        if total_skipped:
            raise CommandError(
                f"Wrote {total_written} row(s) but skipped {total_skipped} with an "
                "unresolvable foreign key. The parent rows are not on this "
                "database — re-run with --all to close the gap."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Copied {total_written} row(s) across {len(results)} table(s)."
            )
        )
