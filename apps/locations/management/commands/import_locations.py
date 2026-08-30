"""import_locations — reconcile Location and ResortLocation against the sheets.

The curated location estate is maintained in a spreadsheet, exported to two
tab-separated files, and reconciled here on exactly the conventions
``import_resorts`` already established: uuid-keyed, ``--mode add/update/
delete``, read-only unless ``--commit``.

**Two sheets, not one, and neither on the resort sheet.**

``apps/locations/data/locations.tsv``
    One row per place. ``uuid``, ``name``, ``kind``, ``latitude``,
    ``longitude``, ``note``.

``apps/locations/data/resort_locations.tsv``
    One row per resort-to-location link: ``resort_uuid``, ``location_uuid``,
    ``role``, ``is_primary``, plus ``resort_name``/``location_name``, which
    are **informational only** — they are what makes the sheet readable to a
    human, and are never matched on.

Flattening locations into resort columns (``top_name``, ``top_lat``,
``top_lon``) would cap how many a resort can have and force Mont Fort to be
repeated once per resort — the exact duplication the model exists to remove.
Two sheets let Mont Fort be one row that Verbier, Nendaz, Veysonnaz and
Thyon all reference. The links need their own sheet rather than a
multi-value cell for the same reason: a spreadsheet cell holding
``verbier:TOP;nendaz:TOP;…`` is nested data in a flat medium, and cannot be
sorted, filtered or diffed.

One command reconciles both, in one transaction: a link is meaningless
without the location it points at, and importing them separately would let a
half-applied run leave links dangling.

**Elevation is not a sheet column.** It is always derived, never supplied
(``docs/locations.md``) — an out-of-band pass resolves it from the
coordinate via Open-Meteo. That also makes the elevation a check on the
coordinate: a location whose resolved height is nowhere near the figure in
its ``note`` has been mis-pinned.

Like ``import_resorts``, this is deliberately **not** wired into
``build.sh``: location rows are editable data owned by each environment's
database, and a deploy that re-imported them would discard admin edits.

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed. A bare invocation prints the diff and exits 0.

Usage:
    # Preview a full reconciliation (default — no writes).
    uv run python manage.py import_locations

    # Apply it.
    uv run python manage.py import_locations --commit

    # Refresh curated fields only — create and delete nothing.
    uv run python manage.py import_locations --mode update --commit

    # Import a different export.
    uv run python manage.py import_locations --file /path/to/locations.tsv \
        --links-file /path/to/resort_locations.tsv
"""

from __future__ import annotations

import csv
import enum
import logging
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.coordinates import InvalidCoordinatesError, validate_coordinates
from apps.locations.models import Location, ResortLocation
from apps.regions.models import Resort

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_SHEET_PATH = _DATA_DIR / "locations.tsv"
DEFAULT_LINKS_PATH = _DATA_DIR / "resort_locations.tsv"

# Truthy spellings accepted in the links sheet's ``is_primary`` cell. A
# spreadsheet export writes whichever its author typed, and "TRUE"/"yes"/"1"
# all plainly mean the same thing; anything else is false.
_TRUTHY = frozenset({"1", "true", "yes", "y", "x"})


