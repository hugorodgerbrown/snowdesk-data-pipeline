"""
apps/oauth/management/commands/purge_expired_oauth_tokens.py.

Management command.

Deletes OAuth tokens that expired, or were revoked, more than seven days
ago, and authorization codes that expired more than seven days ago
(SNOW-1035). An expired or revoked row is never accepted again, so keeping
it serves nothing; the week of grace keeps recent rows readable in the
admin while an operator looks into a report.

A rotated refresh token is neither expired nor revoked until its thirty
days run out, so it survives to do its job — presenting it again is how
reuse is detected, and reuse revokes the grant.

Grants are not touched: a revoked grant is the record that a user
disconnected an app, and it is deleted with the user or the client.

Read-only by default — pass ``--commit`` to delete
(docs/decisions/dry-run-default-commands.md). Scheduled daily in
``schedule.py``.

Typical use::

    python manage.py purge_expired_oauth_tokens
    python manage.py purge_expired_oauth_tokens --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.db.models import Model, QuerySet
from django.utils import timezone

from apps.core.command_iteration import iterate_rows, positive_int
from apps.oauth.models import AuthorizationCode, OAuthToken

logger = logging.getLogger(__name__)

GRACE_DAYS: int = 7


class Command(BaseCommand):
    """Delete long-dead OAuth tokens and authorization codes."""

    help = (
        "Deletes OAuth tokens expired or revoked, and codes expired, more "
        f"than {GRACE_DAYS} days ago. Read-only without --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Delete the rows. Without this flag nothing is written.",
        )
        parser.add_argument(
            "--days",
            type=positive_int,
            default=GRACE_DAYS,
            metavar="N",
            help=f"Grace period in days (default {GRACE_DAYS}).",
        )

    def _delete_all(
        self, queryset: QuerySet[Any], label: str, verbosity: int
    ) -> tuple[int, int]:
        """Stream ``queryset`` newest-first and delete each row.

        Args:
            queryset: The rows to delete.
            label: A noun for the countdown lines.
            verbosity: Django's ``--verbosity``.

        Returns:
            ``(deleted, failed)`` counts.

        """
        deleted = failed = 0
        row: Model
        for row in iterate_rows(
            self,
            queryset,
            verbosity=verbosity,
            describe=lambda r: f"{label} {r.pk}",
        ):
            try:
                row.delete()
                deleted += 1
            except DatabaseError:
                logger.exception(
                    "purge_expired_oauth_tokens: %s %s failed", label, row.pk
                )
                failed += 1
        return deleted, failed

    def handle(self, *args: Any, **options: Any) -> None:
        """Delete the expired rows, or report them when not committing.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: When any row fails to delete.

        """
        verbosity: int = options["verbosity"]
        cutoff = timezone.now() - timedelta(days=options["days"])
        tokens = OAuthToken.objects.spent_before(cutoff)
        codes = AuthorizationCode.objects.expired_before(cutoff)

        if not options["commit"]:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"Would delete {tokens.count()} token(s) and "
                        f"{codes.count()} code(s) dead since before "
                        f"{cutoff:%Y-%m-%d %H:%M} UTC. Re-run with --commit."
                    )
                )
            return

        tokens_deleted, tokens_failed = self._delete_all(tokens, "token", verbosity)
        codes_deleted, codes_failed = self._delete_all(codes, "code", verbosity)

        logger.info(
            "purge_expired_oauth_tokens: deleted %d token(s), %d code(s)",
            tokens_deleted,
            codes_deleted,
        )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {tokens_deleted} token(s) and {codes_deleted} code(s)."
                )
            )
        if tokens_failed or codes_failed:
            raise CommandError(
                f"{tokens_failed} token(s) and {codes_failed} code(s) failed to delete."
            )
