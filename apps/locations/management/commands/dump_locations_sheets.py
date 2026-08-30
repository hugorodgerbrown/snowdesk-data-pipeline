"""dump_locations_sheets — export the curated location estate to its sheets.

The other half of ``import_locations``. After a session of placing summits
with the in-map location editor (``/?edit=locations`` — SNOW-755), or a
local ``import_locations --commit`` run, the edits live only in this
environment's database. This command re-emits
``apps/locations/data/locations.tsv`` and
``apps/locations/data/resort_locations.tsv`` from the current rows so the
operator can ``git diff`` and commit them.

That closes the loop: **sheet → DB via import_locations, DB → sheet via
this command.** An edit only becomes durable — reaching other worktrees, CI
and every deployed environment's next reconciliation — once it is back in
the sheets.

Three things about the emitted shape are load-bearing, and each is a test:

**``note`` is carried forward, not derived.** It is a sheet column with no
database column behind it — ``import_locations`` reads and discards it,
because it is a curator's working note ("resolves to 1494 m against the
resort sheet's base of 1500 m"), not data the application uses. So the dump
reads the notes off the sheet already on disk, keyed by uuid, and writes
them back. A row the DB has and the sheet does not gets an empty note,
which is what a location placed by clicking a map genuinely has.

**``elevation_m`` is never written.** It is derived by
out-of-band from the coordinate, never supplied
(``docs/locations.md``) — emitting it would invite someone to edit it, and
the next resolve would silently overwrite the edit.

**Only the curated estate is dumped.** ``Location.objects.named()``, for
the same reason ``import_locations``'s delete mode is scoped that way: the
anonymous rows minted from favourites and field observations are user data
that happens to live in the same table, and they are not the sheet's to
own.

That scope carries an invariant with it: **no anonymous location may hold
a resort link.** The links sheet can only reference locations the
locations sheet lists, so a link hung on an anonymous row cannot be
written, and re-importing what was written would not restore it. The
editor's write paths uphold the invariant — ``edit_location_create``
requires a name, and ``edit_location_save`` / ``edit_location_link`` are
both scoped to ``named()`` — but the admin's ``ResortLocation`` inline is
not, so an admin can still create one by hand. When that has happened,
this command names each dropped link on stdout and in the log rather than
writing a quietly incomplete sheet: the operator would otherwise commit a
diff missing a link nothing told them about.

Rows are ordered by uuid — locations by their own, links by the location's
then the resort's — so consecutive runs produce identical files and a
``git diff`` shows only what actually changed. Ordering links by location
also keeps the resorts sharing one location adjacent, which is the sheet's
readable shape.

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed. A bare invocation prints a per-file ``+added/-removed`` summary and
exits 0 without writing anything.

Usage:
    # Preview what would change (default — no writes).
    uv run python manage.py dump_locations_sheets

    # Actually write both sheets.
    uv run python manage.py dump_locations_sheets --commit

    # Write somewhere else (both flags mirror import_locations').
    uv run python manage.py dump_locations_sheets --commit \
        --file /tmp/locations.tsv --links-file /tmp/resort_locations.tsv
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
from apps.locations.management.commands.import_locations import (
    DEFAULT_LINKS_PATH,
    DEFAULT_SHEET_PATH,
)
from apps.locations.models import Location, ResortLocation

logger = logging.getLogger(__name__)

# Column order for each sheet. These are the contract with
# ``import_locations``, which requires the first four location columns and
# the first three link columns and treats the rest as optional — so the
# order here is what makes the emitted file readable, and the *names* are
# what make it importable.
LOCATION_COLUMNS = ("uuid", "name", "kind", "latitude", "longitude", "note")
LINK_COLUMNS = (
    "resort_uuid",
    "location_uuid",
    "role",
    "is_primary",
    "resort_name",
    "location_name",
)

# Decimal places for an emitted coordinate. Five is ~1 m in Switzerland,
# which is both the precision the in-map editors read out and the precision
# the committed sheet already uses — and the API rounds a placed pin to the
# same figure, so a dump followed by an import is a genuine no-op rather
# than a diff of trailing digits.
COORD_DECIMALS = 5

# What ``is_primary`` writes as when true. Inside ``import_locations``'
# ``_TRUTHY`` set, and the spelling the committed sheet already uses.
PRIMARY_TRUE = "yes"


class Command(BaseCommand):
    """Re-emit the curated location sheets from the current DB rows."""

    help = (
        "Dump the curated Location and ResortLocation rows to "
        "apps/locations/data/locations.tsv and resort_locations.tsv, in the "
        "column order import_locations reads. Read-only unless --commit is "
        "passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write both sheets to disk. Without this flag the command "
                "only reports what would change and exits 0."
            ),
        )
        parser.add_argument(
            "--file",
            type=Path,
            default=DEFAULT_SHEET_PATH,
            help=f"Path to the locations sheet (default: {DEFAULT_SHEET_PATH}).",
        )
        parser.add_argument(
            "--links-file",
            type=Path,
            default=DEFAULT_LINKS_PATH,
            help=f"Path to the links sheet (default: {DEFAULT_LINKS_PATH}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Render both sheets and, with ``--commit``, write them."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)
        sheet_path: Path = options["file"]
        links_path: Path = options["links_file"]

        # SNOW-602 exempt: bounded curated data — the estate is a few
        # hundred rows by design, and both sheets are rendered whole before
        # either is written, so a partially-dumped pair cannot exist.
        locations = list(Location.objects.named().order_by("uuid"))
        links = list(
            ResortLocation.objects.filter(location__in=locations)
            .select_related("resort", "location")
            .order_by("location__uuid", "resort__uuid")
        )

        self._warn_about_dropped_links(verbosity)

        new_sheet = render_locations_sheet(locations, notes_from(sheet_path))
        new_links = render_links_sheet(links)

        changed = self._report(
            [(sheet_path, new_sheet), (links_path, new_links)],
            len(locations),
            len(links),
            verbosity,
        )
        if not changed:
            return

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — nothing written.")
                )
            return

        _write(sheet_path, new_sheet)
        _write(links_path, new_links)
        logger.info(
            "dump_locations_sheets: wrote %d location(s) and %d link(s)",
            len(locations),
            len(links),
        )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {display_path(sheet_path)} and "
                    f"{display_path(links_path)} — review the diff and "
                    "commit when satisfied."
                )
            )

    def _warn_about_dropped_links(self, verbosity: int) -> None:
        """Name every link this dump silently cannot carry.

        A link whose location is anonymous is dropped, because the
        locations sheet emits only ``named()`` rows and a links row
        pointing at a location neither the sheet nor the database holds
        fails the next ``import_locations``. The editor's write paths
        refuse to create one, but the admin's ``ResortLocation`` inline
        does not — so this can still happen, and the failure mode is the
        bad one: the operator commits a diff that is missing a link
        nothing told them about.

        A warning rather than an error. The rows are legitimate database
        state, the dump of everything else is still correct, and blocking
        a whole curation session on one stray inline edit would be a
        worse trade than naming it.

        Args:
            verbosity: The run's ``--verbosity``; the warning is stdout
                noise below 1 but is always logged.

        """
        dropped = (
            ResortLocation.objects.filter(location__in=Location.objects.anonymous())
            .select_related("resort", "location")
            .order_by("resort__name", "pk")
        )
        for link in dropped:
            logger.warning(
                "dump_locations_sheets: dropping link %s -> %s (%s): the "
                "location is anonymous, so the sheets cannot carry it",
                link.resort.name,
                link.location.uuid,
                link.location.to_string(),
            )
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"Not dumping the link {link.resort.name} -> "
                        f"{link.location.uuid} ({link.location.to_string()}): "
                        "the location has no name, so it is not part of the "
                        "curated estate and neither sheet can carry it. Name "
                        "it in the admin, or remove the link."
                    )
                )

    def _report(
        self,
        pending: list[tuple[Path, str]],
        location_count: int,
        link_count: int,
        verbosity: int,
    ) -> bool:
        """Print a per-file diff summary and say whether anything changed.

        Args:
            pending: ``(path, new_text)`` for each sheet, in the order they
                would be written.
            location_count: Rows in the locations sheet.
            link_count: Rows in the links sheet.
            verbosity: The run's ``--verbosity``.

        Returns:
            True when at least one sheet's rendered text differs from what
            is on disk.

        """
        changed = False
        for path, new_text in pending:
            old_text = path.read_text(encoding="utf-8") if path.exists() else ""
            if old_text == new_text:
                continue
            changed = True
            added, removed = diff_line_counts(old_text, new_text)
            if verbosity >= 1:
                self.stdout.write(
                    f"{display_path(path)} would change (+{added}/-{removed} lines)."
                )
        if not changed and verbosity >= 1:
            self.stdout.write(
                f"No changes — both sheets match the current DB "
                f"({location_count} location(s), {link_count} link(s))."
            )
        return changed


def notes_from(path: Path) -> dict[str, str]:
    """Read the ``note`` column off the sheet on disk, keyed by uuid.

    The note has no database column behind it, so a dump that rendered
    purely from the DB would blank every one of them — silently, and in a
    way that looks like the command worked. Reading them back is what makes
    the round trip lossless.

    A missing or unreadable file yields an empty map rather than an error:
    the first dump into a fresh checkout has no prior sheet to carry
    anything forward from, and that is a legitimate run.

    Args:
        path: Path to the locations sheet.

    Returns:
        ``{uuid: note}`` for every row that carries a non-empty note.

    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Failed to read {path}: {exc}") from exc

    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    return {
        (row.get("uuid") or "").strip(): (row.get("note") or "").strip()
        for row in reader
        if (row.get("uuid") or "").strip() and (row.get("note") or "").strip()
    }


