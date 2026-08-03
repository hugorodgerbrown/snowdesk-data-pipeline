"""
apps/bulletins/management/commands/purge_legacy_meteofrance_bulletins.py.

Management command.

Deletes old-grammar Météo-France ``Bulletin`` rows — ``FR-{NN}-{covered
date}`` — but only where a new-grammar replacement,
``FR-{NN}-{covered date}-{publication timestamp}``, has already been loaded
for the same massif-day (SNOW-559, SNOW-562).

``rekey_meteofrance_bulletins`` cannot repair production: it derives the new
id from a row's own ``raw_data``, but every pre-SNOW-559 row was written by an
archive builder that never captured a publication timestamp, so every row
fails with "no publication timestamp". The instant exists only in the source
PDFs, which the rebuilt archive now carries under a **structurally disjoint**
id — ``bulletin_id`` is ``unique=True``, so the rebuilt archive's rows load
alongside the old ones rather than replacing them. This command removes the
old ones once their replacement has landed.

A candidate is deleted only when a matching new-grammar row already exists —
see :func:`docs/decisions/meteofrance-archive-replace-not-merge.md` for the
load-then-purge ordering this implies and why re-keying is structurally
impossible for this population.

``RegionBulletin`` and ``BulletinGrouping`` rows for a deleted bulletin cascade
away (reported, not handled). ``RegionDayRating.source_bulletin`` is
``SET_NULL`` and does **not** self-heal, so every touched (region, day) pair is
collected before deletion and recomputed afterwards.
``BulletinShare.bulletin`` is also ``SET_NULL``; a non-zero count of affected
shares blocks ``--commit`` unless ``--allow-orphaned-shares`` is passed.

Read-only by default — pass ``--commit`` to persist deletions (per the
project-wide management command convention;
see docs/decisions/dry-run-default-commands.md).

Streams the queryset newest-id-first via ``apps.core.command_iteration
.iterate_rows`` rather than hand-rolled OFFSET/LIMIT batching (SNOW-602) —
the old batching re-queried an offset slice of the same queryset on every
page, which is O(n²). The walk collects only what the delete step actually
needs — candidate ``pk``s and ``bulletin_id`` strings, plus the (region, day)
pairs touched by a replaceable row via ``day_rating_pairs`` — rather than the
full ``Bulletin`` instances in a list held until the end of the run.
``_has_replacement`` is a single indexed ``.exists()`` query against the
new-grammar id's exact shape rather than materialising every id sharing the
old id's prefix and pattern-matching them in Python.

Typical use::

    # Read-only walk — reports the per-massif pre-flight table.
    python manage.py purge_legacy_meteofrance_bulletins

    # Persist.
    python manage.py purge_legacy_meteofrance_bulletins --commit

    # Persist even though some candidates have live BulletinShare rows.
    python manage.py purge_legacy_meteofrance_bulletins --commit --allow-orphaned-shares

    # Skip the day-rating refresh (e.g. when a full recompute follows).
    python manage.py purge_legacy_meteofrance_bulletins --commit --skip-day-ratings
"""

from __future__ import annotations

import logging
import re
from argparse import ArgumentParser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    BulletinShare,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.day_rating import day_rating_pairs, refresh_day_ratings
from apps.bulletins.services.meteofrance_identity import BULLETIN_ID_RE
from apps.core.command_iteration import iterate_rows
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

_PREFETCH_CHUNK_SIZE = 500

# Rows to inspect: every Météo-France bulletin, old or new grammar.
_FR_PREFIX = "FR-"

# Strips the trailing publication stamp from a new-grammar id, recovering the
# old-grammar id it replaces: FR-02-2026-02-13-20260212150000 → FR-02-2026-02-13.
_STAMP_SUFFIX_RE = re.compile(r"-\d{14}$")


@dataclass
class _MassifStats:
    """Per-massif (``FR-{NN}``) counts for the pre-flight report."""

    candidates: int = 0
    replaceable: int = 0
    unreplaced: int = 0