class Mode(enum.StrEnum):
    """Operations ``--mode`` can select. Values are the lowercase CLI names."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


_MODE_CHOICES = [mode.value for mode in Mode]

# Sheet column -> Location field. The sheet owns only these;
# ``elevation_m`` is resolved out-of-band and never written here.
LOCATION_FIELDS = ("name", "kind", "latitude", "longitude")

REQUIRED_LOCATION_COLUMNS = ("uuid", "name", "latitude", "longitude")
REQUIRED_LINK_COLUMNS = ("resort_uuid", "location_uuid", "role")


class Command(BaseCommand):
    """Reconcile Location and ResortLocation against the curated sheets."""

    help = (
        "Reconcile the curated location estate against "
        "apps/locations/data/locations.tsv and resort_locations.tsv: add "
        "rows the DB lacks, update rows it has, delete curated rows the "
        "sheets no longer list. Select operations with --mode "
        f"({' | '.join(_MODE_CHOICES)}); all three by default. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Apply the changes. Without this flag the command only "
                "reports what would change and exits 0."
            ),
        )
        # No argparse-level ``choices``: an unknown value is validated in
        # ``_resolve_modes`` so the error names every available mode, and so
        # casing can be normalised. Mirrors import_resorts.
        parser.add_argument(
            "--mode",
            nargs="+",
            metavar="MODE",
            default=_MODE_CHOICES,
            help=(
                "Operation(s) to perform, case-insensitive and combinable: "
                "add (create rows the sheets list but the DB lacks), "
                "update (overwrite curated fields on rows that exist), "
                "delete (remove curated rows the sheets no longer list). "
                "Default: all three."
            ),
        )
        parser.add_argument(
            "--file",
            type=Path,
            default=DEFAULT_SHEET_PATH,
            help=f"Path to the locations export (default: {DEFAULT_SHEET_PATH}).",
        )
        parser.add_argument(
            "--links-file",
            type=Path,
            default=DEFAULT_LINKS_PATH,
            help=f"Path to the links export (default: {DEFAULT_LINKS_PATH}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Diff the sheets against the DB and, with ``--commit``, apply them."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)
        modes = _resolve_modes(options["mode"])

        rows = _read_sheet(options["file"], REQUIRED_LOCATION_COLUMNS, ("kind", "note"))
        link_rows = _read_sheet(
            options["links_file"],
            REQUIRED_LINK_COLUMNS,
            ("is_primary", "resort_name", "location_name"),
            key="location_uuid",
        )

        plan = _build_plan(rows, link_rows, modes)
        self._report_plan(plan, modes, verbosity)

        if plan.errors:
            # Rule 4: a partially applicable sheet is a failure, not a
            # warning — exit non-zero so an operator (or CI) notices.
            for message in plan.errors:
                self.stderr.write(self.style.ERROR(message))
            raise CommandError(
                f"{len(plan.errors)} sheet row(s) could not be applied — "
                "nothing was written."
            )

        if not plan.has_changes:
            return

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — nothing written.")
                )
            return

        self._apply(plan, verbosity)

    def _report_plan(self, plan: _Plan, modes: set[Mode], verbosity: int) -> None:
        """Print the add/update/delete diff at the requested verbosity."""
        if verbosity < 1:
            return
        if not plan.has_changes and not plan.errors:
            self.stdout.write(
                "No changes — the DB already matches the sheets "
                f"({'/'.join(sorted(mode.value for mode in modes))})."
            )
            return

        self.stdout.write(
            f"{len(plan.additions)} location(s) to add, "
            f"{len(plan.updates)} to update, "
            f"{len(plan.deletions)} to delete; "
            f"{len(plan.link_additions)} link(s) to add, "
            f"{len(plan.link_updates)} to update, "
            f"{len(plan.link_deletions)} to delete."
        )
        if verbosity >= 2:
            self._report_locations(plan)
            self._report_links(plan)

    def _report_locations(self, plan: _Plan) -> None:
        """Print the per-location half of the verbose diff."""
        for location in plan.additions:
            self.stdout.write(f"  + {location.name}")
        for location, changes in plan.updates:
            self.stdout.write(f"  ~ {location.name}")
            self._report_changes(changes)
        for location in plan.deletions:
            self.stdout.write(f"  - {location.name}")

    def _report_links(self, plan: _Plan) -> None:
        """Print the per-link half of the verbose diff."""
        for link in plan.link_additions:
            self.stdout.write(
                f"  + link {link.resort} -> {link.location} [{link.role}]"
            )
        for link, changes in plan.link_updates:
            self.stdout.write(f"  ~ link {link.resort} -> {link.location}")
            self._report_changes(changes)
        for link in plan.link_deletions:
            self.stdout.write(f"  - link {link.resort} -> {link.location}")

    def _report_changes(self, changes: dict[str, tuple[Any, Any]]) -> None:
        """Print one row's field-level ``old -> new`` lines."""
        for field, (old, new) in sorted(changes.items()):
            self.stdout.write(f"      {field}: {old!r} -> {new!r}")

    def _apply(self, plan: _Plan, verbosity: int) -> None:
        """Persist the plan in a single transaction.

        Order matters: links are deleted **before** their locations, or the
        ``PROTECT`` on ``ResortLocation.location`` refuses the location
        delete; and locations are created before the links pointing at them.
        """
        with transaction.atomic():
            for location in plan.additions:
                location.save()
            for location, _changes in plan.updates:
                location.save()
            for link in plan.link_deletions:
                link.delete()
            for link in plan.link_additions:
                link.save()
            for link, _changes in plan.link_updates:
                link.save()
            for location in plan.deletions:
                location.delete()

        logger.info(
            "import_locations: locations +%d ~%d -%d, links +%d ~%d -%d",
            len(plan.additions),
            len(plan.updates),
            len(plan.deletions),
            len(plan.link_additions),
            len(plan.link_updates),
            len(plan.link_deletions),
        )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Added {len(plan.additions)} location(s), "
                    f"updated {len(plan.updates)}, "
                    f"deleted {len(plan.deletions)}; "
                    f"added {len(plan.link_additions)} link(s), "
                    f"updated {len(plan.link_updates)}, "
                    f"deleted {len(plan.link_deletions)}."
                )
            )