def render_locations_sheet(locations: list[Location], notes: dict[str, str]) -> str:
    """Return the locations sheet's full text for ``locations``.

    Args:
        locations: The curated rows, already in emission order.
        notes: ``{uuid: note}`` carried forward from the sheet on disk.

    Returns:
        The tab-separated text, header included and newline-terminated.

    """
    return _render(
        LOCATION_COLUMNS,
        [
            {
                "uuid": str(location.uuid),
                "name": location.name,
                "kind": location.kind,
                "latitude": format_coordinate(location.latitude),
                "longitude": format_coordinate(location.longitude),
                "note": notes.get(str(location.uuid), ""),
            }
            for location in locations
        ],
    )


def render_links_sheet(links: list[ResortLocation]) -> str:
    """Return the links sheet's full text for ``links``.

    ``resort_name`` and ``location_name`` are informational — nothing
    matches on them — but they are what makes a sheet of uuid pairs
    legible to the person editing it, so they are rendered from the live
    rows rather than carried forward.

    Args:
        links: The links, already in emission order.

    Returns:
        The tab-separated text, header included and newline-terminated.

    """
    return _render(
        LINK_COLUMNS,
        [
            {
                "resort_uuid": str(link.resort.uuid),
                "location_uuid": str(link.location.uuid),
                "role": link.role,
                "is_primary": PRIMARY_TRUE if link.is_primary else "",
                "resort_name": link.resort.name,
                "location_name": link.location.name,
            }
            for link in links
        ],
    )