@dataclass
class _PurgeReport:
    """Everything gathered by one streamed walk of the FR bulletin table."""

    massifs: dict[str, _MassifStats] = field(default_factory=dict)
    replaceable_pks: list[int] = field(default_factory=list)
    unreplaced_ids: list[str] = field(default_factory=list)
    pairs: set[tuple[MicroRegion, date]] = field(default_factory=set)
    new_grammar_count: int = 0
    recovered_count: int = 0
    region_bulletin_count: int = 0
    grouping_count: int = 0
    day_rating_count: int = 0
    share_count: int = 0


def _massif_code(bulletin_id: str) -> str:
    """Return the ``FR-{NN}`` region component of a bulletin's id.

    Args:
        bulletin_id: The bulletin's current identifier.

    Returns:
        The massif identifier, or the full id when it is not the expected
        shape (defensive — should not occur for rows matching ``FR-``).

    """
    parts = bulletin_id.split("-")
    if len(parts) < 2:
        return bulletin_id
    return f"{parts[0]}-{parts[1]}"


class Command(BaseCommand):
    """Delete old-grammar FR bulletins that already have a new-grammar replacement."""

    help = (
        "Delete old-grammar FR-{NN}-{covered date} Bulletin rows once a "
        "new-grammar FR-{NN}-{covered date}-{publication timestamp} replacement "
        "already exists (SNOW-559, SNOW-562). Read-only unless --commit is "
        "passed. A candidate with no replacement is reported and never deleted."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Delete replaceable old-grammar rows. Without this flag the "
                "command is read-only."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_PREFETCH_CHUNK_SIZE,
            metavar="N",
            help=(
                "Chunk size for the streamed queryset iterator and the "
                f"delete step (default: {_PREFETCH_CHUNK_SIZE})."
            ),
        )
        parser.add_argument(
            "--skip-day-ratings",
            action="store_true",
            help="Skip the RegionDayRating refresh step after purging.",
        )
        parser.add_argument(
            "--allow-orphaned-shares",
            action="store_true",
            help=(
                "Proceed even when deleting would null the bulletin link on one "
                "or more BulletinShare rows. Without this flag --commit refuses "
                "to run when any candidate is referenced by a share."
            ),
        )

    def _has_replacement(self, old_id: str) -> bool:
        """Return whether a new-grammar replacement already exists for ``old_id``.

        A candidate is replaceable iff a row exists whose ``bulletin_id`` is
        exactly the old id plus ``-`` plus a 14-digit publication stamp — the
        new grammar's exact shape (mirrors ``BULLETIN_ID_RE``). A single
        indexed ``.exists()`` query against that regex, rather than
        materialising every row sharing the old id's prefix and pattern
        -matching them in Python.

        Args:
            old_id: The candidate's current (old-grammar) bulletin_id.

        Returns:
            ``True`` when a matching new-grammar row exists.

        """
        return Bulletin.objects.filter(
            bulletin_id__regex=rf"^{re.escape(old_id)}-\d{{14}}$"
        ).exists()

    def _walk(self, *, chunk_size: int, verbosity: int) -> _PurgeReport:
        """Stream every FR bulletin newest-id-first, building the report.

        Args:
            chunk_size: Forwarded to ``iterate_rows`` as its ``chunk_size``.
            verbosity: Django's ``--verbosity`` level.

        Returns:
            A populated ``_PurgeReport``.

        """
        qs = Bulletin.objects.filter(
            bulletin_id__startswith=_FR_PREFIX
        ).prefetch_related("regions")

        massifs: dict[str, _MassifStats] = defaultdict(_MassifStats)
        replaceable_pks: list[int] = []
        unreplaced_ids: list[str] = []
        pairs: set[tuple[MicroRegion, date]] = set()
        new_grammar_count = 0
        # Old-grammar ids implied by every new-grammar row seen — used to
        # derive the number of second issues the load recovered.
        massif_days_with_new_grammar: set[str] = set()

        for bulletin in iterate_rows(
            self,
            qs,
            verbosity=verbosity,
            chunk_size=chunk_size,
            describe=lambda b: b.bulletin_id,
        ):
            if BULLETIN_ID_RE.match(bulletin.bulletin_id):
                new_grammar_count += 1
                massif_days_with_new_grammar.add(
                    _STAMP_SUFFIX_RE.sub("", bulletin.bulletin_id)
                )
                continue

            stats = massifs[_massif_code(bulletin.bulletin_id)]
            stats.candidates += 1
            if self._has_replacement(bulletin.bulletin_id):
                stats.replaceable += 1
                replaceable_pks.append(bulletin.pk)
                pairs |= day_rating_pairs([bulletin])
            else:
                stats.unreplaced += 1
                unreplaced_ids.append(bulletin.bulletin_id)

        return _PurgeReport(
            massifs=dict(massifs),
            replaceable_pks=replaceable_pks,
            unreplaced_ids=unreplaced_ids,
            pairs=pairs,
            new_grammar_count=new_grammar_count,
            recovered_count=new_grammar_count - len(massif_days_with_new_grammar),
            region_bulletin_count=RegionBulletin.objects.filter(
                bulletin_id__in=replaceable_pks
            ).count(),
            grouping_count=BulletinGrouping.objects.filter(
                bulletin_id__in=replaceable_pks
            ).count(),
            day_rating_count=RegionDayRating.objects.filter(
                source_bulletin_id__in=replaceable_pks
            ).count(),
            share_count=BulletinShare.objects.filter(
                bulletin_id__in=replaceable_pks
            ).count(),
        )

    def _print_report(self, report: _PurgeReport) -> None:
        """Write the per-massif pre-flight table and totals to stdout.

        Args:
            report: The report built by ``_walk``.

        """
        self.stdout.write("Per-massif candidates (old-grammar FR bulletins):")
        for massif in sorted(report.massifs):
            stats = report.massifs[massif]
            self.stdout.write(
                f"  {massif}: candidates={stats.candidates} "
                f"replaceable={stats.replaceable} unreplaced={stats.unreplaced}"
            )
        self.stdout.write(
            "Totals: candidates="
            f"{len(report.replaceable_pks) + len(report.unreplaced_ids)} "
            f"replaceable={len(report.replaceable_pks)} "
            f"unreplaced={len(report.unreplaced_ids)}"
        )
        self.stdout.write(
            "Cascading on delete: "
            f"RegionBulletin={report.region_bulletin_count} "
            f"BulletinGrouping={report.grouping_count} "
            f"RegionDayRating(source_bulletin to null)={report.day_rating_count} "
            f"BulletinShare(bulletin to null)={report.share_count}"
        )
        self.stdout.write(
            f"New-grammar FR bulletins already loaded: {report.new_grammar_count} "
            f"({report.recovered_count} second issue(s) recovered by the rebuilt "
            "archive)."
        )
        if report.unreplaced_ids:
            self.stdout.write(self.style.WARNING("Unreplaced (will not be deleted):"))
            for bulletin_id in report.unreplaced_ids:
                self.stdout.write(f"  {bulletin_id}")

    def _delete(self, pks: list[int], batch_size: int) -> int:
        """Delete the bulletins named by ``pks`` in chunks of ``batch_size``.

        Args:
            pks: Primary keys of the rows to delete.
            batch_size: Rows per chunk.

        Returns:
            The number of Bulletin rows deleted.

        """
        deleted = 0
        for i in range(0, len(pks), batch_size):
            chunk = pks[i : i + batch_size]
            n, _ = Bulletin.objects.filter(pk__in=chunk).delete()
            deleted += n
        return deleted

    def _check_unreplaced_gate(self, report: _PurgeReport) -> None:
        """Raise if any candidate has no new-grammar replacement.

        Unconditional in both dry-run and ``--commit`` modes — an incomplete
        archive load should fail a dry-run just as loudly as a real run, so
        the runbook's pre-flight step surfaces it before anything is deleted.

        Args:
            report: The report built by ``_walk``.

        Raises:
            CommandError: Any candidate has no replacement.

        """
        if report.unreplaced_ids:
            raise CommandError(
                f"{len(report.unreplaced_ids)} legacy bulletin(s) have no "
                "new-grammar replacement — see the report above. Load the "
                "rebuilt archive before purging."
            )

    def _check_share_gate(
        self, report: _PurgeReport, *, allow_orphaned_shares: bool
    ) -> None:
        """Raise if a live BulletinShare would be orphaned by the delete.

        Only meaningful once something is actually about to be deleted, so
        the caller must only invoke this under ``--commit`` — a dry-run still
        *reports* ``report.share_count`` but must not fail on it, since
        nothing is being deleted.

        Args:
            report: The report built by ``_walk``.
            allow_orphaned_shares: Whether a non-zero ``BulletinShare`` count
                should be tolerated.

        Raises:
            CommandError: The share gate fires without
                ``--allow-orphaned-shares``.

        """
        if report.share_count and not allow_orphaned_shares:
            raise CommandError(
                f"{report.share_count} BulletinShare row(s) reference bulletin(s) "
                "that would be deleted, which would null their bulletin link. "
                "Pass --allow-orphaned-shares to proceed."
            )

    def _purge(
        self,
        report: _PurgeReport,
        *,
        batch_size: int,
        skip_day_ratings: bool,
        allow_orphaned_shares: bool,
        verbosity: int,
    ) -> None:
        """Delete the replaceable candidates and refresh their day ratings.

        Args:
            report: The report built by ``_walk``.
            batch_size: Rows per delete chunk.
            skip_day_ratings: Whether to skip the RegionDayRating refresh.
            allow_orphaned_shares: Whether a non-zero ``BulletinShare`` count
                should be tolerated.
            verbosity: Django's ``--verbosity``.

        Raises:
            CommandError: The share gate fires without
                ``--allow-orphaned-shares``, or a day-rating recompute failed
                after deleting.

        """
        # Only meaningful now that something is actually about to be deleted
        # — a dry-run must report the share count without failing on it.
        self._check_share_gate(report, allow_orphaned_shares=allow_orphaned_shares)

        # The (region, day) pairs were collected in _walk, before deleting —
        # the RegionBulletin links that back bulletin.regions.all() cascade
        # away with the row.
        deleted = self._delete(report.replaceable_pks, batch_size)
        if verbosity:
            self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} bulletin(s)."))

        if skip_day_ratings:
            return

        if verbosity:
            self.stdout.write(
                f"Refreshing day ratings for {len(report.pairs)} (region, day) pair(s)."
            )
        failures = refresh_day_ratings(report.pairs)
        if verbosity:
            self.stdout.write(self.style.SUCCESS("Day ratings refreshed."))
        if failures:
            raise CommandError(
                f"{failures} day rating recompute(s) failed after purging — see "
                "the log."
            )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the purge command.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: Any candidate has no replacement (both modes); or,
                under ``--commit`` only, the share gate fires without
                ``--allow-orphaned-shares``, or a day-rating recompute fails
                after deleting — so cron/CI see a non-zero exit.

        """
        commit: bool = options["commit"]
        batch_size: int = options["batch_size"]
        verbosity: int = options["verbosity"]
        skip_day_ratings: bool = options["skip_day_ratings"]
        allow_orphaned_shares: bool = options["allow_orphaned_shares"]

        if verbosity:
            mode = "commit" if commit else "read-only"
            self.stdout.write(f"Inspecting Météo-France bulletins [{mode}].")

        report = self._walk(chunk_size=batch_size, verbosity=verbosity)

        if verbosity:
            self._print_report(report)

        # Unconditional in both modes: an incomplete archive load should fail
        # a dry-run just as loudly as a real run.
        self._check_unreplaced_gate(report)

        if not report.replaceable_pks:
            if verbosity:
                self.stdout.write(self.style.SUCCESS("Nothing to purge."))
            return

        if not commit:
            if verbosity:
                self.stdout.write(
                    self.style.WARNING(
                        f"Would delete {len(report.replaceable_pks)} bulletin(s). "
                        "Read-only run — pass --commit to persist."
                    )
                )
            return

        self._purge(
            report,
            batch_size=batch_size,
            skip_day_ratings=skip_day_ratings,
            allow_orphaned_shares=allow_orphaned_shares,
            verbosity=verbosity,
        )
