"""
accounts/management/commands/backfill_verified_accounts.py — backfill command.

Back-fills a verified ``Account`` profile for every ``auth.User`` that has a
confirmed (active) ``Subscriber`` but no verified ``Account`` yet.  These users
proved ownership of their email by clicking an account-access link before the
registration flow (SNOW-430) existed, so they should be treated as verified —
otherwise the new ``is_verified`` gate would lock existing subscribers out of
field reports.

For each active subscriber:
  * no Account row       → create ``Account(is_verified=True)``;
  * unverified Account   → flip ``is_verified`` to True;
  * verified Account     → skip (idempotent).

``verified_at`` is stamped from the subscriber's ``confirmed_at`` (the moment
they proved ownership), falling back to "now" when that is missing.

Safe by default — read-only unless ``--commit`` is passed (CLAUDE.md Option A).
Idempotent — re-running after a commit reports everything as skipped.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Account, Subscriber

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Back-fill verified Account profiles for confirmed subscribers."""

    help = (
        "Back-fill a verified Account for every confirmed subscriber that lacks "
        "one. Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the created/updated Account rows. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the backfill."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        logger.info("backfill_verified_accounts started: commit=%s", commit)

        subscribers = Subscriber.objects.active().select_related("user")
        now = timezone.now()

        counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}

        for subscriber in subscribers.iterator():
            try:
                self._process_one(subscriber, now, commit, counts)
            except Exception as exc:  # noqa: BLE001 — per-row isolation
                logger.exception(
                    "backfill_verified_accounts: error on subscriber pk=%s: %s",
                    subscriber.pk,
                    exc,
                )
                counts["error"] += 1

        self._log_summary(verbosity, commit, counts)
        logger.info("backfill_verified_accounts finished: %s commit=%s", counts, commit)

        if counts["error"] > 0:
            raise CommandError(
                f"backfill_verified_accounts completed with {counts['error']} "
                "error(s); see logs."
            )

    def _process_one(
        self,
        subscriber: Subscriber,
        now: Any,
        commit: bool,
        counts: dict[str, int],
    ) -> None:
        """Ensure the subscriber's user has a verified Account; update counts."""
        user = subscriber.user
        verified_at = subscriber.confirmed_at or now

        account = Account.objects.filter(user=user).first()
        if account is None:
            if commit:
                Account.objects.create(
                    user=user, is_verified=True, verified_at=verified_at
                )
            counts["created"] += 1
        elif not account.is_verified:
            if commit:
                account.is_verified = True
                if account.verified_at is None:
                    account.verified_at = verified_at
                account.save(update_fields=["is_verified", "verified_at", "updated_at"])
            counts["updated"] += 1
        else:
            counts["skipped"] += 1

    def _log_summary(
        self, verbosity: int, commit: bool, counts: dict[str, int]
    ) -> None:
        """Write a human-readable summary to stdout."""
        if verbosity < 1:
            return

        mode = "Committed" if commit else "Dry-run"
        changed = counts["created"] + counts["updated"]
        verb = "verified" if commit else "would verify"
        msg = (
            f"{mode}: {verb} {changed} account(s) "
            f"(created={counts['created']}, updated={counts['updated']}, "
            f"skipped={counts['skipped']}, errors={counts['error']})."
        )
        if not commit and changed:
            msg += " Re-run with --commit to persist."

        if commit:
            self.stdout.write(self.style.SUCCESS(msg))
        else:
            self.stdout.write(msg)
