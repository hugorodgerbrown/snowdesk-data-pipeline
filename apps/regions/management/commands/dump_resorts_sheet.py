"""dump_resorts_sheet — export the Resort table to its curated sheet.

The other half of ``import_resorts``, and the direction that did not exist
before SNOW-817. After a session of placing resort coordinates in the in-map
editor (``/map/?edit=resorts``, superuser-only — SNOW-74/SNOW-724), or an admin
edit, those changes live only in that environment's database. This command
re-emits ``apps/regions/data/resorts.tsv`` from the current rows so the
operator can ``git diff`` and commit them.

That closes the loop: **sheet → DB via import_resorts, DB → sheet via this
command.** An edit only becomes durable — reaching other worktrees, CI and
every other environment's next reconciliation — once it is back in the sheet.

It replaces ``dump_resorts_fixture``, which wrote ``resorts.json``. That
fixture is gone: the sheet is now the only file that describes a resort
(SNOW-817). Before the merge, 77 coordinates existed *only* in the fixture,
because the map editor wrote to the database and the database was dumped to
JSON, while the sheet was maintained by hand and never received them.

Two things about the emitted shape are load-bearing, and each is a test:

**Retired rows are dumped, and old markers carried forward.** Since
SNOW-1404 retiring a resort is a state, not a deletion, so every row comes
from ``Resort.all_objects``: a retired one is written with its ``status``
(the reason), ``retired_at`` and ``retired_note``. A sheet row whose status
is set but which the database does not hold — a place rejected before the
database kept retired rows — is read back off the sheet on disk, keyed by
uuid, and written out unchanged, so the record of it is never lost.

**Existing rows keep their position.** Output follows the on-disk sheet's
row order, with rows the sheet has never seen appended in name order. A
re-pinned coordinate is then a one-line diff rather than a reordered file.

Safe-by-default: read-only unless ``--commit`` is passed. A bare invocation
prints a one-line diff summary and exits 0 without writing anything.

Usage:
    # Preview what would change (default — no writes).
    uv run python manage.py dump_resorts_sheet

    # Actually write the updated sheet.
    uv run python manage.py dump_resorts_sheet --commit
"""

from __future__ import annotations

import csv
import io
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_output import diff_line_counts, display_path
from apps.core.command_register import RegisteredCommand
from apps.regions.management.commands.import_resorts import DEFAULT_SHEET_PATH
from apps.regions.models import Resort

logger = logging.getLogger(__name__)

# The sheet's columns, in the order they are written. This is the schema
# ``import_resorts`` reads, so the two must agree — a column added here
# without a reader there is a column that silently does nothing.
SHEET_COLUMNS: tuple[str, ...] = (
    "uuid",
    "name",
    "kind",
    "tier",
    "passes",
    "canton",
    "region",
    "latitude",
    "longitude",
    "name_alt",
    "operator_name",
    "website",
    "why_it_matters",
    "top_elevation_m",
    "base_elevation_m",
    "num_runs",
    "num_lifts",
    "total_piste_km",
    "typical_season_open",
    "typical_season_close",
    "status",
    # SNOW-1404: when and why the resort was retired; blank when live.
    "retired_at",
    "retired_note",
    "notes",
    "geocode_source",
    "geocode_confidence",
    "needs_review",
)


class Command(RegisteredCommand):
    """Re-emit apps/regions/data/resorts.tsv from the current DB rows."""

    help = (
        "Dump the Resort table to apps/regions/data/resorts.tsv, retired rows "
        "included with their status, carrying forward the retired rows the "
        "database does not hold. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the sheet to disk. Without this flag the command "
            "only reports what would change and exits 0.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Render the sheet and (optionally) write it to disk."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        new_text = render_resorts_sheet(DEFAULT_SHEET_PATH)
        old_text = (
            DEFAULT_SHEET_PATH.read_text(encoding="utf-8")
            if DEFAULT_SHEET_PATH.exists()
            else ""
        )

        if new_text == old_text:
            if verbosity >= 1:
                self.stdout.write("No changes — the sheet matches the current DB.")
            return

        added, removed = diff_line_counts(old_text, new_text)
        if verbosity >= 1:
            # SNOW-602 exempt: bounded curated data — a few hundred Resort
            # rows, not a growable table — and stdout carries a file diff
            # summary, not a per-row countdown.
            self.stdout.write(
                f"resorts.tsv would change ({Resort.all_objects.count()} DB rows; "
                f"+{added}/-{removed} lines)."
            )

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing sheet.")
                )
            return

        write_resorts_sheet(DEFAULT_SHEET_PATH)
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {display_path(DEFAULT_SHEET_PATH)} — review "
                    "the diff and commit when satisfied."
                )
            )


