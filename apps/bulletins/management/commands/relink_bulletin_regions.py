"""
apps/bulletins/management/commands/relink_bulletin_regions.py — Management command.

Rebuilds each bulletin's ``RegionBulletin`` links from the region list in its
own stored ``raw_data`` (SNOW-1187).

Every ALBINA bulletin of the 2025/26 season was found linked to the wrong
micro regions: the links were correct when written — ``region_name_at_time``
still names the right region — but the ``MicroRegion`` primary keys they hold
had moved underneath them, so Tyrol's danger was painted onto South Tyrol and
regions ALBINA never publishes for read as covered. The raw payload is the
authority on which regions a bulletin covers, so the links are rebuilt from it
the way ``upsert_bulletin`` would build them on ingest, without fetching
anything.

For each bulletin whose linked region set differs from the one its payload
resolves to:

* the (region, day) pairs it covered BEFORE the relink are captured, so the
  wrongly-rated regions are recomputed (to no rating) rather than left
  holding a rating sourced from a bulletin that no longer covers them;
* the links are replaced in one transaction;
* the dissolved grouping outline is recomputed.

Every touched (region, day) pair — before and after — is then refreshed once
at the end. A pair the refresh leaves with no bulletin at all is a region the
bulletin never covered, and ``recompute_region_day`` writes it a sourceless
``no_rating`` row; those rows are deleted, because any ``RegionDayRating`` row
counts as coverage (``covered_region_ids``) and a region ALBINA never
published for must go back to reading as unsupported (SNOW-1185).

Bulletins already linked exactly as their payload says are skipped, so the
command is idempotent and safe to re-run. A bulletin none of
whose payload regions resolve is reported and left alone: replacing its links
with nothing would erase coverage rather than correct it (the SNOW-547 rule
``upsert_bulletin`` enforces).

Streams the queryset newest-id-first via ``apps.core.command_iteration
.iterate_rows`` (SNOW-602), accumulating the touched pairs into a bounded set.

Typical use::

    # Read-only walk — reports how many bulletins would be relinked.
    python manage.py relink_bulletin_regions --source albina

    # Persist.
    python manage.py relink_bulletin_regions --source albina --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import QuerySet

from apps.bulletins.models import Bulletin, RegionBulletin, RegionDayRating
from apps.bulletins.services.day_rating import (
    day_rating_pairs,
    refresh_day_ratings,
    slim_regions_prefetch,
)
from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary
from apps.bulletins.services.slf_fetcher import _resolve_bulletin_regions
from apps.core.command_iteration import iterate_rows
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

_PREFETCH_CHUNK_SIZE = 500

# Per-bulletin outcomes of ``_relink``.
_RELINKED = "relinked"
_CURRENT = "current"
_UNRESOLVED = "unresolved"


def _raw_regions(bulletin: Bulletin) -> list[dict[str, str]]:
    """Return the CAAML ``regions`` list from a stored ``raw_data`` payload.

    Every provider's payload is stored in a GeoJSON Feature envelope by
    ``upsert_bulletin``; an unwrapped payload is accepted too, as
    ``rekey_meteofrance_bulletins`` does for the oldest rows.

    Args:
        bulletin: The bulletin whose payload to read.

    Returns:
        The ``regions`` list, or an empty list when the payload has none.

    """
    raw_data = bulletin.raw_data or {}
    properties = raw_data.get("properties")
    if not isinstance(properties, dict):
        properties = raw_data
    regions = properties.get("regions")
    return regions if isinstance(regions, list) else []


def _clear_placeholders(pairs: set[tuple[MicroRegion, date]]) -> int:
    """Delete the sourceless ``no_rating`` rows among ``pairs``.

    After the refresh, a pair whose row has no ``source_bulletin`` and reads
    ``no_rating`` is one no bulletin covers — the residue of a wrong link, not
    a day the provider published nothing for a region it covers (a covered
    region's off day has no row unless something asked for it). Deleting it
    is what lets ``covered_region_ids`` stop counting the region.

    Args:
        pairs: The (region, day) pairs the run refreshed.

    Returns:
        The number of rows deleted.

    """
    no_rating = RegionDayRating.Rating.NO_RATING
    deleted = 0
    by_day: dict[date, set[int]] = {}
    for region, day in pairs:
        by_day.setdefault(day, set()).add(region.pk)
    for day, region_pks in by_day.items():
        count, _ = RegionDayRating.objects.filter(
            date=day,
            region_id__in=region_pks,
            source_bulletin__isnull=True,
            max_rating=no_rating,
        ).delete()
        deleted += count
    return deleted


class Command(BaseCommand):
    """Rebuild bulletins' region links from their stored raw payloads."""

    help = (
        "Rebuild RegionBulletin links for one provider's bulletins from each "
        "bulletin's stored raw_data, then refresh the affected day ratings and "
        "grouping outlines (SNOW-1187). Read-only unless --commit is passed. "
        "Idempotent: bulletins already linked as their payload says are skipped."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--source",
            required=True,
            type=str.upper,
            choices=Bulletin.Source.values,
            help="Provider whose bulletins to relink, e.g. ``albina``.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the rebuilt links. Without this flag nothing is written.",
        )
        parser.add_argument(
            "--bulletin-id",
            metavar="ID",
            help="Restrict to a single bulletin by its bulletin_id.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_PREFETCH_CHUNK_SIZE,
            metavar="N",
            help=(
                "Chunk size for the streamed queryset iterator "
                f"(default: {_PREFETCH_CHUNK_SIZE})."
            ),
        )

    def _build_queryset(
        self, source: str, bulletin_id: str | None
    ) -> QuerySet[Bulletin]:
        """Build the queryset of bulletins to inspect.

        Args:
            source: A ``Bulletin.Source`` value.
            bulletin_id: Optional bulletin_id to restrict to one row.

        Returns:
            The provider's bulletins, ``regions`` prefetched slim for the
            day-rating pair collection.

        Raises:
            CommandError: ``bulletin_id`` names no bulletin of ``source``.

        """
        qs = Bulletin.objects.filter(source=source)
        if bulletin_id:
            qs = qs.filter(bulletin_id=bulletin_id)
            if not qs.exists():
                raise CommandError(
                    f"No {source} bulletin found with bulletin_id={bulletin_id!r}"
                )
        return qs.prefetch_related(slim_regions_prefetch())

    def _relink(
        self, bulletin: Bulletin, *, commit: bool
    ) -> tuple[str, set[tuple[MicroRegion, date]]]:
        """Bring one bulletin's links into line with its payload.

        Args:
            bulletin: The bulletin to relink, ``regions`` prefetched.
            commit: Whether to write.

        Returns:
            ``(outcome, pairs)``. ``pairs`` holds the (region, day) pairs
            touched before and after the relink, and is empty unless the
            bulletin was relinked under ``commit``.

        """
        raw_regions = _raw_regions(bulletin)
        resolved, skipped = _resolve_bulletin_regions(raw_regions)
        if raw_regions and not resolved:
            logger.warning(
                "Bulletin %s: none of its %d region(s) resolve (%s) — left alone",
                bulletin.bulletin_id,
                len(raw_regions),
                ", ".join(sorted(skipped)),
            )
            return _UNRESOLVED, set()

        wanted = {region.pk for region, _name in resolved}
        linked = {region.pk for region in bulletin.regions.all()}
        if wanted == linked:
            return _CURRENT, set()

        if not commit:
            logger.info(
                "[read-only] Would relink %s: %d link(s) → %d",
                bulletin.bulletin_id,
                len(linked),
                len(wanted),
            )
            return _RELINKED, set()

        # The pairs the WRONG links covered, captured before they go: they are
        # what has to be recomputed down to no rating.
        pairs = day_rating_pairs([bulletin])
        with transaction.atomic():
            RegionBulletin.objects.filter(bulletin=bulletin).delete()
            RegionBulletin.objects.bulk_create(
                RegionBulletin(
                    bulletin=bulletin,
                    region=region,
                    region_name_at_time=name,
                )
                for region, name in resolved
            )
        # The prefetch cache still holds the old links; drop it so the
        # after-pairs and the grouping read the new ones.
        bulletin = Bulletin.objects.prefetch_related(slim_regions_prefetch()).get(
            pk=bulletin.pk
        )
        pairs |= day_rating_pairs([bulletin])
        try:
            compute_bulletin_grouping_boundary(bulletin)
        except Exception:
            logger.exception(
                "compute_bulletin_grouping_boundary failed for bulletin %s",
                bulletin.bulletin_id,
            )
        logger.info("Relinked %s", bulletin.bulletin_id)
        return _RELINKED, pairs

    def _refresh(self, pairs: set[tuple[MicroRegion, date]], verbosity: int) -> int:
        """Refresh every touched pair, then clear the placeholders it leaves.

        Args:
            pairs: The (region, day) pairs touched by the relinked bulletins.
            verbosity: Django's ``--verbosity``; ``0`` suppresses output.

        Returns:
            The number of pairs that failed to recompute.

        """
        if verbosity:
            self.stdout.write(
                f"Refreshing day ratings for {len(pairs)} (region, day) pair(s)."
            )
        failures = refresh_day_ratings(pairs)
        cleared = _clear_placeholders(pairs)
        if verbosity:
            self.stdout.write(
                f"Cleared {cleared} placeholder rating(s) left on regions "
                "no bulletin covers."
            )
        return failures

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the relink.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: A day rating failed to recompute, so cron and CI
                see a non-zero exit.

        """
        # Upper-cased here as well as by the parser: ``call_command`` hands
        # keyword options over without running ``type``.
        source: str = options["source"].upper()
        if source not in Bulletin.Source.values:
            raise CommandError(f"Unknown --source {options['source']!r}")
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        qs = self._build_queryset(source, options.get("bulletin_id"))
        if verbosity:
            mode = "commit" if commit else "read-only"
            self.stdout.write(f"Inspecting {qs.count()} {source} bulletin(s) [{mode}].")

        counts = {_RELINKED: 0, _CURRENT: 0, _UNRESOLVED: 0}
        pairs: set[tuple[MicroRegion, date]] = set()
        for bulletin in iterate_rows(
            self,
            qs,
            verbosity=verbosity,
            chunk_size=options["batch_size"],
            describe=Bulletin.row_label,
        ):
            outcome, touched = self._relink(bulletin, commit=commit)
            counts[outcome] += 1
            pairs |= touched

        failures = self._refresh(pairs, verbosity) if pairs else 0

        if verbosity:
            prefix = "Relinked" if commit else "Would relink"
            self.stdout.write(
                f"{prefix} {counts[_RELINKED]}, already current {counts[_CURRENT]}, "
                f"unresolvable {counts[_UNRESOLVED]}."
            )
            if not commit and counts[_RELINKED]:
                self.stdout.write(
                    self.style.WARNING("Read-only run — pass --commit to persist.")
                )

        if failures:
            raise CommandError(
                f"{failures} day rating(s) failed to recompute — see the log."
            )
