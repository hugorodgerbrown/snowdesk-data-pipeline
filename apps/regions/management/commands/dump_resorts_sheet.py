"""dump_resorts_sheet — export the Resort table to its curated sheet.

The other half of ``import_resorts``, and the direction that did not exist
before SNOW-817. After a session of placing resort coordinates in the in-map
editor (``/?edit=resorts``, superuser-only — SNOW-74/SNOW-724), or an admin
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

**Retired rows are carried forward, not derived.** A row whose ``status`` is
``NOT_A_SKI_RESORT`` is deliberately absent from the database — that is what
retiring it means — so it cannot be dumped from one. Those rows are read
back off the sheet already on disk, keyed by uuid, and written out
unchanged. Losing them would silently un-retire every rejected place on the
next ``import_resorts`` run, because ``--mode delete`` decides what to remove
by what the sheet does *not* list.

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

from django.core.management.base import BaseCommand, CommandError

from apps.core.command_output import diff_line_counts, display_path
from apps.regions.management.commands.import_resorts import (
    DEFAULT_SHEET_PATH,
    DELETE_MARKER,
)
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
    "notes",
    "geocode_source",
    "geocode_confidence",
    "needs_review",
)


class Command(BaseCommand):
    """Re-emit apps/regions/data/resorts.tsv from the current DB rows."""

    help = (
        "Dump the Resort table to apps/regions/data/resorts.tsv, carrying "
        "forward the retired (NOT_A_SKI_RESORT) rows the database does not "
        "hold. Read-only unless --commit is passed."
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
                f"resorts.tsv would change ({Resort.objects.count()} DB rows; "
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
        Resort.objects.count(),
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
        for resort in Resort.objects.select_related("region").order_by("name")
    }

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for uuid, old_row in existing.items():
        if old_row.get("status", "").strip().upper() == DELETE_MARKER:
            # Retired: absent from the DB by definition, so carry it through
            # verbatim rather than dropping it.
            rows.append(old_row)
            seen.add(uuid)
        elif uuid in db_rows:
            rows.append(db_rows[uuid])
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

    ``status`` is always blank: a row the database holds is by definition
    not retired, and retiring one is a curator's edit to the sheet, not
    something the database can express.

    Args:
        resort: The row to render, with ``region`` already selected.

    Returns:
        Every column in ``SHEET_COLUMNS``, as strings.

    """
    return {
        "uuid": str(resort.uuid),
        "name": resort.name,
        "kind": resort.kind,
        "tier": resort.tier,
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
        "status": "",
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
