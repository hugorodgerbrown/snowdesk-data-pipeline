"""import_resorts — reconcile the Resort table against the curated sheet.

``apps/regions/data/resorts.tsv`` is **the** file that describes a resort.
SNOW-817 retired ``apps/regions/fixtures/resorts.json``, which had been a
second, partially-overlapping description of the same 164 rows: the sheet
carried the editorial columns, the fixture carried the coordinates placed in
the map editor, and 77 coordinates plus every ``notes`` value existed in only
one of them. This command reads the sheet; ``dump_resorts_sheet`` writes it
back from the database.

The sheet carries ``Resort``'s editorial columns (operator, website, the
``why_it_matters`` line, the map ``tier``, elevations, lift/run counts, piste
length, typical season dates, ``notes``), the coordinate pair, and the
provenance of that coordinate. This command reconciles the database against
it in up to three modes, selected with ``--mode`` (all three by default):

  ``add``     Create a resort for a sheet row with no matching ``uuid``.
  ``update``  Overwrite the editorial fields of rows that do match.
  ``delete``  Remove resorts the sheet does not list.

The sheet's *live* set is every row whose ``status`` column is blank.
``NOT_A_SKI_RESORT`` there retires an entry that was never a lift-served
resort (a pass, a reservoir, a valley town, a touring label), and is the
only other value the column accepts — anything else is an error, not a
silent "live". ``delete`` removes both the retired rows and any resort
missing from the sheet altogether; ``add`` and ``update`` ignore them.

Because ``delete`` deletes anything the sheet does not list, a resort
created in the admin reaches the sheet on its next export or is removed by
the next full run. Use ``--mode update`` (or ``add update``) to reconcile
without that risk, and read the dry-run diff either way.

Adding requires ``region`` (a MicroRegion ``region_id`` such as
``CH-4115``) and ``canton`` columns, which the editorial export carries —
a row that must be created without them is reported as an error rather
than guessed at. Every other column is optional.

``region``, ``canton``, the ``latitude``/``longitude`` pair and the three
provenance columns (``geocode_source``, ``geocode_confidence``,
``needs_review``) are **creation-time only**: they are read when a row is
first created and never written again. A resort re-pinned in the map editor
owns its own position afterwards, so re-running the import cannot drag it
back to whatever the sheet happened to say (SNOW-544). The way a coordinate
edit *does* travel between environments is `dump_resorts_sheet` → commit →
a fresh database's first import.

Deliberately *not* wired into ``build.sh`` — see
``docs/decisions/resorts-are-editable-data.md``. Resort rows are editable
data, owned by each environment's database; a deploy that re-imported them
would throw away every admin and map-editor edit.

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed. A bare invocation prints the diff and exits 0.

Usage:
    # Preview a full reconciliation (default — no writes).
    uv run python manage.py import_resorts

    # Apply it.
    uv run python manage.py import_resorts --commit

    # Refresh editorial fields only — create and delete nothing.
    uv run python manage.py import_resorts --mode update --commit

    # Import a different export of the sheet.
    uv run python manage.py import_resorts --file /path/to/resorts.tsv
"""

from __future__ import annotations

import csv
import enum
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)

DEFAULT_SHEET_PATH = Path(__file__).resolve().parents[2] / "data" / "resorts.tsv"

# The one non-blank ``status`` the sheet recognises. It retires the resort —
# the sheet's way of saying "this row was never a lift-served ski area".
#
# SNOW-817 gave the marker its own column. It used to be a prefix on the
# ``note`` column, so one cell carried both the curator's prose and a
# machine-read verdict, and the prose could not begin with those characters
# without deleting the resort.
DELETE_MARKER = "NOT_A_SKI_RESORT"


