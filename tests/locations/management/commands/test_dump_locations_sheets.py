"""
tests/locations/management/commands/test_dump_locations_sheets.py

Covers ``dump_locations_sheets`` (SNOW-755) — the write-back half of the
curated location estate.

The load-bearing property is the round trip: an edit made in the database
(by the in-map editor, or by hand) must come back out in a shape
``import_locations`` reads without change. So the central test writes a
pair of sheets, imports them, dumps them straight back and asserts the
files are byte-identical — and then that a second import reports no
changes at all.

The rest are the ways a dump can look like it worked and not have:
  - ``note`` has no database column behind it, so a dump rendered purely
    from the DB would blank every one of them.
  - ``resort_name`` / ``location_name`` are informational, matched on by
    nothing, and are exactly what makes a sheet of uuid pairs readable.
  - ``elevation_m`` is derived, never supplied — writing it would invite
    an edit the next resolve silently discards.
  - Anonymous locations (favourites, observations) are user data in the
    same table and are not the sheet's to own.
  - Nothing is written without ``--commit`` (CLAUDE.md rule 2).
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.locations.management.commands.dump_locations_sheets import (
    LINK_COLUMNS,
    LOCATION_COLUMNS,
)
from apps.locations.models import Location, ResortLocation
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
)

LOCATION_HEADER = "\t".join(LOCATION_COLUMNS)
LINK_HEADER = "\t".join(LINK_COLUMNS)

MONT_FORT = "0d0a2b6e-3f1f-4a9d-9f5b-1b0f6c2a7d03"
VERBIER_VILLAGE = "0d0a2b6e-3f1f-4a9d-9f5b-1b0f6c2a7d01"


def sheet_paths(tmp_path: Path) -> dict[str, Path]:
    """Return the ``call_command`` kwargs naming a pair of sheets in tmp.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Kwargs for ``call_command``: ``file`` and ``links_file``.

    """
    return {
        "file": tmp_path / "locations.tsv",
        "links_file": tmp_path / "resort_locations.tsv",
    }


def write_sheets(
    paths: dict[str, Path], locations: list[str], links: list[str]
) -> None:
    """Write both sheets, headers included.

    Args:
        paths: The ``sheet_paths`` mapping.
        locations: Data rows for the locations sheet, without the header.
        links: Data rows for the links sheet, without the header.

    """
    paths["file"].write_text(
        "\n".join([LOCATION_HEADER, *locations]) + "\n", encoding="utf-8"
    )
    paths["links_file"].write_text(
        "\n".join([LINK_HEADER, *links]) + "\n", encoding="utf-8"
    )


def dump(paths: dict[str, Path], *args: str) -> str:
    """Run the command against ``paths`` and return its stdout.

    Args:
        paths: The ``sheet_paths`` mapping.
        *args: Extra command-line flags (e.g. ``"--commit"``).

    Returns:
        Everything the command wrote to stdout.

    """
    out = StringIO()
    call_command("dump_locations_sheets", *args, stdout=out, **paths)
    return out.getvalue()


def rows_of(path: Path) -> list[str]:
    """Return a sheet's data rows, header and trailing blank removed.

    Args:
        path: The sheet to read.

    Returns:
        One string per data row, in file order.

    """
    return path.read_text(encoding="utf-8").splitlines()[1:]


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRoundTrip:
    """import_locations → DB → dump_locations_sheets → the same files."""

    def test_dump_reproduces_the_sheets_it_was_imported_from(
        self, tmp_path: Path
    ) -> None:
        """A dump of an unmodified import is byte-identical to its input."""
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        paths = sheet_paths(tmp_path)
        write_sheets(
            paths,
            [
                f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.09610\t"
                "7.22860\t4 Vallées; resolves to 1494 m.",
                f"{MONT_FORT}\tMont Fort\tPEAK\t46.10361\t7.29889\tShared summit.",
            ],
            [
                f"{verbier.uuid}\t{VERBIER_VILLAGE}\tBASE\tyes\tVerbier\t"
                "Verbier village",
                # The two Mont Fort links are written in the order the dump
                # emits them — by location uuid, then resort uuid — because
                # a factory-minted resort uuid is random and the sheet's
                # order is a property of the dump, not of the input.
                *sorted(
                    [
                        f"{verbier.uuid}\t{MONT_FORT}\tTOP\t\tVerbier\tMont Fort",
                        f"{nendaz.uuid}\t{MONT_FORT}\tTOP\t\tNendaz\tMont Fort",
                    ]
                ),
            ],
        )
        before = {
            name: path.read_text(encoding="utf-8") for name, path in paths.items()
        }

        call_command("import_locations", "--commit", stdout=StringIO(), **paths)
        dump(paths, "--commit")

        after = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
        assert after == before

    def test_a_reimport_of_a_dump_reports_no_changes(self, tmp_path: Path) -> None:
        """The proof the loop is closed: import after dump is a no-op.

        A DB-side edit (a moved pin, a new link) is only durable once the
        sheets carry it, and only genuinely round-tripped if importing
        them back changes nothing.
        """
        verbier = ResortFactory.create(name="Verbier")
        nendaz = ResortFactory.create(name="Nendaz")
        mont_fort = LocationFactory.create(
            name="Mont Fort",
            kind=Location.KIND.PEAK,
            latitude=46.10361,
            longitude=7.29889,
        )
        ResortLocationFactory.create(resort=verbier, location=mont_fort, role="TOP")
        ResortLocationFactory.create(resort=nendaz, location=mont_fort, role="TOP")
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")
        out = StringIO()
        call_command("import_locations", stdout=out, **paths)

        assert "No changes" in out.getvalue()

    def test_headers_match_what_import_locations_reads(self, tmp_path: Path) -> None:
        """Both sheets carry every column the importer requires."""
        link = ResortLocationFactory.create()
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        assert paths["file"].read_text(encoding="utf-8").splitlines()[0] == (
            "uuid\tname\tkind\tlatitude\tlongitude\tnote"
        )
        assert paths["links_file"].read_text(encoding="utf-8").splitlines()[0] == (
            "resort_uuid\tlocation_uuid\trole\tis_primary\tresort_name\tlocation_name"
        )
        assert str(link.location.uuid) in paths["file"].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# What the emitted rows carry
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmittedRows:
    """The columns a dump must preserve, and the one it must not write."""

    def test_note_survives_a_round_trip(self, tmp_path: Path) -> None:
        """``note`` has no DB column, so it is carried off the old sheet."""
        resort = ResortFactory.create(name="Verbier")
        paths = sheet_paths(tmp_path)
        note = "4 Vallées. Verified: resolves to 1494 m against a base of 1500 m."
        write_sheets(
            paths,
            [f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.09610\t7.22860\t{note}"],
            [f"{resort.uuid}\t{VERBIER_VILLAGE}\tBASE\tyes\tVerbier\tVerbier village"],
        )
        call_command("import_locations", "--commit", stdout=StringIO(), **paths)

        dump(paths, "--commit")

        assert rows_of(paths["file"]) == [
            f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.09610\t7.22860\t{note}"
        ]

    def test_a_location_with_no_prior_row_gets_an_empty_note(
        self, tmp_path: Path
    ) -> None:
        """A pin placed in the editor has no note, and that is not an error."""
        LocationFactory.create(
            name="Mont Fort",
            kind=Location.KIND.PEAK,
            latitude=46.10361,
            longitude=7.29889,
        )
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        assert rows_of(paths["file"])[0].endswith(
            "\tMont Fort\tPEAK\t46.10361\t7.29889\t"
        )

    def test_link_rows_carry_both_readable_names(self, tmp_path: Path) -> None:
        """``resort_name``/``location_name`` are rendered from the live rows."""
        resort = ResortFactory.create(name="Nendaz")
        location = LocationFactory.create(name="Mont Fort", kind=Location.KIND.PEAK)
        ResortLocationFactory.create(
            resort=resort, location=location, role="TOP", is_primary=True
        )
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        assert rows_of(paths["links_file"]) == [
            f"{resort.uuid}\t{location.uuid}\tTOP\tyes\tNendaz\tMont Fort"
        ]

    def test_is_primary_writes_as_yes_or_empty(self, tmp_path: Path) -> None:
        """Both spellings are what ``import_locations``' _TRUTHY expects."""
        location = LocationFactory.create(name="Attelas")
        ResortLocationFactory.create(location=location, is_primary=False, role="MID")
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        assert rows_of(paths["links_file"])[0].split("\t")[3] == ""

    def test_elevation_is_never_written(self, tmp_path: Path) -> None:
        """It is derived from the coordinate, so the sheet must not own it."""
        LocationFactory.create(name="Mont Fort", resolved=True, elevation_m=3328.0)
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        text = paths["file"].read_text(encoding="utf-8")
        assert "elevation" not in text
        assert "3328" not in text

    def test_anonymous_locations_are_not_dumped(self, tmp_path: Path) -> None:
        """A location minted from a favourite is user data, not sheet data."""
        LocationFactory.create(name="Mont Fort")
        anonymous = LocationFactory.create(anonymous=True)
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        text = paths["file"].read_text(encoding="utf-8")
        assert "Mont Fort" in text
        assert str(anonymous.uuid) not in text

    def test_a_link_to_an_anonymous_location_is_not_dumped(
        self, tmp_path: Path
    ) -> None:
        """The links sheet is scoped to the same estate as the locations one.

        Emitting a link whose location the sheet omits would make the pair
        unimportable — ``import_locations`` fails a link naming a location
        that is in neither the DB nor the sheet.
        """
        anonymous = LocationFactory.create(anonymous=True)
        ResortLocationFactory.create(location=anonymous, role="BASE")
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        assert rows_of(paths["links_file"]) == []

    def test_rows_are_ordered_by_uuid(self, tmp_path: Path) -> None:
        """Stable order is what makes a git diff mean something."""
        for name in ("C", "A", "B"):
            LocationFactory.create(name=name)
        paths = sheet_paths(tmp_path)

        dump(paths, "--commit")

        uuids = [row.split("\t")[0] for row in rows_of(paths["file"])]
        assert uuids == sorted(uuids)


