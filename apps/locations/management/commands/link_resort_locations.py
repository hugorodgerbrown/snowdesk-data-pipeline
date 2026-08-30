"""link_resort_locations — give every geocoded resort a Location for weather.

A resort's pin and a resort's weather were two unconnected things. The
edit-resorts map overlay writes ``Resort.latitude``/``longitude`` and never
touches ``Location``; the resort page's weather section reads
``ResortLocation`` links, which only the separate edit-locations overlay
creates. So a resort could be on the map for months with a hand-placed pin
and still show no weather — production had 115 geocoded resorts and 4 with a
link.

This closes that: a geocoded resort with no link gets an anonymous
``Location`` at its own coordinate, marked ``is_primary``. Curating named
village / mid / peak points stays worthwhile and stays the editor's job —
this is the floor, not a replacement for it.

**Offline.** The coordinate is the resort's own, and the height is
``base_elevation_m`` where the sheet records one. No Open-Meteo call, which
is what makes it affordable to run on every deploy alongside
``link_region_centroid_locations``.

**Reuses rather than mints** via ``anchor_location``, for the reason SNOW-771
records: a re-link that created a fresh row each deploy would orphan the
previous one and every ``Weather`` row hanging off it.

``role`` is left blank on purpose. BASE/MID/TOP are claims about what a point
IS, and a hand-placed pin is only "where this resort is" — ``is_primary``
already carries that. A curator naming the point later can say which it is.

Usage:
    # Preview — reports what it would link, writes nothing.
    uv run python manage.py link_resort_locations

    # Persist. This is what build.sh runs on every deploy.
    uv run python manage.py link_resort_locations --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.command_iteration import announce_link_run, iterate_rows
from apps.locations.models import ResortLocation
from apps.locations.services.anchor import anchor_location
from apps.regions.models import Resort

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Give every geocoded resort without a linked Location one at its pin.

    Read-only by default; pass --commit to persist. Offline and idempotent,
    so it is safe on every deploy of every service.
    """

    help = (
        "Mint a Location at each geocoded resort's own coordinate and link "
        "it, so a resort on the map has weather without hand curation. "
        "Offline. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted Locations and their ResortLocation "
                "links. Without this flag the command reports but writes "
                "nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Link every geocoded resort that has no location yet."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        candidates = Resort.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            resort_locations__isnull=True,
        )
        total = candidates.count()

        announce_link_run(
            self,
            logger=logger,
            command_name="link_resort_locations",
            banner=f"Linking a Location for {total} geocoded resort(s)",
            candidate_count=total,
            commit=commit,
        )

        counts = {"linked": 0, "skipped": 0, "failed": 0}
        for resort in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.name}",
        ):
            self._resolve_one(resort, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        # Non-zero only on a total failure — every candidate raised. A
        # partial failure logs loudly and lets the deploy finish; failing
        # every deploy of every service over one unwritable resort is the
        # worse outcome (see link_region_centroid_locations).
        if counts["failed"] > 0 and counts["linked"] == 0:
            raise CommandError(
                f"link_resort_locations linked nothing: all "
                f"{counts['failed']} candidate(s) failed. Check logs."
            )

    def _resolve_one(
        self, resort: Resort, counts: dict[str, int], *, commit: bool
    ) -> None:
        """Anchor one resort to a Location at its own pin.

        Args:
            resort: The resort to anchor.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        if resort.latitude is None or resort.longitude is None:
            counts["skipped"] += 1
            return

        if commit:
            try:
                with transaction.atomic():
                    # Lock and re-read: production deploys three services at
                    # once, all running this against one database.
                    locked = (
                        Resort.objects.select_for_update().filter(pk=resort.pk).first()
                    )
                    if locked is None:
                        counts["skipped"] += 1
                        return
                    if locked.resort_locations.exists():
                        # A concurrent build linked it while we queued.
                        counts["linked"] += 1
                        return
                    if locked.latitude is None or locked.longitude is None:
                        # Re-checked under the lock: the queryset excludes a
                        # null coordinate, but an editor save could have
                        # cleared one between the walk and here.
                        counts["skipped"] += 1
                        return
                    location = anchor_location(
                        locked.latitude,
                        locked.longitude,
                        locked.base_elevation_m,
                    )
                    ResortLocation.objects.create(
                        resort=locked,
                        location=location,
                        role="",
                        is_primary=True,
                    )
            except Exception:  # noqa: BLE001 — one resort must not fail a deploy
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
                    f"{counts['skipped']} skipped, "
                    f"{counts['failed']} failed.{suffix}"
                )
            )
        logger.info(
            "link_resort_locations finished: linked=%d skipped=%d failed=%d commit=%s",
            counts["linked"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