class Mode(enum.StrEnum):
    """Operations ``--mode`` can select. Values are the lowercase CLI names."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


_MODE_CHOICES = [mode.value for mode in Mode]

# Sheet column -> model field. The sheet owns only these. ``region``,
# ``canton`` and the coordinate pair are read separately: they are needed to
# create a resort but are never overwritten on one that already exists.
TEXT_FIELDS = (
    "name",
    "name_alt",
    "operator_name",
    "website",
    "why_it_matters",
    "typical_season_open",
    "typical_season_close",
    "notes",
)
INT_FIELDS = ("top_elevation_m", "base_elevation_m", "num_runs", "num_lifts")
FLOAT_FIELDS = ("total_piste_km",)

# Read only when a row is created — see ``_coordinates_from_row``.
COORDINATE_COLUMNS = ("latitude", "longitude")

# Also creation-time only, and for the same reason: after the first save the
# database owns the pin and its provenance. Without these the sheet could
# not say "this coordinate was placed by an operator and reviewed", and
# every import would demote 77 reviewed pins to IMPORT/needs_review.
PROVENANCE_COLUMNS = ("geocode_source", "geocode_confidence", "needs_review")

# Without these the sheet cannot be keyed or read at all.
REQUIRED_COLUMNS = ("uuid", "name", "status")


class Command(BaseCommand):
    """Reconcile the Resort table against apps/regions/data/resorts.tsv."""

    help = (
        "Reconcile the Resort table against the curated sheet "
        "(apps/regions/data/resorts.tsv): add rows the DB lacks, update rows it "
        "has, delete resorts the sheet does not list. Select operations "
        f"with --mode ({' | '.join(_MODE_CHOICES)}); all three by default. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Apply the changes. Without this flag the command only "
            "reports what would change and exits 0.",
        )
        # No argparse-level ``choices``: an unknown value is validated in
        # ``_resolve_modes`` so the error names every available mode rather
        # than argparse's terse rendering, and so casing can be normalised.
        parser.add_argument(
            "--mode",
            nargs="+",
            metavar="MODE",
            default=_MODE_CHOICES,
            help=(
                "Operation(s) to perform, case-insensitive and combinable: "
                "add (create resorts the sheet lists but the DB lacks), "
                "update (overwrite editorial fields on rows that exist), "
                "delete (remove resorts the sheet does not list). "
                "Default: all three."
            ),
        )
        parser.add_argument(
            "--file",
            type=Path,
            default=DEFAULT_SHEET_PATH,
            help=f"Path to the sheet export (default: {DEFAULT_SHEET_PATH}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Diff the sheet against the DB and, with ``--commit``, apply it."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)
        modes = _resolve_modes(options["mode"])

        rows = _read_sheet(options["file"], modes)
        plan = _build_plan(rows, modes)

        self._report_plan(plan, modes, verbosity)

        if plan.errors:
            # Rule 5: a partially applicable sheet is a failure, not a
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
                f"No changes — the DB already matches the sheet "
                f"({'/'.join(sorted(mode.value for mode in modes))})."
            )
            return

        self.stdout.write(
            f"{len(plan.additions)} resort(s) to add, "
            f"{len(plan.updates)} to update, "
            f"{len(plan.deletions)} to delete."
        )
        if verbosity < 2:
            return
        for resort in plan.additions:
            self.stdout.write(f"  + {resort.name}")
        for resort, changes in plan.updates:
            self.stdout.write(f"  ~ {resort.name}")
            for field, (old, new) in sorted(changes.items()):
                self.stdout.write(f"      {field}: {old!r} -> {new!r}")
        for resort in plan.deletions:
            self.stdout.write(f"  - {resort.name}")

    def _apply(self, plan: _Plan, verbosity: int) -> None:
        """Persist the plan in a single transaction."""
        with transaction.atomic():
            for resort in plan.additions:
                resort.save()
            for resort, _changes in plan.updates:
                resort.save()
            for resort in plan.deletions:
                resort.delete()

        logger.info(
            "import_resorts: added %d, updated %d, deleted %d resort(s): %s",
            len(plan.additions),
            len(plan.updates),
            len(plan.deletions),
            [resort.name for resort in plan.deletions],
        )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Added {len(plan.additions)} resort(s), "
                    f"updated {len(plan.updates)}, "
                    f"deleted {len(plan.deletions)}."
                )
            )


class _Plan:
    """The set of changes a sheet implies, computed before anything is written.

    ``additions`` holds unsaved new resorts; ``updates`` holds existing
    resorts with the sheet's values already assigned in memory (but
    unsaved) alongside a field -> ``(old, new)`` map for reporting;
    ``deletions`` holds the resorts the sheet does not list; ``errors``
    holds human-readable reasons the sheet could not be applied in full.
    """

    def __init__(self) -> None:
        """Start with an empty plan."""
        self.additions: list[Resort] = []
        self.updates: list[tuple[Resort, dict[str, tuple[Any, Any]]]] = []
        self.deletions: list[Resort] = []
        self.errors: list[str] = []

    @property
    def has_changes(self) -> bool:
        """Return True when the plan would write anything."""
        return bool(self.additions or self.updates or self.deletions)


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


def _read_sheet(path: Path, modes: set[Mode]) -> list[dict[str, str]]:
    """Return the sheet's rows as dicts keyed by column name.

    Missing optional columns are filled with empty strings, so an export
    that omits a column the caller's modes don't need still loads.

    Args:
        path: Path to the tab-separated export.
        modes: The selected operations — ``add`` additionally requires the
            ``region`` and ``canton`` columns needed to create a row.

    Returns:
        One dict per data row with a non-empty ``uuid``, in sheet order.

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
    missing = set(REQUIRED_COLUMNS).difference(present)
    if missing:
        raise CommandError(
            f"{path} is missing required column(s): {', '.join(sorted(missing))}"
        )

    optional = (
        *TEXT_FIELDS,
        *INT_FIELDS,
        *FLOAT_FIELDS,
        "region",
        "canton",
        *COORDINATE_COLUMNS,
        *PROVENANCE_COLUMNS,
    )
    rows = []
    for row in reader:
        if not (row.get("uuid") or "").strip():
            continue
        columns = (*present, *optional)
        rows.append({column: (row.get(column) or "") for column in columns})

    if Mode.ADD in modes and not {"region", "canton"}.issubset(present):
        # Not fatal on its own — only rows that actually need creating fail,
        # and today's export has none. Warn through the plan instead.
        logger.info(
            "import_resorts: %s has no region/canton column, so no resort can "
            "be created from it",
            path,
        )
    return rows