# ---------------------------------------------------------------------------
# Safe by default
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDryRun:
    """CLAUDE.md rule 2 — a bare run writes nothing and exits 0."""

    def test_nothing_is_written_without_commit(self, tmp_path: Path) -> None:
        """The bare invocation reports the diff and leaves the files alone."""
        LocationFactory.create(name="Mont Fort")
        paths = sheet_paths(tmp_path)
        write_sheets(paths, [], [])
        before = paths["file"].read_text(encoding="utf-8")

        out = dump(paths)

        assert "would change" in out
        assert "Dry-run" in out
        assert paths["file"].read_text(encoding="utf-8") == before

    def test_a_missing_sheet_is_not_created_without_commit(
        self, tmp_path: Path
    ) -> None:
        """A first run in a fresh checkout still writes nothing."""
        LocationFactory.create(name="Mont Fort")
        paths = sheet_paths(tmp_path)

        dump(paths)

        assert not paths["file"].exists()
        assert not paths["links_file"].exists()

    def test_a_matching_database_reports_no_changes(self, tmp_path: Path) -> None:
        """A second dump of an unchanged estate is a quiet no-op."""
        LocationFactory.create(name="Mont Fort")
        paths = sheet_paths(tmp_path)
        dump(paths, "--commit")

        out = dump(paths)

        assert "No changes" in out
        assert "would change" not in out


# ---------------------------------------------------------------------------
# The delete leg
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemovals:
    """A row deleted in the DB leaves the sheet on the next dump."""

    def test_a_deleted_link_disappears_from_the_sheet(self, tmp_path: Path) -> None:
        """Unlinking in the editor must reach git as a removed row."""
        location = LocationFactory.create(name="Mont Fort")
        keep = ResortLocationFactory.create(location=location, role="TOP")
        drop = ResortLocationFactory.create(location=location, role="MID")
        paths = sheet_paths(tmp_path)
        dump(paths, "--commit")
        assert len(rows_of(paths["links_file"])) == 2

        ResortLocation.objects.filter(pk=drop.pk).delete()
        dump(paths, "--commit")

        rows = rows_of(paths["links_file"])
        assert len(rows) == 1
        assert rows[0].startswith(str(keep.resort.uuid))
