"""
apps/core/command_output.py — Shared reporting helpers for dump commands.

A "dump" command re-emits a committed data file from the current database
rows so an operator can ``git diff`` it: ``dump_resorts_sheet`` writes
``apps/regions/data/resorts.tsv``, ``dump_locations_sheets`` writes the
two TSVs under ``apps/locations/data/``. Both are read-only without
``--commit`` (CLAUDE.md management-command rule 2), which means both have to
answer the same two questions before writing anything:

1. **How much would change?** — ``diff_line_counts`` reduces the old and new
   texts to an ``+added/-removed`` pair, which is what a dry run prints. It
   is a summary, not a diff: the command's job is to say whether the write
   is worth making, and ``git diff`` says the rest afterwards.
2. **Where did it go?** — ``display_path`` renders the destination
   repo-relative for the normal case of a run from the project root.

Both lived privately in the resort dumper until SNOW-755 added a
second dump command. Two callers is the bar CLAUDE.md sets for an
abstraction, so they moved here rather than being copied.

Lives in ``apps/core/`` alongside the other flat cross-app command helpers
(``command_iteration.py``), which is where a command in any app already
looks for one.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def display_path(path: Path) -> str:
    """Return ``path`` relative to the cwd, or absolute if it isn't below it.

    ``Path.relative_to`` raises ``ValueError`` rather than falling back, so
    calling it unguarded made this message crash for any destination outside
    the working directory — and it runs *after* the write, so the file was
    already on disk and only the confirmation was lost (SNOW-659).

    Args:
        path: The destination the command wrote to.

    Returns:
        The repo-relative path for the normal case of a run from the project
        root, else the absolute path.

    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def diff_line_counts(old: str, new: str) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts between two text blobs.

    Set membership rather than a sequence diff: a dump reorders nothing (its
    ordering is fixed and stable by contract), so a line that appears in
    both texts is unchanged wherever it sits, and counting by set is both
    cheaper and less misleading than a ``difflib`` opcode walk that would
    report a moved line as one removal plus one addition.

    Args:
        old: The text currently on disk, or "" when the file is absent.
        new: The text the command would write.

    Returns:
        A ``(added, removed)`` pair for the dry-run summary line.

    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    old_set = set(old_lines)
    new_set = set(new_lines)
    added = sum(1 for line in new_lines if line not in old_set)
    removed = sum(1 for line in old_lines if line not in new_set)
    return added, removed