def _build_plan(rows: list[dict[str, str]], modes: set[Mode]) -> _Plan:
    """Diff the sheet rows against the current Resort table.

    Args:
        rows: The sheet's data rows, as returned by ``_read_sheet``.
        modes: The operations to plan for; anything not selected is skipped.

    Returns:
        A ``_Plan`` holding the pending additions, updates, deletions and
        errors.

    """
    plan = _Plan()
    # SNOW-602 exempt: bounded curated data — a few hundred Resort rows,
    # not a growable table.
    by_uuid = {str(resort.uuid): resort for resort in Resort.objects.all()}
    live_uuids: set[str] = set()

    for row in rows:
        uuid = row["uuid"].strip()
        resort = by_uuid.get(uuid)

        try:
            retired = _is_retired(row)
        except ValueError as exc:
            plan.errors.append(f"{row['name'].strip() or uuid}: {exc}")
            continue
        if retired:
            # Retired by the sheet: excluded from the live set, so ``delete``
            # picks it up below. Never added or updated.
            continue
        live_uuids.add(uuid)

        if resort is None:
            if Mode.ADD in modes:
                _plan_addition(plan, row, uuid)
            continue
        if Mode.UPDATE in modes:
            _plan_update(plan, resort, row, uuid)

    if Mode.DELETE in modes:
        plan.deletions = [
            resort
            for uuid, resort in sorted(by_uuid.items(), key=lambda kv: kv[1].name)
            if uuid not in live_uuids
        ]
    return plan


def _plan_addition(plan: _Plan, row: dict[str, str], uuid: str) -> None:
    """Stage a new (unsaved) Resort for a sheet row the DB does not hold."""
    region_id = row["region"].strip()
    canton = row["canton"].strip()
    if not region_id or not canton:
        plan.errors.append(
            f"{row['name'].strip() or uuid}: cannot be created — the sheet "
            "has no region/canton for it, and both are required. Create the "
            "resort in the admin, then re-export the sheet."
        )
        return
    region = MicroRegion.objects.filter(region_id=region_id).first()
    if region is None:
        plan.errors.append(
            f"{row['name'].strip() or uuid}: region {region_id!r} does not exist."
        )
        return

    resort = Resort(uuid=uuid, region=region, canton=canton)
    try:
        coordinates = _coordinates_from_row(row)
        if coordinates is not None:
            resort.latitude, resort.longitude = coordinates
            _stamp_provenance(resort, row)
        _apply_row(resort, row)
        resort.full_clean(validate_unique=False, validate_constraints=False)
    except ValueError as exc:
        plan.errors.append(f"{row['name'].strip() or uuid}: {exc}")
        return
    except ValidationError as exc:
        plan.errors.append(f"{row['name'].strip() or uuid}: {exc.message_dict}")
        return
    plan.additions.append(resort)


