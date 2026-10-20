"""link_resort_locations — backfill ``Resort.location`` for every pinned resort.

**A one-time backfill (SNOW-1144).** A resort's map pin is now a
``Location`` — ``Resort.location`` — written by
``apps.regions.services.resort_pin.set_resort_pin`` from every path that
places, moves or clears one: the edit-resorts map overlay, ``import_resorts``
and the admin. Once this has run against an environment, that service keeps
pins and Locations in step on its own, and there is nothing left for an
operator to link by hand.

What it backfills is the estate from before the column existed: resorts
whose pin is still only the two mirrored floats ``Resort.latitude`` /
``longitude``. Each one goes through ``set_resort_pin`` — the same writer as
every other path, so the reuse rules are stated once:

* the ``Location`` behind the resort's primary ``ResortLocation`` — the
  anonymous row this command minted before SNOW-1144 — is adopted rather
  than duplicated, so the weather already stored against it stays the
  resort's;
* failing that, ``anchor_location`` reuses an anonymous row already at the
  coordinate, and only then mints one. The height is ``base_elevation_m``
  where the sheet records one. No Open-Meteo call.

**History.** Before SNOW-1144 this command minted an anonymous ``Location``
at the pin and joined it through ``ResortLocation(is_primary=True)``, because
the pin and the weather were two unconnected things and production had 115
geocoded resorts and 4 links. It still never runs on a deploy: it writes one
row per candidate, and a deploy that times out mid-run on three concurrently
deploying services leaves the estate half linked.

**``--check``** is the SNOW-885 detector, scheduled daily: it names every
resort with coordinates and no pin ``Location`` and exits non-zero when
there is one. After the backfill that set is empty by construction.

Usage:
    # Preview — reports what it would link, writes nothing.
    uv run python manage.py link_resort_locations

    # Persist.
    uv run python manage.py link_resort_locations --commit

    # Detect only — non-zero exit when any resort is unlinked.
    uv run python manage.py link_resort_locations --check
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import announce_link_run, iterate_rows
from apps.regions.models import Resort
from apps.regions.services.resort_pin import locked_resort, set_resort_pin

logger = logging.getLogger(__name__)


def _pin_is_current(resort: Resort) -> bool:
    """Return whether the resort's pin Location already sits at its coordinate.

    Reads only the ``select_related`` location, so it costs no query per row.

    Args:
        resort: A resort carrying a coordinate.

    Returns:
        True when ``set_resort_pin`` would write nothing for this resort.

    """
    location = resort.location
    return location is not None and (location.latitude, location.longitude) == (
        resort.latitude,
        resort.longitude,
    )


class Command(BaseCommand):
    """Set ``Resort.location`` for every resort with a coordinate.

    Read-only by default; pass --commit to persist. Offline and idempotent:
    a resort whose pin Location already matches its coordinate is counted
    as unchanged and not written. Per-resort failures are caught, logged and
    counted; the command exits non-zero when any failed.
    """

    help = (
        "Backfill Resort.location — the resort's pin as a Location — for "
        "every resort with a coordinate, through set_resort_pin. Offline. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist each resort's pin Location. Without this flag the "
                "command reports but writes nothing."
            ),
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Report resorts with a coordinate but no pin Location and "
                "exit NON-ZERO if there are any. Writes nothing. For the "
                "scheduled detector."
            ),
        )

    def _check(self) -> None:
        """Report unlinked resorts and fail if there are any.

        SNOW-885. The gap this names is silent by construction — a resort
        with no pin ``Location`` has no weather and nothing else says so —
        so a scheduled run that exits 0 whatever it finds would be a
        detector that never detects. Finding any is an ERROR and a non-zero
        exit: that is the whole signal. Every resort is named, not just
        counted, so the operator knows which pages are wrong.

        Raises:
            CommandError: When any unlinked resort exists.

        """
        names = list(
            Resort.objects.unlinked().order_by("name").values_list("name", flat=True)
        )
        if not names:
            self.stdout.write("No unlinked geocoded resorts.")
            return

        for name in names:
            self.stdout.write(f"unlinked: {name}")
        logger.error(
            "link_resort_locations: %d geocoded resort(s) have no pin location "
            "and render no weather: %s",
            len(names),
            ", ".join(names),
        )
        raise CommandError(
            f"{len(names)} geocoded resort(s) have no pin location. "
            f"Run: link_resort_locations --commit"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Backfill the pin Location of every resort with a coordinate."""
        if options["check"]:
            self._check()
            return

        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        candidates = Resort.objects.filter(
            latitude__isnull=False, longitude__isnull=False
        ).select_related("location")
        total = candidates.count()

        announce_link_run(
            self,
            logger=logger,
            command_name="link_resort_locations",
            banner=f"Backfilling the pin Location of {total} resort(s)",
            candidate_count=total,
            commit=commit,
        )

        counts = {"linked": 0, "unchanged": 0, "failed": 0}
        for resort in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: row.name,
        ):
            self._resolve_one(resort, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        # Non-zero on ANY failure, partial batches included — the command
        # contract in CLAUDE.md. One unwritable resort never aborts the
        # walk, but the operator must not read a half-linked estate as a
        # clean run.
        if counts["failed"] > 0:
            raise CommandError(
                f"link_resort_locations: {counts['failed']} of {total} "
                f"resort(s) failed to link. Check logs."
            )

    def _resolve_one(
        self, resort: Resort, counts: dict[str, int], *, commit: bool
    ) -> None:
        """Bring one resort's pin Location into step with its coordinate.

        Args:
            resort: The resort, with ``location`` already loaded.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        if _pin_is_current(resort):
            counts["unchanged"] += 1
            return

        if commit:
            try:
                with transaction.atomic():
                    # Lock and re-read: an editor save could move or clear
                    # the pin between the walk and here.
                    locked = locked_resort(resort.pk).select_related("location").first()
                    if (
                        locked is None
                        or locked.latitude is None
                        or locked.longitude is None
                        or _pin_is_current(locked)
                    ):
                        counts["unchanged"] += 1
                        return
                    set_resort_pin(locked, locked.latitude, locked.longitude)
            except Exception:  # noqa: BLE001 — one resort must not fail the batch
                logger.exception(
                    "link_resort_locations: failed to link resort %s", resort.pk
                )
                counts["failed"] += 1
                return
        counts["linked"] += 1

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary."""
        if verbosity >= 1:
            suffix = "" if commit else " No data written. Pass --commit to persist."
            verb = "linked" if commit else "would be linked"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts['linked']} resort(s) {verb}, "
                    f"{counts['unchanged']} unchanged, "
                    f"{counts['failed']} failed.{suffix}"
                )
            )
        logger.info(
            "link_resort_locations finished: linked=%d unchanged=%d failed=%d "
            "commit=%s",
            counts["linked"],
            counts["unchanged"],
            counts["failed"],
            commit,
        )
