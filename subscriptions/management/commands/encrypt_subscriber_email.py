"""
subscriptions/management/commands/encrypt_subscriber_email.py — Backfill command.

Re-saves every Subscriber row so that the ORM's ``get_prep_value`` writes
AES-SIV ciphertext for the email column.  This is the post-deploy backfill step
that follows the ``0005_encrypt_subscriber_email`` migration.

The command is idempotent: because AES-SIV encryption is deterministic (same
plaintext → same ciphertext), re-running the command produces identical ciphertext
for rows that are already encrypted.

Deploy ordering
---------------
1. Run ``migrate`` (metadata-only ``AlterField`` — no data changes; existing
   plaintext rows are still readable via the ``from_db_value`` fallback).
2. Run ``encrypt_subscriber_email --commit`` once to backfill all existing rows.
3. From that point on, all new writes go through the ORM and are encrypted
   automatically.

Usage
-----
    # Dry-run — counts rows that WOULD be re-encrypted, writes nothing.
    python manage.py encrypt_subscriber_email

    # Commit — re-encrypts all rows.
    python manage.py encrypt_subscriber_email --commit
"""

import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from subscriptions.models import Subscriber

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill at-rest email encryption for all Subscriber rows.

    Read-only by default; pass ``--commit`` to write.
    """

    help = (
        "Re-save every Subscriber row so the ORM writes AES-SIV ciphertext for "
        "the email column.  Post-deploy backfill step.  Read-only by default; "
        "pass --commit to persist."
    )

    def add_arguments(self, parser: Any) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Re-save every Subscriber row to the database, encrypting any "
                "plaintext email values.  Without this flag the command is "
                "read-only (no database writes)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        total = Subscriber.objects.count()

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry-run: {total} subscriber row(s) would be re-encrypted. "
                        "Pass --commit to persist."
                    )
                )
            logger.info(
                "encrypt_subscriber_email dry-run: total=%d commit=False",
                total,
            )
            return

        succeeded = 0
        failed = 0

        for subscriber in Subscriber.objects.iterator():
            try:
                subscriber.save(update_fields=["email"])
                succeeded += 1
                if verbosity >= 2:
                    self.stdout.write(f"  Encrypted {subscriber.pk}")
            except Exception:
                failed += 1
                logger.exception(
                    "encrypt_subscriber_email: failed to re-save subscriber pk=%s",
                    subscriber.pk,
                )

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {succeeded} encrypted, {failed} failed (of {total} total)."
                )
            )

        logger.info(
            "encrypt_subscriber_email finished: "
            "total=%d succeeded=%d failed=%d commit=True",
            total,
            succeeded,
            failed,
        )

        if failed > 0:
            raise CommandError(
                f"encrypt_subscriber_email completed with {failed} failure(s). "
                "Check logs for details."
            )
