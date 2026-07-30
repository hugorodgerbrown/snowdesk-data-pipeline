"""
apps/bulletins/management/commands/rekey_meteofrance_bulletins.py — Management command.

One-time migration of existing Météo-France ``Bulletin`` rows from the old
``FR-{NN}-{covered date}`` identifier to
``FR-{NN}-{covered date}-{publication timestamp}`` (SNOW-559).

The old id could not distinguish the two BRAs Météo-France publishes for one
massif and covered day, so the archive loader silently overwrote one issue with
the other and the live fetcher silently skipped the second.  Adding the
publication instant to the id fixes both, but changes the identity of every row
already ingested — hence this command.

There is no schema change: ``bulletin_id`` is a ``CharField``, so this is purely
a data migration and belongs in a command rather than a ``RunPython`` migration
(see docs/decisions/dry-run-default-commands.md and the project rule against
data updates inside migrations).

The new id is derived from each row's own ``raw_data``, so the command needs
neither the NDJSON archive nor the source PDFs:

* archive-loaded rows carry ``properties.publicationTime`` once the archive has
  been re-extracted;
* live-fetched rows carry it too, and additionally
  ``customData.MF.rawLocalTimes.publishedAt`` as a fallback.

Rows already on the new grammar are skipped, so the command is idempotent and
safe to re-run.  Rows with no usable timestamp are reported and left alone —
they cannot be given a correct identity here, and guessing one would recreate
the ambiguity this change removes.

Re-keying alone does not restore the issues that were previously overwritten;
re-run the archive load afterwards to create them.

Typical use::

    # Read-only walk — reports what would change.
    python manage.py rekey_meteofrance_bulletins

    # Persist.
    python manage.py rekey_meteofrance_bulletins --commit

    # Skip the day-rating refresh (e.g. when a full recompute follows).
    python manage.py rekey_meteofrance_bulletins --commit --skip-day-ratings
"""

from __future__ import annotations

import datetime
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import (
    recompute_region_day,
    target_day_for_valid_from,
)
from apps.bulletins.services.meteofrance_identity import (
    BULLETIN_ID_RE,
    build_bulletin_id,
)

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500

# Rows to re-key: every Météo-France bulletin.
_FR_PREFIX = "FR-"


def _properties(raw_data: dict[str, Any] | None) -> dict[str, Any]:
    """Return the CAAML ``properties`` dict from a stored ``raw_data`` payload.

    Every provider's raw payload is wrapped in a GeoJSON Feature envelope, but
    some older rows were stored unwrapped, so both shapes are accepted.

    Args:
        raw_data: The ``Bulletin.raw_data`` value.

    Returns:
        The ``properties`` sub-dict, or an empty dict when absent.

    """
    if not raw_data:
        return {}
    properties = raw_data.get("properties")
    if isinstance(properties, dict):
        return properties
    return raw_data


def _publication_time(properties: dict[str, Any]) -> object:
    """Return the best available publication timestamp for a stored payload.

    Args:
        properties: The bulletin's CAAML ``properties`` dict.

    Returns:
        The top-level ``publicationTime`` when present, otherwise the live
        path's ``customData.MF.rawLocalTimes.publishedAt``, otherwise ``None``.
        The raw-local fallback is naive Europe/Paris rather than UTC, so it is
        only used when the canonical field is missing.

    """
    published = properties.get("publicationTime")
    if published:
        return published
    custom = properties.get("customData")
    if not isinstance(custom, dict):
        return None
    mf = custom.get("MF")
    if not isinstance(mf, dict):
        return None
    raw_times = mf.get("rawLocalTimes")
    if not isinstance(raw_times, dict):
        return None
    return raw_times.get("publishedAt")


def _covered_date(properties: dict[str, Any], bulletin: Bulletin) -> str | None:
    """Return the ISO covered date for a bulletin.

    Args:
        properties: The bulletin's CAAML ``properties`` dict.
        bulletin: The row being re-keyed, used to recover the covered date from
            the existing id when the payload does not carry one.

    Returns:
        The covered date in ``YYYY-MM-DD`` form, or ``None``.

    """
    custom = properties.get("customData")
    if isinstance(custom, dict):
        mf = custom.get("MF")
        if isinstance(mf, dict) and mf.get("date"):
            return str(mf["date"])
    # Fall back to the old id's own trailing date: FR-{NN}-{YYYY-MM-DD}.
    parts = bulletin.bulletin_id.split("-")
    if len(parts) >= 5:
        return "-".join(parts[2:5])
    return None