class _Plan:
    """The set of changes the sheets imply, computed before anything is written.

    Locations and links are tracked separately because they are applied in a
    specific order (see ``_apply``) and reported separately — a run that
    changes only links should say so rather than reporting zero locations.
    """

    def __init__(self) -> None:
        """Start with an empty plan."""
        self.additions: list[Location] = []
        self.updates: list[tuple[Location, dict[str, tuple[Any, Any]]]] = []
        self.deletions: list[Location] = []
        self.link_additions: list[ResortLocation] = []
        self.link_updates: list[tuple[ResortLocation, dict[str, tuple[Any, Any]]]] = []
        self.link_deletions: list[ResortLocation] = []
        self.errors: list[str] = []

    @property
    def has_changes(self) -> bool:
        """Return True when the plan would write anything."""
        return bool(
            self.additions
            or self.updates
            or self.deletions
            or self.link_additions
            or self.link_updates
            or self.link_deletions
        )


def _resolve_modes(names: list[str]) -> set[Mode]:
    """Return the ``Mode`` set named by ``--mode``, case-insensitively.

    Args:
        names: Raw ``--mode`` values as typed on the command line.

    Returns:
        The selected modes, de-duplicated.

    Raises:
        CommandError: If ``names`` is empty or holds an unknown mode.

    """
    valid = {mode.value: mode for mode in Mode}
    selected: set[Mode] = set()
    unknown: list[str] = []
    for name in names:
        mode = valid.get(name.strip().lower())
        if mode is None:
            unknown.append(name)
        else:
            selected.add(mode)
    if unknown:
        raise CommandError(
            f"--mode: unknown mode(s): {', '.join(unknown)}. "
            f"Available: {', '.join(_MODE_CHOICES)}."
        )
    if not selected:
        raise CommandError(f"--mode needs at least one of: {', '.join(_MODE_CHOICES)}.")
    return selected


