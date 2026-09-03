"""backfill_subscriptions_to_region_pins — turn Subscription rows into region pins.

One-shot backfill for SNOW-802. ``Subscription`` was justified as a
notification channel and never was one — nothing sends a bulletin — so each
of its rows is a bookmark on a region, which is what a region pin is
(``docs/decisions/two-documents-and-a-map.md``). This command creates the
region pin for every ``(account, region)`` row and leaves the ``Subscription``
row in place: the table is dropped in a later, separate deploy (SNOW-805),
after this has run in production. ``build.sh`` auto-migrates, so a drop
cannot travel with the backfill that empties it.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations.

**The favourites cap does not apply.** A user's existing regions must not
be dropped on the floor because they also hold many placed pins, so the
service is called with ``enforce_cap=False``.

Idempotent: ``create_region_favourite`` returns an existing pin rather than
minting a second, so a re-run selects every row again and writes nothing
new. Exits non-zero if any row failed.

Usage:
    # Preview — reports what would be created, writes nothing.
    uv run python manage.py backfill_subscriptions_to_region_pins

    # Apply it.
    uv run python manage.py backfill_subscriptions_to_region_pins --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Subscription
from apps.core.command_iteration import iterate_rows
from apps.favourites.models import Favourite
from apps.favourites.services import create_region_favourite

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Create a region pin for every Subscription row.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    row failed, so a partial run is detectable.
    """

    help = (
        "Create a region pin (Favourite) for every Subscription row "
        "(SNOW-802). Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the region pins. Without this flag the command "
                "reports and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Create a region pin per subscription."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        candidates = Subscription.objects.select_related(
            "account__user", "region"
        ).order_by("-id")
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Converting {total} subscription(s) to region pins{flag_label}"
            )
        )
        logger.info(
            "backfill_subscriptions_to_region_pins started: candidates=%d commit=%s",
            total,
            commit,
        )

        counts = {"created": 0, "existing": 0, "failed": 0}
        for subscription in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: (
                f"{row.pk} account={row.account_id} {row.region.region_id}"
            ),
        ):
            self._backfill_one(subscription, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_subscriptions_to_region_pins completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _backfill_one(
        self, subscription: Subscription, counts: dict[str, int], *, commit: bool
    ) -> None:
        """Create the region pin for one subscription.

        Args:
            subscription: The row to convert.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        user = subscription.account.user
        region = subscription.region
        exists = (
            Favourite.objects.for_user(user)
            .region_pins()
            .filter(region=region)
            .exists()
        )
        if exists:
            counts["existing"] += 1
            return
        counts["created"] += 1
        if not commit:
            return
        try:
            create_region_favourite(user, region, enforce_cap=False)
        except Exception:  # noqa: BLE001 — broad catch intentional: one row must not abort the batch
            logger.exception(
                "backfill_subscriptions_to_region_pins: failed on subscription id=%s",
                subscription.pk,
            )
            counts["created"] -= 1
            counts["failed"] += 1

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['created']} region pin(s) created, "
                        f"{counts['existing']} already present, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['created']} region "
                        f"pin(s) would be created, {counts['existing']} already "
                        "present. No data written. Pass --commit to persist."
                    )
                )
        logger.info(
            "backfill_subscriptions_to_region_pins finished: created=%d "
            "existing=%d failed=%d commit=%s",
            counts["created"],
            counts["existing"],
            counts["failed"],
            commit,
        )