def _is_retired(row: dict[str, str]) -> bool:
    """
    Read the sheet's ``status`` cell (SNOW-817).

    Blank means a live resort — the overwhelming majority of rows.
    ``NOT_A_SKI_RESORT`` retires it: the row stays in the sheet as a record
    of a place that was considered and rejected, and ``--mode delete``
    removes it from the database.

    An unrecognised value is an error rather than a silent fallback, for
    the reason ``_kind_from_row`` rejects one: a typo resolving quietly to
    "live" would put a retired row back on the map as a resort pin, and a
    typo resolving quietly to "retired" would delete a real resort.

    Args:
        row: One sheet row.

    Returns:
        True when the sheet retires this row.

    Raises:
        ValueError: If the cell holds something that is neither blank nor
            the marker.

    """
    raw = (row.get("status") or "").strip().upper()
    if not raw:
        return False
    if raw != DELETE_MARKER:
        raise ValueError(f"unknown status {raw!r} (expected blank or {DELETE_MARKER})")
    return True


def _stamp_provenance(resort: Resort, row: dict[str, str]) -> None:
    """
    Record how a newly-created resort's coordinate was obtained (SNOW-817).

    The sheet is a mirror of a real database, so it can say that a pin was
    placed by an operator in the map editor and reviewed. When it does, that
    provenance is carried through verbatim.

    When it does not — a row a curator typed a coordinate into by hand — the
    fallback is the older, more cautious stamp: ``IMPORT`` and
    ``needs_review``. The panel's ``MANUAL`` / ``confidence=1.0`` /
    ``needs_review=False`` combination asserts that somebody placed this pin
    on a map, which is not true of a coordinate that merely arrived as sheet
    data. Flagging it for review is the honest record, and the panel
    re-stamps it on the first save.

    Args:
        resort: The newly-constructed resort, already carrying coordinates.
        row: One sheet row.

    Raises:
        ValueError: If the sheet names a source that is not a
            ``GeocodeSource``, or a confidence that does not parse.

    """
    raw_source = (row.get("geocode_source") or "").strip().upper()
    resort.geocoded_at = timezone.now()

    if not raw_source:
        resort.geocode_source = Resort.GeocodeSource.IMPORT
        resort.needs_review = True
        return

    if raw_source not in Resort.GeocodeSource.values:
        valid = ", ".join(Resort.GeocodeSource.values)
        raise ValueError(
            f"unknown geocode_source {raw_source!r} (expected one of: {valid})"
        )
    resort.geocode_source = raw_source

    raw_confidence = (row.get("geocode_confidence") or "").strip()
    if raw_confidence:
        try:
            resort.geocode_confidence = float(raw_confidence)
        except ValueError as exc:
            raise ValueError(
                f"has a non-numeric geocode_confidence ({raw_confidence!r})"
            ) from exc

    # Anything but an explicit "true" is False — the column is a flag, and a
    # blank cell on a sheet that names a source means "not flagged".
    resort.needs_review = (row.get("needs_review") or "").strip().lower() == "true"


def _coordinates_from_row(row: dict[str, str]) -> tuple[float, float] | None:
    """
    Read the sheet's ``latitude``/``longitude`` pair, if it carries one (SNOW-544).

    Coordinates are optional: the sheet spent most of its life without them,
    and a row that omits them still creates a resort — one with no pin, which
    then needs placing in the edit-resorts panel before it appears on the map.

    A **half-filled** pair is an error rather than a silent single-axis
    value, because a resort at latitude-only is not a location; it would
    save as a null coordinate and look, from every consumer's side,
    identical to a row that never supplied one.

    Args:
        row: One sheet row.

    Returns:
        The ``(latitude, longitude)`` pair, or ``None`` when the row omits
        both.

    Raises:
        ValueError: If exactly one of the two is filled, or either does not
            parse as a float.

    """
    raw_lat = (row.get("latitude") or "").strip()
    raw_lon = (row.get("longitude") or "").strip()
    if not raw_lat and not raw_lon:
        return None
    if not raw_lat or not raw_lon:
        missing = "longitude" if raw_lat else "latitude"
        raise ValueError(f"has a latitude/longitude pair with no {missing}")
    try:
        return float(raw_lat), float(raw_lon)
    except ValueError as exc:
        raise ValueError(
            f"has a non-numeric coordinate ({raw_lat!r}, {raw_lon!r})"
        ) from exc


