"""
apps/core/management/commands/sync_waffle_flags.py — Reconcile Flags to a manifest.

Replaces the old "one data migration per flag" pattern (see
docs/feature-flags.md) with a single manifest at
``apps/core/fixtures/waffle_flags.json``. The command diffs the manifest against
the DB and reconciles by **create + delete only** — an existing flag's
admin-tuned targeting (``superusers``, ``everyone``, ``percent``, …) is
never edited in place, so an operator's live changes survive every deploy.

Read-only by default (prints the create/delete diff); pass ``--commit`` to
persist. Run on every deploy via ``build.sh``, after ``migrate``/``loaddata``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from waffle.models import Flag

from apps.core.services.waffle_sync import compute_flag_diff, load_manifest

logger = logging.getLogger(__name__)

# The command lives at apps/core/management/commands/, so parents[2] is the ``core``
# app root; the manifest sits alongside the app's other fixtures.
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "waffle_flags.json"
)


class Command(BaseCommand):
    """Reconcile waffle.Flag rows to apps/core/fixtures/waffle_flags.json.

    Read-only by default; ``--commit`` persists the create/delete diff.
    Flags present in both the manifest and the DB are left untouched.

    SNOW-602 exempt: no per-row queryset loop — a handful of named flags,
    reconciled as name sets via bulk create/delete.
    """

    help = (
        "Reconcile waffle.Flag rows to the declarative manifest at "
        "apps/core/fixtures/waffle_flags.json by create + delete only (never "
        "edit-in-place, so admin-tuned targeting on an existing flag is "
        "preserved). Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the create/delete diff. Without this flag the "
                "command is read-only."
            ),
        )
        parser.add_argument(
            "--manifest",
            type=Path,
            default=DEFAULT_MANIFEST_PATH,
            help=(
                f"Path to the flag manifest JSON. Defaults to {DEFAULT_MANIFEST_PATH}."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        manifest_path: Path = options["manifest"]

        try:
            specs = load_manifest(manifest_path)
        except (ValueError, FileNotFoundError) as exc:
            raise CommandError(str(exc)) from exc

        manifest_by_name = {spec.name: spec for spec in specs}
        db_names = set(Flag.objects.values_list("name", flat=True))
        to_create, to_delete = compute_flag_diff(set(manifest_by_name), db_names)

        if verbosity >= 1:
            self.stdout.write(
                f"Flags to create: {', '.join(sorted(to_create)) or '(none)'}"
            )
            self.stdout.write(
                f"Flags to delete: {', '.join(sorted(to_delete)) or '(none)'}"
            )

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] dry-run — no rows written. Pass --commit to persist."
                )
            )
            return

        with transaction.atomic():
            for name in to_create:
                spec = manifest_by_name[name]
                Flag.objects.create(name=name, **spec.create_defaults())
            if to_delete:
                Flag.objects.filter(name__in=to_delete).delete()

        logger.info(
            "sync_waffle_flags: created %d flag(s), deleted %d flag(s)",
            len(to_create),
            len(to_delete),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(to_create)} flag(s), deleted {len(to_delete)} flag(s)."
            )
        )
