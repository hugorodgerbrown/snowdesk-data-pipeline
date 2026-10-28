"""
apps/bulletins/management/commands/rebuild_source_render_models.py — Service models.

The service's counterpart of ``rebuild_render_models`` (SNOW-1270). The
weather service builds a render model for every bulletin it stores
(``SourceBulletin.render_model``) and serves it with the CAAML; a client
copies it when it was built at the client's own ``RENDER_MODEL_VERSION``.
After a builder change bumps the version, the stored models are stale: a
bulletin is rebuilt when it is next fetched, but the history is not, so
this command rebuilds it.

By default it walks the rows whose model predates the current version
(failed builds included); ``--all`` walks every row, for a builder fix that
did not bump the version. A rebuilt row's ``updated_at`` moves, so the
bulletins feed serves it again and a client's next pull copies the new
model. A rebuild that comes out the same writes nothing.

Run it once per source environment after deploying a version bump, and
when a source first takes SNOW-1270 (every row stored before it has no
model). Read-only unless ``--commit`` is passed.

Usage:
    uv run python manage.py rebuild_source_render_models           # preview
    uv run python manage.py rebuild_source_render_models --commit  # rebuild
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import (
    REBUILD_FAILED,
    REBUILT,
    RENDER_MODEL_VERSION,
    SourceBulletin,
    rebuild_render_model,
)

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Rebuild the weather service's stored render models."""

    help = (
        "Rebuild the render model the service stores for each bulletin whose "
        "model predates RENDER_MODEL_VERSION (or every bulletin, with --all). "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Save the rebuilt models. Without it the command counts them.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="Rebuild every bulletin, not only the stale ones.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Walk the stored bulletins, rebuilding each one's render model."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        rows = SourceBulletin.objects.only(
            "pk",
            "bulletin_id",
            "provider",
            "caaml",
            "render_model",
            "render_model_version",
        )
        if not options["rebuild_all"]:
            rows = rows.stale_render_model(RENDER_MODEL_VERSION)
        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Rebuilding {rows.count()} service render model(s) at "
                f"version {RENDER_MODEL_VERSION}{label}"
            )
        )

        rebuilt = unchanged = failed = 0
        for row in iterate_rows(
            self, rows, verbosity=verbosity, describe=SourceBulletin.to_string
        ):
            outcome = rebuild_render_model(row, commit=commit)
            if outcome == REBUILT:
                rebuilt += 1
            elif outcome == REBUILD_FAILED:
                failed += 1
                self.stderr.write(f"  render model failed: {row}")
            else:
                unchanged += 1

        self.tally("rebuilt", rebuilt)
        self.tally("unchanged", unchanged)
        self.tally("failed", failed)
        if verbosity >= 1:
            verb = "Rebuilt" if commit else "Would rebuild"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {rebuilt}; {unchanged} unchanged; {failed} failed."
                )
            )
        logger.info(
            "rebuild_source_render_models: rebuilt=%d unchanged=%d failed=%d commit=%s",
            rebuilt,
            unchanged,
            failed,
            commit,
        )
        if failed:
            raise CommandError(f"{failed} render model build(s) failed")