def _plan_update(plan: _Plan, resort: Resort, row: dict[str, str], uuid: str) -> None:
    """Stage the sheet's editorial values onto an existing Resort."""
    try:
        changes = _apply_row(resort, row)
        resort.full_clean(validate_unique=False, validate_constraints=False)
    except ValueError as exc:
        # A non-numeric elevation/count/piste-km cell in the sheet.
        plan.errors.append(f"{resort.name} ({uuid}): {exc}")
        return
    except ValidationError as exc:
        plan.errors.append(f"{resort.name} ({uuid}): {exc.message_dict}")
        return
    if changes:
        plan.updates.append((resort, changes))


def _kind_from_row(row: dict[str, str]) -> str:
    """
    Read the sheet's ``kind`` cell, defaulting to ``RESORT`` (SNOW-544).

    A blank or absent cell means ``RESORT`` — the overwhelming majority of
    rows, and the shape every row had before the column existed, so the
    sheet does not have to be filled in exhaustively to stay importable.

    An unrecognised value is an error rather than a silent fallback. A typo
    quietly resolving to ``RESORT`` would put lift-less terrain back on the
    map as a resort pin, which is the exact failure this column was added
    to prevent.

    Args:
        row: One sheet row.

    Returns:
        A valid ``Resort.Kind`` value.

    Raises:
        ValueError: If the cell holds something that is not a Kind.

    """
    raw = (row.get("kind") or "").strip().upper()
    if not raw:
        return Resort.Kind.RESORT
    if raw not in Resort.Kind.values:
        valid = ", ".join(Resort.Kind.values)
        raise ValueError(f"unknown kind {raw!r} (expected one of: {valid})")
    return raw


def _tier_from_row(row: dict[str, str]) -> str:
    """
    Read the sheet's ``tier`` cell, defaulting to ``STANDARD`` (SNOW-543).

    A blank or absent cell means ``STANDARD`` — the middle of the three
    tiers and the shape every row had before the column existed, so an
    export that predates it still imports.

    An unrecognised value is an error rather than a silent fallback, for
    the same reason ``_kind_from_row`` rejects one: a typo resolving
    quietly to the default would demote a Core resort to an ordinary pin
    with nothing in the output saying so.

    Args:
        row: One sheet row.

    Returns:
        A valid ``Resort.Tier`` value.

    Raises:
        ValueError: If the cell holds something that is not a Tier.

    """
    raw = (row.get("tier") or "").strip().upper()
    if not raw:
        return Resort.Tier.STANDARD
    if raw not in Resort.Tier.values:
        valid = ", ".join(Resort.Tier.values)
        raise ValueError(f"unknown tier {raw!r} (expected one of: {valid})")
    return raw


def _apply_row(resort: Resort, row: dict[str, str]) -> dict[str, tuple[Any, Any]]:
    """Assign the sheet's editorial values onto ``resort`` in memory.

    The resort is not saved — the caller validates first and persists the
    whole plan in one transaction. ``region``, ``canton`` and the
    ``latitude``/``longitude`` pair are not touched here: they are set once
    at creation and owned by the database after that. Coordinates in
    particular must never reach this path — it runs on every update, so
    assigning them here would drag a resort that had been re-pinned in the
    map editor back to the sheet's value on the next import (SNOW-544).

    Args:
        resort: The resort the row keys on — existing or newly constructed.
        row: One sheet row.

    Returns:
        A field -> ``(old, new)`` map of the values that actually changed;
        empty when the row matches the database.

    Raises:
        ValueError: If a numeric cell does not parse.

    """
    values: dict[str, Any] = {field: row[field].strip() for field in TEXT_FIELDS}
    values["kind"] = _kind_from_row(row)
    values["tier"] = _tier_from_row(row)
    for field in INT_FIELDS:
        raw = row[field].strip()
        values[field] = int(raw) if raw else None
    for field in FLOAT_FIELDS:
        raw = row[field].strip()
        values[field] = float(raw) if raw else None

    changes: dict[str, tuple[Any, Any]] = {}
    for field, new in values.items():
        old = getattr(resort, field)
        if old != new:
            changes[field] = (old, new)
            setattr(resort, field, new)
    return changes
