"""
apps/core/management/commands/dump_settings.py — Redacted settings dump.

Answers "what is this environment actually configured with" without reading
the Render dashboard field by field (SNOW-580). Every secret is replaced
with a fixed marker — not a prefix or a hash, because a leaked prefix is
still a leak in a pasted support thread.

Each row also carries its validation problem, so the dump doubles as a
diagnosis rather than only telling you what is set. That is the same
information ``manage.py check`` reports, in a form you can read at a glance
against a service whose deploy is already failing.

Read-only by construction: it reads ``django.conf.settings`` and prints.
There is no ``--commit`` flag because there is nothing to commit.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.core.settings_spec import iter_settings_report


class Command(BaseCommand):
    """Print every environment-derived setting, with secrets redacted."""

    help = "Print a redacted dump of every environment-derived setting."

    def add_arguments(self, parser: Any) -> None:
        """Register the command's optional flags."""
        parser.add_argument(
            "--problems-only",
            action="store_true",
            help="Only print settings that failed validation.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Print the settings report, honouring --problems-only and --verbosity."""
        problems_only: bool = options["problems_only"]
        verbosity: int = options["verbosity"]

        rows = list(iter_settings_report())
        shown = [row for row in rows if row.problem] if problems_only else rows

        if not shown:
            if verbosity:
                self.stdout.write(self.style.SUCCESS("No configuration problems."))
            return

        name_width = max(len(row.name) for row in shown)
        problem_count = 0

        for row in shown:
            line = f"{row.name.ljust(name_width)}  {row.value}"
            if row.problem:
                problem_count += 1
                self.stdout.write(self.style.ERROR(line))
                self.stdout.write(f"{' ' * name_width}  !! {row.problem}")
            elif verbosity >= 2 and row.note:
                self.stdout.write(f"{line}    # {row.note}")
            else:
                self.stdout.write(line)

        if verbosity:
            self.stdout.write("")
            summary = f"{len(rows)} settings, {problem_count} problem(s)."
            style = self.style.ERROR if problem_count else self.style.SUCCESS
            self.stdout.write(style(summary))