def write_resorts_sheet(sheet_path: Path = DEFAULT_SHEET_PATH) -> None:
    """Write the current Resort table to ``sheet_path``.

    Extracted to module scope so ``audit_resort_regions`` can call it after
    re-FKing resorts without importing or invoking the command class.

    Args:
        sheet_path: Destination path. Overwritten in place.

    Raises:
        CommandError: If the file cannot be written.

    """
    text = render_resorts_sheet(sheet_path)
    try:
        sheet_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Failed to write {sheet_path}: {exc}") from exc
    logger.info(
        "write_resorts_sheet: wrote %d DB row(s) to %s",
        Resort.all_objects.count(),
        sheet_path,
    )


def render_resorts_sheet(sheet_path: Path = DEFAULT_SHEET_PATH) -> str:
    """Return the sheet's full text for the current Resort table.

    Args:
        sheet_path: The sheet already on disk. Read for its row order and
            for the retired rows the database does not hold; never written.

    Returns:
        The tab-separated sheet, header included, ending in a newline.

    """
    existing = _read_existing(sheet_path)
    db_rows = {
        str(resort.uuid): _row_from_resort(resort)
        for resort in Resort.all_objects.select_related("region")
        .prefetch_related("passes")
        .order_by("name")
    }

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for uuid, old_row in existing.items():
        if uuid in db_rows:
            rows.append(db_rows[uuid])
            seen.add(uuid)
        elif old_row.get("status", "").strip():
            # A retired row the database does not hold: carried through
            # verbatim, so the record of a rejected place is kept.
            rows.append(old_row)
            seen.add(uuid)
        # A live sheet row with no DB row is a resort somebody deleted. It is
        # dropped here, which is the honest record — re-add it by restoring
        # the row, not by leaving a stale line in the file.

    rows.extend(row for uuid, row in db_rows.items() if uuid not in seen)

    buffer = io.StringIO()
    fieldnames: list[str] = list(SHEET_COLUMNS)
    writer = csv.DictWriter(
        buffer, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _read_existing(sheet_path: Path) -> dict[str, dict[str, str]]:
    """Return the on-disk sheet's rows, keyed by uuid, in file order.

    Args:
        sheet_path: The sheet to read. A missing file is not an error — the
            first dump into an empty directory writes every DB row.

    Returns:
        uuid -> row, with every column in ``SHEET_COLUMNS`` present.

    """
    if not sheet_path.exists():
        return {}
    text = sheet_path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    rows: dict[str, dict[str, str]] = {}
    for row in reader:
        uuid = (row.get("uuid") or "").strip()
        if not uuid:
            continue
        rows[uuid] = {column: (row.get(column) or "") for column in SHEET_COLUMNS}
    return rows


def _row_from_resort(resort: Resort) -> dict[str, str]:
    """Render one Resort as a sheet row.

    ``status`` is the retirement reason, blank for a live resort, beside
    its ``retired_at`` and ``retired_note`` (SNOW-1404).

    Args:
        resort: The row to render, with ``region`` already selected and
            ``passes`` prefetched.

    Returns:
        Every column in ``SHEET_COLUMNS``, as strings.

    """
    return {
        "uuid": str(resort.uuid),
        "name": resort.name,
        "kind": resort.kind,
        "tier": resort.tier,
        # Read from the prefetch cache, so this costs no query per row.
        "passes": ",".join(sorted(ski_pass.slug for ski_pass in resort.passes.all())),
        "canton": resort.canton,
        "region": resort.region.region_id,
        "latitude": _number(resort.latitude),
        "longitude": _number(resort.longitude),
        "name_alt": resort.name_alt,
        "operator_name": resort.operator_name,
        "website": resort.website,
        "why_it_matters": resort.why_it_matters,
        "top_elevation_m": _number(resort.top_elevation_m),
        "base_elevation_m": _number(resort.base_elevation_m),
        "num_runs": _number(resort.num_runs),
        "num_lifts": _number(resort.num_lifts),
        "total_piste_km": _number(resort.total_piste_km),
        "typical_season_open": resort.typical_season_open,
        "typical_season_close": resort.typical_season_close,
        "status": resort.retired_reason,
        "retired_at": resort.retired_at.isoformat() if resort.retired_at else "",
        "retired_note": resort.retired_note,
        "notes": resort.notes,
        "geocode_source": resort.geocode_source,
        "geocode_confidence": _number(resort.geocode_confidence),
        "needs_review": "true" if resort.needs_review else "false",
    }


def _number(value: float | int | None) -> str:
    """Render a numeric cell, or an empty string for ``None``.

    ``repr`` is used for a fractional float rather than ``str`` because it is
    the shortest representation that round-trips exactly, so a coordinate
    written here and read back by ``import_resorts`` is the same float.

    A float with no fractional part is written without one: ``total_piste_km``
    and ``geocode_confidence`` are ``FloatField``s that mostly hold round
    numbers, and rendering 210 as ``210.0`` would churn 118 lines of the sheet
    the first time it was dumped, for no change in meaning.

    Args:
        value: The number to render.

    Returns:
        The cell's text.

    """
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)