def _read_sheet(
    path: Path,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    *,
    key: str = "uuid",
) -> list[dict[str, str]]:
    """Return a sheet's rows as dicts keyed by column name.

    Missing optional columns are filled with empty strings, so an export
    that omits one still loads.

    Args:
        path: Path to the tab-separated export.
        required: Columns without which the sheet cannot be read at all.
        optional: Columns filled with "" when the export omits them.
        key: The column whose emptiness marks a row as blank padding —
            spreadsheet exports routinely carry trailing empty rows.

    Returns:
        One dict per data row with a non-empty ``key`` column, in sheet order.

    Raises:
        CommandError: If the file is missing, unreadable, or lacks a
            required column.

    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Failed to read {path}: {exc}") from exc

    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    present = set(reader.fieldnames or [])
    missing = set(required).difference(present)
    if missing:
        raise CommandError(
            f"{path} is missing required column(s): {', '.join(sorted(missing))}"
        )

    columns = (*present, *optional)
    return [
        {column: (row.get(column) or "") for column in columns}
        for row in reader
        if (row.get(key) or "").strip()
    ]


def _build_plan(
    rows: list[dict[str, str]], link_rows: list[dict[str, str]], modes: set[Mode]
) -> _Plan:
    """Diff both sheets against the current tables.

    Args:
        rows: The locations sheet's data rows.
        link_rows: The links sheet's data rows.
        modes: The operations to plan for; anything not selected is skipped.

    Returns:
        A ``_Plan`` holding the pending changes and any errors.

    """
    plan = _Plan()
    # SNOW-602 exempt: bounded curated data. The scan is deliberately over
    # ``named()`` only — anonymous rows are minted from favourites and
    # observations (SNOW-704 / SNOW-709), are not the sheet's to own, and
    # must never be swept up by ``delete``.
    by_uuid = {str(location.uuid): location for location in Location.objects.named()}
    live_uuids: set[str] = set()

    for row in rows:
        uuid = row["uuid"].strip()
        location = by_uuid.get(uuid)
        live_uuids.add(uuid)

        if location is None:
            if Mode.ADD in modes:
                _plan_addition(plan, row, uuid)
            continue
        if Mode.UPDATE in modes:
            _plan_update(plan, location, row, uuid)

    if Mode.DELETE in modes:
        plan.deletions = [
            location
            for uuid, location in sorted(by_uuid.items(), key=lambda kv: kv[1].name)
            if uuid not in live_uuids
        ]

    _plan_links(plan, link_rows, modes, by_uuid)
    return plan


def _plan_addition(plan: _Plan, row: dict[str, str], uuid: str) -> None:
    """Stage a new (unsaved) Location for a sheet row the DB does not hold."""
    location = Location(uuid=uuid)
    try:
        _apply_row(location, row)
        location.full_clean(validate_unique=False, validate_constraints=False)
    except (ValueError, InvalidCoordinatesError) as exc:
        plan.errors.append(f"{row['name'].strip() or uuid}: {exc}")
        return
    except ValidationError as exc:
        plan.errors.append(f"{row['name'].strip() or uuid}: {exc.message_dict}")
        return
    plan.additions.append(location)


def _plan_update(
    plan: _Plan, location: Location, row: dict[str, str], uuid: str
) -> None:
    """Stage the sheet's curated values onto an existing Location."""
    try:
        changes = _apply_row(location, row)
        location.full_clean(validate_unique=False, validate_constraints=False)
    except (ValueError, InvalidCoordinatesError) as exc:
        plan.errors.append(f"{location.name} ({uuid}): {exc}")
        return
    except ValidationError as exc:
        plan.errors.append(f"{location.name} ({uuid}): {exc.message_dict}")
        return
    if changes:
        plan.updates.append((location, changes))


