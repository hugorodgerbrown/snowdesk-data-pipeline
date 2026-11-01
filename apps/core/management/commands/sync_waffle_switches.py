"""
apps/core/management/commands/sync_waffle_switches.py — Switches to a manifest.

SNOW-1201. The switch counterpart of ``sync_waffle_flags``: diffs the
manifest at ``apps/core/fixtures/waffle_switches.json`` against the DB's
``waffle.Switch`` rows and reconciles by **create + delete only**. A missing
switch is created in the manifest's ``active`` state, a switch the manifest
no longer names is deleted, and an existing switch is never edited, so an
operator's off survives every deploy.

The first switches are the scheduled jobs', ``schedule_<job id>``, which
``schedule.py`` checks when each job fires; ``tests/test_schedule.py``
fails a job with no switch here. Read-only by default (prints the diff);
pass ``--commit`` to persist. Run on every deploy via ``build.sh``, after
``sync_waffle_flags``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError
from django.db import transaction
from waffle.models import Switch

from apps.core.command_register import RegisteredCommand
from apps.core.services.waffle_sync import compute_flag_diff, load_switch_manifest

logger = logging.getLogger(__name__)

# The command lives at apps/core/management/commands/, so parents[2] is the
# ``core`` app root, beside waffle_flags.json.
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "waffle_switches.json"
)


class Command(RegisteredCommand):
    """Reconcile waffle.Switch rows to apps/core/fixtures/waffle_switches.json.

    Read-only by default; ``--commit`` persists the create/delete diff.

    SNOW-602 exempt: a handful of named switches reconciled as name sets,
    not a growable queryset streamed row by row.
    """

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Reconcile waffle.Switch rows to the manifest at "
        "apps/core/fixtures/waffle_switches.json by create + delete only (an "
        "existing switch is never edited, so an operator's off survives). "
        "Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the create/delete diff. Without it, nothing is written.",
        )
        parser.add_argument(
            "--manifest",
            type=Path,
            default=DEFAULT_MANIFEST_PATH,
            help=(f"Path to the switch manifest. Defaults to {DEFAULT_MANIFEST_PATH}."),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Diff the switches against the manifest and apply it with --commit."""
        commit: bool = options["commit"]
        try:
            specs = {
                spec.name: spec for spec in load_switch_manifest(options["manifest"])
            }
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        db_names = set(Switch.objects.values_list("name", flat=True))
        to_create, to_delete = compute_flag_diff(set(specs), db_names)
        # Counts for the command run register (SNOW-1190).
        self.tally("created", len(to_create))
        self.tally("deleted", len(to_delete))

        if options["verbosity"] >= 1:
            self.stdout.write(
                f"Switches to create: {', '.join(sorted(to_create)) or '(none)'}"
            )
            self.stdout.write(
                f"Switches to delete: {', '.join(sorted(to_delete)) or '(none)'}"
            )
        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] dry-run — no rows written. Pass --commit to persist."
                )
            )
            return

        # Row by row, not in bulk: waffle invalidates its cache in save()
        # and delete(), which bulk operations skip. There are a handful.
        with transaction.atomic():
            for name in sorted(to_create):
                Switch.objects.create(
                    name=name, active=specs[name].active, note=specs[name].note
                )
            for switch in Switch.objects.filter(name__in=to_delete):
                switch.delete()
        logger.info(
            "sync_waffle_switches: created %s, deleted %s",
            sorted(to_create),
            sorted(to_delete),
        )