def _region_code(bulletin: Bulletin) -> str | None:
    """Return the ``FR-{NN}`` region component of a bulletin's id.

    Args:
        bulletin: The row being re-keyed.

    Returns:
        The region identifier, or ``None`` when the id is not the expected
        shape.

    """
    parts = bulletin.bulletin_id.split("-")
    if len(parts) < 2:
        return None
    return f"{parts[0]}-{parts[1]}"


class Command(BaseCommand):
    """Re-key Météo-France bulletins onto the publication-stamped identifier."""

    help = (
        "Migrate Météo-France Bulletin rows from FR-{NN}-{date} to "
        "FR-{NN}-{date}-{publication timestamp} (SNOW-559). Read-only unless "
        "--commit is passed. Idempotent: rows already on the new grammar are "
        "skipped."
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
                "Persist the new bulletin_id values. Without this flag the "
                "command is read-only."
            ),
        )
        parser.add_argument(
            "--bulletin-id",
            metavar="ID",
            help="Restrict to a single bulletin by its current bulletin_id.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_DEFAULT_BATCH_SIZE,
            metavar="N",
            help=(
                f"Process bulletins in batches of N (default: {_DEFAULT_BATCH_SIZE})."
            ),
        )
        parser.add_argument(
            "--skip-day-ratings",
            action="store_true",
            help="Skip the RegionDayRating refresh step after re-keying.",
        )

    def _build_queryset(self, bulletin_id_arg: str | None) -> Any:
        """Build the queryset of Météo-France bulletins to inspect.

        Args:
            bulletin_id_arg: Optional bulletin_id to restrict to one row.

        Returns:
            A Bulletin queryset ordered by pk.

        Raises:
            CommandError: ``bulletin_id_arg`` is not an FR id, or is not found.

        """
        if bulletin_id_arg:
            if not bulletin_id_arg.startswith(_FR_PREFIX):
                raise CommandError(
                    f"--bulletin-id must start with {_FR_PREFIX!r}; got "
                    f"{bulletin_id_arg!r}. This command is scoped to French "
                    "bulletins only."
                )
            qs = Bulletin.objects.filter(bulletin_id=bulletin_id_arg)
            if not qs.exists():
                raise CommandError(
                    f"No bulletin found with bulletin_id={bulletin_id_arg!r}"
                )
            return qs.order_by("pk")
        return Bulletin.objects.filter(bulletin_id__startswith=_FR_PREFIX).order_by(
            "pk"
        )

    def _new_id_for(self, bulletin: Bulletin) -> tuple[str | None, str]:
        """Derive the new id for one row.

        Args:
            bulletin: The row being re-keyed.

        Returns:
            A ``(new_id, reason)`` tuple.  ``new_id`` is ``None`` when the row
            cannot be re-keyed, in which case ``reason`` explains why;
            ``reason`` is ``"skipped"`` when the row is already correct.

        """
        if BULLETIN_ID_RE.match(bulletin.bulletin_id):
            return None, "skipped"

        properties = _properties(bulletin.raw_data)
        region_id = _region_code(bulletin)
        covered = _covered_date(properties, bulletin)
        if region_id is None or covered is None:
            return None, "unparseable id"

        new_id = build_bulletin_id(region_id, covered, _publication_time(properties))
        if new_id is None:
            return None, "no publication timestamp"
        return new_id, ""

    def _rekey(self, bulletin: Bulletin, new_id: str) -> bool:
        """Persist a new id for one row.

        Args:
            bulletin: The row being re-keyed.
            new_id: The identifier to write.

        Returns:
            ``True`` on success, ``False`` when the new id is already taken by
            a different row — which means the archive already holds the
            correctly-keyed issue, so this stale row is left for inspection
            rather than silently dropped.

        """
        try:
            with transaction.atomic():
                Bulletin.objects.filter(pk=bulletin.pk).update(bulletin_id=new_id)
        except IntegrityError:
            logger.warning(
                "Cannot re-key %s to %s: that id already exists",
                bulletin.bulletin_id,
                new_id,
            )
            return False
        logger.info("Re-keyed %s → %s", bulletin.bulletin_id, new_id)
        return True

    def _refresh_day_ratings(
        self, bulletins: list[Bulletin], verbosity: int = 1
    ) -> int:
        """Recompute RegionDayRating for every (region, day) touched.

        Args:
            bulletins: Bulletins whose day ratings should be refreshed.
            verbosity: Django's ``--verbosity``; ``0`` suppresses progress
                output.

        Returns:
            The number of (region, day) pairs that failed to recompute.

        """
        pairs: set[tuple[Any, datetime.date]] = set()
        for bulletin in bulletins:
            # Prefer the stored column (SNOW-560); fall back to deriving it for
            # any row ingested before that migration backfilled it.
            day = bulletin.target_date or target_day_for_valid_from(bulletin.valid_from)
            for region in bulletin.regions.all():
                pairs.add((region, day))

        if verbosity:
            self.stdout.write(
                f"Refreshing day ratings for {len(pairs)} (region, day) pair(s)."
            )
        failures = 0
        for region, day in pairs:
            try:
                recompute_region_day(region, day, commit=True)
            except Exception:
                failures += 1
                logger.exception(
                    "Failed to refresh day rating for region=%s day=%s",
                    region.region_id,
                    day,
                )
        if verbosity:
            self.stdout.write(self.style.SUCCESS("Day ratings refreshed."))
        return failures

    def _process_in_batches(
        self, qs: Any, total: int, batch_size: int, commit: bool
    ) -> tuple[int, int, int, list[Bulletin]]:
        """Walk the queryset in pk-ordered batches, re-keying each row.

        Args:
            qs: The queryset of candidate bulletins.
            total: The queryset count, for batch bookkeeping.
            batch_size: Rows per batch.
            commit: Whether to persist changes.

        Returns:
            A ``(changed, skipped, failed, changed_bulletins)`` tuple.

        """
        changed = 0
        skipped = 0
        failed = 0
        changed_bulletins: list[Bulletin] = []
        offset = 0

        while offset < total:
            batch_ids = list(
                qs.values_list("pk", flat=True)[offset : offset + batch_size]
            )
            if not batch_ids:
                break

            for bulletin in Bulletin.objects.filter(pk__in=batch_ids).order_by("pk"):
                new_id, reason = self._new_id_for(bulletin)
                if new_id is None:
                    if reason == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                        logger.warning(
                            "Cannot re-key %s: %s", bulletin.bulletin_id, reason
                        )
                    continue

                if not commit:
                    changed += 1
                    logger.info(
                        "[read-only] Would re-key %s → %s",
                        bulletin.bulletin_id,
                        new_id,
                    )
                    continue

                if self._rekey(bulletin, new_id):
                    changed += 1
                    bulletin.bulletin_id = new_id
                    changed_bulletins.append(bulletin)
                else:
                    failed += 1

            offset += batch_size

        return changed, skipped, failed, changed_bulletins

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the re-key command.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: Any row could not be re-keyed, so cron and CI see a
                non-zero exit.

        """
        commit: bool = options["commit"]
        batch_size: int = options["batch_size"]
        verbosity: int = options["verbosity"]

        qs = self._build_queryset(options.get("bulletin_id"))
        total = qs.count()

        if verbosity:
            mode = "commit" if commit else "read-only"
            self.stdout.write(f"Inspecting {total} Météo-France bulletin(s) [{mode}].")

        changed, skipped, failed, changed_bulletins = self._process_in_batches(
            qs, total, batch_size, commit
        )

        if commit and changed_bulletins and not options["skip_day_ratings"]:
            failed += self._refresh_day_ratings(changed_bulletins, verbosity)

        if verbosity:
            prefix = "Re-keyed" if commit else "Would re-key"
            self.stdout.write(
                f"{prefix} {changed}, already current {skipped}, failed {failed}."
            )
            if not commit and changed:
                self.stdout.write(
                    self.style.WARNING("Read-only run — pass --commit to persist.")
                )

        if failed:
            raise CommandError(
                f"{failed} bulletin(s) could not be re-keyed — see the log."
            )