def _apply_row(location: Location, row: dict[str, str]) -> dict[str, tuple[Any, Any]]:
    """Assign the sheet's curated values onto a Location, in memory.

    Args:
        location: The location to write onto — unsaved.
        row: One row of the locations sheet.

    Returns:
        A ``field -> (old, new)`` map of what actually changed, for the
        dry-run diff.

    Raises:
        ValueError: If ``kind`` is not a recognised value, or a coordinate
            does not parse as a float.
        InvalidCoordinatesError: If a coordinate is non-finite or outside
            WGS-84 bounds — caught here rather than at the DB, which would
            otherwise happily store a NaN.

    """
    values: dict[str, Any] = {
        "name": row["name"].strip(),
        "kind": _kind_from_row(row),
        "latitude": _float_from_row(row, "latitude"),
        "longitude": _float_from_row(row, "longitude"),
    }
    validate_coordinates(values["latitude"], values["longitude"])

    changes: dict[str, tuple[Any, Any]] = {}
    for field in LOCATION_FIELDS:
        old = getattr(location, field, None)
        new = values[field]
        if old != new:
            changes[field] = (old, new)
            setattr(location, field, new)
    return changes


def _kind_from_row(row: dict[str, str]) -> str:
    """Read the sheet's ``kind`` cell, defaulting to empty.

    A blank cell is legitimate — it means an unclassified place, which is
    what a location minted from user data looks like. An *unrecognised*
    value is an error rather than a silent fallback: a typo quietly
    resolving to "" would strip a curated peak of its classification with
    no signal, which is exactly what the sheet exists to record.

    Args:
        row: One sheet row.

    Returns:
        A valid ``Location.KIND`` value, or "".

    Raises:
        ValueError: If the cell holds something that is not a KIND.

    """
    raw = (row.get("kind") or "").strip().upper()
    if not raw:
        return ""
    if raw not in Location.KIND.values:
        raise ValueError(
            f"has an unrecognised kind {raw!r} "
            f"(expected one of: {', '.join(Location.KIND.values)}, or blank)"
        )
    return raw


def _float_from_row(row: dict[str, str], column: str) -> float:
    """Read one required numeric cell.

    Args:
        row: One sheet row.
        column: The column to read.

    Returns:
        The parsed float.

    Raises:
        ValueError: If the cell is empty or does not parse.

    """
    raw = (row.get(column) or "").strip()
    if not raw:
        raise ValueError(f"has no {column}, which is required")
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"has a non-numeric {column} ({raw!r})") from exc


@dataclass(frozen=True)
class _LinkIndex:
    """The lookup tables one links-sheet row is resolved against.

    Bundled rather than passed as four parameters: they are built once and
    read together, and threading them individually made the signature the
    longest thing in the module.

    Attributes:
        resorts: Every resort, by uuid string.
        locations: Curated locations already in the DB, by uuid string.
        pending: Locations this run will create, by uuid string. They carry
            no pk yet, but ``_apply`` saves them before the links that point
            at them, and Django resolves an FK assigned an unsaved instance
            at save time — which is what makes a fresh sheet import in one
            pass rather than two.
        existing: Current links, by ``(resort_uuid, location_uuid)``.

    """

    resorts: dict[str, Resort]
    locations: dict[str, Location]
    pending: dict[str, Location]
    existing: dict[tuple[str, str], ResortLocation]


def _plan_links(
    plan: _Plan,
    link_rows: list[dict[str, str]],
    modes: set[Mode],
    by_uuid: dict[str, Location],
) -> None:
    """Diff the links sheet against the ResortLocation table.

    A link is keyed by its ``(resort, location)`` pair, matching the model's
    ``unique_together`` — so a curator changing a role edits a row rather
    than creating a second one.

    Args:
        plan: The plan to append to.
        link_rows: The links sheet's data rows.
        modes: The operations to plan for.
        by_uuid: The curated locations already in the DB, by uuid string.

    """
    index = _LinkIndex(
        resorts={str(resort.uuid): resort for resort in Resort.objects.all()},
        locations=by_uuid,
        pending={str(location.uuid): location for location in plan.additions},
        # Only links whose location is curated are the sheet's to own, for
        # the same reason ``delete`` is scoped to ``named()`` above.
        existing={
            (str(link.resort.uuid), str(link.location.uuid)): link
            for link in ResortLocation.objects.select_related(
                "resort", "location"
            ).filter(
                location__uuid__in=[location.uuid for location in by_uuid.values()]
            )
        },
    )

    live_keys = {
        key
        for key in (_plan_one_link(plan, row, modes, index) for row in link_rows)
        if key is not None
    }

    if Mode.DELETE in modes:
        plan.link_deletions = [
            link for key, link in index.existing.items() if key not in live_keys
        ]


