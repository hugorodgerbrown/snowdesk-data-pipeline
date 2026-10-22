"""
apps/oauth/management/commands/purge_expired_oauth_tokens.py.

Management command.

Clears expired OAuth rows from django-oauth-toolkit's tables (SNOW-1193):
access tokens past their hour, refresh tokens past their thirty days or
revoked, and authorization codes past their minutes. The deletion itself is
the toolkit's own ``clear_expired()`` — the rule that decides which rows are
dead lives with the models that define them. This command adds what the
toolkit's ``cleartokens`` lacks under Snowdesk's command contract: it is
read-only without ``--commit``, and a run records how many rows of each
kind went — or, in a dry run, would go — in the command run register. The
dry run gets its counts by running ``clear_expired()`` inside a transaction
it rolls back, so the rule is the toolkit's in both modes.

A rotated refresh token is revoked rather than deleted when it is spent;
presenting it again is how reuse is detected, so ``clear_expired()`` keeps
it until its own lifetime ends.

Scheduled daily in ``schedule.py``. Typical use::

    python manage.py purge_expired_oauth_tokens
    python manage.py purge_expired_oauth_tokens --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.db import transaction
from oauth2_provider.models import (
    clear_expired,
    get_access_token_model,
    get_grant_model,
    get_refresh_token_model,
)

from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)


def _row_counts() -> dict[str, int]:
    """Return the number of rows in each table ``clear_expired()`` prunes.

    Returns:
        Row counts keyed ``access_tokens``, ``refresh_tokens``, ``codes``.

    """
    return {
        "access_tokens": get_access_token_model().objects.count(),
        "refresh_tokens": get_refresh_token_model().objects.count(),
        "codes": get_grant_model().objects.count(),
    }


class Command(RegisteredCommand):
    """Clear expired OAuth tokens and codes with the toolkit's own rule."""

    help = (
        "Deletes expired OAuth access tokens, refresh tokens and authorization "
        "codes using django-oauth-toolkit's clear_expired(). Read-only without "
        "--commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Delete the expired rows. Without this flag nothing is written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Report, and with --commit clear, the expired rows.

        Exempt from row-by-row streaming (CLAUDE.md management-command rule
        5): the toolkit deletes in batches of its own, with no per-row work
        to show a countdown for.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        """
        commit: bool = options["commit"]
        with transaction.atomic():
            before = _row_counts()
            clear_expired()
            after = _row_counts()
            if not commit:
                transaction.set_rollback(True)
        deleted = {name: before[name] - after[name] for name in before}
        for name, count in deleted.items():
            self.tally(f"{name}_deleted", count)
        summary = ", ".join(f"{count} {name}" for name, count in deleted.items())
        if options["verbosity"] >= 1:
            if commit:
                self.stdout.write(f"Deleted {summary}.")
            else:
                self.stdout.write(
                    f"Read-only run — would delete {summary}. "
                    "Pass --commit to clear them."
                )
        logger.info(
            "purge_expired_oauth_tokens: %s %s",
            "deleted" if commit else "would delete",
            deleted,
        )
