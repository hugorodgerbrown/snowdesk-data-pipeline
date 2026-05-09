"""dump_resorts_fixture — export the Resort table to its fixture file.

After a session of placing resort coordinates via the in-map editor
(``/map/?edit=resorts`` in DEBUG mode — SNOW-74), edits live only in
the local SQLite. This command re-emits ``regions/fixtures/resorts.json``
from the current DB rows so the operator can ``git diff`` and commit the
change. Without it, edits silently disappear on the next ``loaddata`` run.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a one-line diff summary and exits 0 without writing
anything. Mirrors ``refresh_eaws_fixtures``'s shape (CLAUDE.md
management-command rules).

Output uses ``use_natural_foreign_keys=True`` so the ``region`` column
emits as ``["CH-4115"]`` rather than a numeric PK — matching the
existing fixture format and keeping fixtures portable across DB resets.

Usage:
    # Preview what would change (default — no writes).
    poetry run python manage.py dump_resorts_fixture

    # Actually write the updated fixture.
    poetry run python manage.py dump_resorts_fixture --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

from regions.models import Resort

logger = logging.getLogger(__name__)

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "resorts.json"
)


class Command(BaseCommand):
    """Re-emit regions/fixtures/resorts.json from the current DB rows."""

    help = (
        "Dump the Resort table to regions/fixtures/resorts.json with natural "
        "foreign keys. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the fixture to disk. Without this flag the command "
            "only reports what would change and exits 0.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Serialise Resort rows and (optionally) write the fixture file."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        # Order by pk so the fixture keeps a stable diff across runs (matches
        # the existing resorts.json which is pk-ordered).
        queryset = Resort.objects.order_by("pk").all()
        new_text = _serialise_with_natural_keys(queryset)

        old_text = (
            _FIXTURE_PATH.read_text(encoding="utf-8") if _FIXTURE_PATH.exists() else ""
        )

        if new_text == old_text:
            if verbosity >= 1:
                self.stdout.write("No changes — fixture matches the current DB.")
            return

        added, removed = _diff_line_counts(old_text, new_text)
        if verbosity >= 1:
            self.stdout.write(
                f"resorts.json would change ({queryset.count()} rows; "
                f"+{added}/-{removed} lines)."
            )

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixture.")
                )
            return

        try:
            _FIXTURE_PATH.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"Failed to write {_FIXTURE_PATH}: {exc}") from exc

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {_FIXTURE_PATH.relative_to(Path.cwd())} — review "
                    "the diff and commit when satisfied."
                )
            )


def _serialise_with_natural_keys(queryset: Any) -> str:
    """Return the fixture JSON text with natural FKs and pretty indentation."""
    raw = serializers.serialize(
        "json",
        queryset,
        use_natural_foreign_keys=True,
        indent=2,
    )
    # Ensure a trailing newline so editors that strip-on-save don't show a
    # one-line diff every time the fixture is round-tripped.
    if not raw.endswith("\n"):
        raw += "\n"
    # Re-encode through json so non-ASCII characters round-trip as UTF-8
    # (Django's default ``ensure_ascii=True`` would emit ``é`` for é,
    # whereas the existing fixture uses literal UTF-8 — keep parity).
    parsed = json.loads(raw)
    return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"


def _diff_line_counts(old: str, new: str) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts between two text blobs."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    old_set = set(old_lines)
    new_set = set(new_lines)
    added = sum(1 for line in new_lines if line not in old_set)
    removed = sum(1 for line in old_lines if line not in new_set)
    return added, removed