def format_coordinate(value: float) -> str:
    """Return a coordinate as the sheet spells it — fixed to 5 decimals.

    Fixed rather than ``repr``: a ragged column of 46.0961 next to
    46.103612 is harder to scan, and the trailing digits are below the
    precision anything upstream of this actually has.

    Args:
        value: The WGS-84 coordinate.

    Returns:
        The formatted cell.

    """
    return f"{value:.{COORD_DECIMALS}f}"


def _render(columns: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    """Return one sheet's text from its column order and its rows.

    Written with ``csv``'s default (minimal) quoting rather than by joining
    on tabs, so that whatever the reader on the other side would need in
    order to parse a cell back — a name holding a tab, a note holding a
    quote — is what gets written. Minimal quoting means an ordinary cell is
    emitted bare, so the committed sheets are reproduced character for
    character and only a genuinely awkward value ever gains quotes.

    Args:
        columns: Header names, in emission order.
        rows: One dict per data row, keyed by column name.

    Returns:
        The tab-separated text, header included and newline-terminated.

    """
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write(path: Path, text: str) -> None:
    """Write one sheet, turning an OS error into a CommandError.

    Args:
        path: Destination (overwritten).
        text: The rendered sheet.

    Raises:
        CommandError: If the file cannot be written.

    """
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Failed to write {path}: {exc}") from exc