def _plan_one_link(
    plan: _Plan, row: dict[str, str], modes: set[Mode], index: _LinkIndex
) -> tuple[str, str] | None:
    """Stage one links-sheet row, appending an addition, update or error.

    Args:
        plan: The plan to append to.
        row: One links-sheet row.
        modes: The operations to plan for.
        index: The lookup tables the row is resolved against.

    Returns:
        The row's ``(resort_uuid, location_uuid)`` key when it names a real
        pair — which keeps it out of the ``delete`` sweep — or ``None`` when
        the row could not be resolved at all.

    """
    resort_uuid = row["resort_uuid"].strip()
    location_uuid = row["location_uuid"].strip()
    label = (
        f"{row.get('resort_name', '').strip() or resort_uuid} -> "
        f"{row.get('location_name', '').strip() or location_uuid}"
    )

    resort = index.resorts.get(resort_uuid)
    if resort is None:
        plan.errors.append(f"{label}: resort {resort_uuid!r} does not exist.")
        return None

    location = index.locations.get(location_uuid) or index.pending.get(location_uuid)
    if location is None:
        plan.errors.append(
            f"{label}: location {location_uuid!r} is neither in the database "
            "nor in the locations sheet."
        )
        return None

    key = (resort_uuid, location_uuid)
    try:
        role = _role_from_row(row)
    except ValueError as exc:
        plan.errors.append(f"{label}: {exc}")
        # Still a live key: the pair exists in the sheet, and deleting it
        # because its role cell is malformed would compound one error with
        # another. The run aborts on plan.errors regardless.
        return key
    is_primary = (row.get("is_primary") or "").strip().lower() in _TRUTHY

    link = index.existing.get(key)
    if link is None:
        if Mode.ADD in modes:
            plan.link_additions.append(
                ResortLocation(
                    resort=resort,
                    location=location,
                    role=role,
                    is_primary=is_primary,
                )
            )
    elif Mode.UPDATE in modes:
        _plan_link_update(plan, link, role=role, is_primary=is_primary)
    return key


def _plan_link_update(
    plan: _Plan, link: ResortLocation, *, role: str, is_primary: bool
) -> None:
    """Stage the sheet's values onto an existing link, if either changed.

    Args:
        plan: The plan to append to.
        link: The existing link, written onto in memory but not saved.
        role: The role the sheet gives this link.
        is_primary: Whether the sheet marks this link primary.

    """
    changes: dict[str, tuple[Any, Any]] = {}
    if link.role != role:
        changes["role"] = (link.role, role)
        link.role = role
    if link.is_primary != is_primary:
        changes["is_primary"] = (link.is_primary, is_primary)
        link.is_primary = is_primary
    if changes:
        plan.link_updates.append((link, changes))


def _role_from_row(row: dict[str, str]) -> str:
    """Read the links sheet's ``role`` cell.

    Unlike ``kind``, ``role`` is **required**: a link that does not say what
    the location is to the resort carries no information the model does not
    already have from the FK pair.

    Args:
        row: One links-sheet row.

    Returns:
        A valid ``ResortLocation.ROLE`` value.

    Raises:
        ValueError: If the cell is blank or holds something that is not a ROLE.

    """
    raw = (row.get("role") or "").strip().upper()
    if not raw:
        raise ValueError("has no role, which is required")
    if raw not in ResortLocation.ROLE.values:
        raise ValueError(
            f"has an unrecognised role {raw!r} "
            f"(expected one of: {', '.join(ResortLocation.ROLE.values)})"
        )
    return raw
