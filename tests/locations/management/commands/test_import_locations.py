"""
tests/locations/management/commands/test_import_locations.py

Covers ``import_locations`` (SNOW-701):
  - Adds, updates and deletes locations and links under --commit.
  - Dry-run (no --commit) reports the same plan and writes nothing.
  - Idempotent — a second --commit run reports no changes.
  - A location and its links import in ONE pass, not two.
  - The sharing the model exists for: one location, four resorts, one row.
  - delete is scoped to the curated estate — an anonymous location minted
    from user data is never swept up.
  - Sheet errors (bad kind, bad role, unknown resort, bad coordinate) fail
    the whole run with a non-zero exit and write nothing.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location, ResortLocation
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
)

LOCATION_HEADER = "uuid\tname\tkind\tlatitude\tlongitude\tnote"
LINK_HEADER = "resort_uuid\tlocation_uuid\trole\tis_primary\tresort_name\tlocation_name"

MONT_FORT = "0d0a2b6e-3f1f-4a9d-9f5b-1b0f6c2a7d03"
VERBIER_VILLAGE = "0d0a2b6e-3f1f-4a9d-9f5b-1b0f6c2a7d01"


def write_sheets(
    tmp_path: Path, locations: list[str], links: list[str]
) -> dict[str, Path]:
    """Write a pair of sheet fixtures and return the two paths.

    Args:
        tmp_path: pytest's per-test temporary directory.
        locations: Data rows for the locations sheet, without the header.
        links: Data rows for the links sheet, without the header.

    Returns:
        Kwargs for ``call_command``: ``file`` and ``links_file``.

    """
    sheet = tmp_path / "locations.tsv"
    sheet.write_text("\n".join([LOCATION_HEADER, *locations]), encoding="utf-8")
    link_sheet = tmp_path / "resort_locations.tsv"
    link_sheet.write_text("\n".join([LINK_HEADER, *links]), encoding="utf-8")
    return {"file": sheet, "links_file": link_sheet}


@pytest.mark.django_db
class TestImportLocationsCommit:
    """--commit applies the plan."""

    def test_adds_a_location_and_its_link_in_one_pass(self, tmp_path: Path) -> None:
        """A fresh sheet imports both halves in a single run.

        A link points at a location the same run creates, which has no pk
        when the link is planned. Django resolves the FK at save time, and
        _apply saves locations first — so this must not need a second pass.
        """
        resort = ResortFactory.create(name="Verbier")
        sheets = write_sheets(
            tmp_path,
            [f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.0961\t7.2286\t"],
            [f"{resort.uuid}\t{VERBIER_VILLAGE}\tBASE\tyes\tVerbier\tVerbier village"],
        )

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        location = Location.objects.get(uuid=VERBIER_VILLAGE)
        assert location.name == "Verbier village"
        assert location.kind == Location.KIND.VILLAGE
        assert location.latitude == pytest.approx(46.0961)
        link = ResortLocation.objects.get(resort=resort, location=location)
        assert link.role == ResortLocation.ROLE.BASE
        assert link.is_primary is True

    def test_one_location_is_shared_by_four_resorts(self, tmp_path: Path) -> None:
        """Four resorts reference one Mont Fort row, not four copies of 3330.

        The case the two-sheet format exists for — a single locations row
        with four links, rather than the resort sheet's four repetitions of
        the same scalar.
        """
        resorts = [
            ResortFactory.create(name=name)
            for name in ("Verbier", "Nendaz", "Veysonnaz", "Thyon")
        ]
        sheets = write_sheets(
            tmp_path,
            [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"],
            [
                f"{resort.uuid}\t{MONT_FORT}\tTOP\t\t{resort.name}\tMont Fort"
                for resort in resorts
            ],
        )

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        assert Location.objects.filter(name="Mont Fort").count() == 1
        assert Location.objects.get(uuid=MONT_FORT).resort_locations.count() == 4

    def test_second_run_reports_no_changes(self, tmp_path: Path) -> None:
        """Idempotent — re-importing the same sheets writes nothing."""
        resort = ResortFactory.create()
        sheets = write_sheets(
            tmp_path,
            [f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.0961\t7.2286\t"],
            [f"{resort.uuid}\t{VERBIER_VILLAGE}\tBASE\tyes\tR\tV"],
        )
        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        out = StringIO()
        call_command("import_locations", "--commit", stdout=out, **sheets)

        assert "No changes" in out.getvalue()
        assert Location.objects.count() == 1
        assert ResortLocation.objects.count() == 1

    def test_updates_a_changed_curated_field(self, tmp_path: Path) -> None:
        """A renamed or re-pinned location is updated in place."""
        location = LocationFactory.create(
            uuid=MONT_FORT, name="Mt Fort", kind=Location.KIND.PEAK
        )
        sheets = write_sheets(
            tmp_path, [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"], []
        )

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        location.refresh_from_db()
        assert location.name == "Mont Fort"

    def test_update_never_touches_the_resolved_fields(self, tmp_path: Path) -> None:
        """elevation_m and forecast_cell survive a re-import.

        The sheet does not own them — link_location_forecast_cells does —
        so an import must not blank the work an Open-Meteo call paid for.
        """
        location = LocationFactory.create(
            uuid=MONT_FORT, name="Mt Fort", kind=Location.KIND.PEAK, resolved=True
        )
        assert location.forecast_cell is not None
        cell_pk = location.forecast_cell.pk
        sheets = write_sheets(
            tmp_path, [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"], []
        )

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        location.refresh_from_db()
        assert location.elevation_m == 1500.0
        assert location.forecast_cell is not None
        assert location.forecast_cell.pk == cell_pk

    def test_deletes_a_curated_location_the_sheet_dropped(self, tmp_path: Path) -> None:
        """A curated row removed from the sheet is deleted, links first.

        Links must go before the location or PROTECT refuses the delete —
        this is the ordering assertion for _apply.
        """
        link = ResortLocationFactory.create(
            location=LocationFactory.create(name="Retired")
        )
        sheets = write_sheets(tmp_path, [], [])

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        assert not Location.objects.filter(pk=link.location.pk).exists()
        assert not ResortLocation.objects.filter(pk=link.pk).exists()

    def test_delete_never_touches_an_anonymous_location(self, tmp_path: Path) -> None:
        """A location minted from user data is not the sheet's to delete.

        SNOW-704 and SNOW-709 mint a nameless location per favourite and per
        observation. Sweeping those up because they are absent from a
        curation sheet would destroy user data.
        """
        minted = LocationFactory.create(anonymous=True)
        sheets = write_sheets(tmp_path, [], [])

        call_command("import_locations", "--commit", stdout=StringIO(), **sheets)

        assert Location.objects.filter(pk=minted.pk).exists()

    def test_mode_update_creates_and_deletes_nothing(self, tmp_path: Path) -> None:
        """--mode update refreshes existing rows only."""
        LocationFactory.create(uuid=MONT_FORT, name="Mt Fort")
        stale = LocationFactory.create(name="Retired")
        sheets = write_sheets(
            tmp_path,
            [
                f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t",
                f"{VERBIER_VILLAGE}\tVerbier village\tVILLAGE\t46.0961\t7.2286\t",
            ],
            [],
        )

        call_command(
            "import_locations",
            "--commit",
            "--mode",
            "update",
            stdout=StringIO(),
            **sheets,
        )

        assert Location.objects.get(uuid=MONT_FORT).name == "Mont Fort"
        assert not Location.objects.filter(uuid=VERBIER_VILLAGE).exists()
        assert Location.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db
class TestImportLocationsDryRun:
    """Without --commit the command reports and writes nothing."""

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        """The default invocation is read-only."""
        resort = ResortFactory.create()
        sheets = write_sheets(
            tmp_path,
            [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"],
            [f"{resort.uuid}\t{MONT_FORT}\tTOP\t\tR\tMont Fort"],
        )

        out = StringIO()
        call_command("import_locations", stdout=out, **sheets)

        assert "Dry-run" in out.getvalue()
        assert not Location.objects.exists()
        assert not ResortLocation.objects.exists()

    def test_dry_run_reports_the_same_plan(self, tmp_path: Path) -> None:
        """The diff names what would be added, at verbosity 2."""
        resort = ResortFactory.create()
        sheets = write_sheets(
            tmp_path,
            [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"],
            [f"{resort.uuid}\t{MONT_FORT}\tTOP\t\tR\tMont Fort"],
        )

        out = StringIO()
        call_command("import_locations", verbosity=2, stdout=out, **sheets)

        body = out.getvalue()
        assert "1 location(s) to add" in body
        assert "1 link(s) to add" in body
        assert "+ Mont Fort" in body


@pytest.mark.django_db
class TestImportLocationsErrors:
    """A partially applicable sheet fails the whole run."""

    @pytest.mark.parametrize(
        ("row", "fragment"),
        [
            (
                f"{MONT_FORT}\tMont Fort\tSUMMIT\t46.1036\t7.2989\t",
                "unrecognised kind",
            ),
            (f"{MONT_FORT}\tMont Fort\tPEAK\t\t7.2989\t", "no latitude"),
            (
                f"{MONT_FORT}\tMont Fort\tPEAK\tnorth\t7.2989\t",
                "non-numeric latitude",
            ),
            (f"{MONT_FORT}\tMont Fort\tPEAK\t946.1\t7.2989\t", "out of range"),
        ],
    )
    def test_bad_location_row_aborts_and_writes_nothing(
        self, tmp_path: Path, row: str, fragment: str
    ) -> None:
        """A malformed location row fails the run rather than being skipped."""
        sheets = write_sheets(tmp_path, [row], [])

        with pytest.raises(CommandError):
            call_command(
                "import_locations",
                "--commit",
                stdout=StringIO(),
                stderr=StringIO(),
                **sheets,
            )
        assert not Location.objects.exists()

    def test_unknown_resort_in_the_links_sheet_aborts(self, tmp_path: Path) -> None:
        """A link naming a resort that does not exist fails the run."""
        sheets = write_sheets(
            tmp_path,
            [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"],
            [f"{MONT_FORT}\t{MONT_FORT}\tTOP\t\tGhost\tMont Fort"],
        )

        with pytest.raises(CommandError):
            call_command(
                "import_locations",
                "--commit",
                stdout=StringIO(),
                stderr=StringIO(),
                **sheets,
            )
        assert not Location.objects.exists()

    def test_bad_role_aborts(self, tmp_path: Path) -> None:
        """A link with an unrecognised role fails the run."""
        resort = ResortFactory.create()
        sheets = write_sheets(
            tmp_path,
            [f"{MONT_FORT}\tMont Fort\tPEAK\t46.1036\t7.2989\t"],
            [f"{resort.uuid}\t{MONT_FORT}\tSUMMIT\t\tR\tMont Fort"],
        )

        with pytest.raises(CommandError):
            call_command(
                "import_locations",
                "--commit",
                stdout=StringIO(),
                stderr=StringIO(),
                **sheets,
            )
        assert not Location.objects.exists()

    def test_missing_required_column_is_an_error(self, tmp_path: Path) -> None:
        """A sheet without a required column cannot be read at all."""
        sheet = tmp_path / "locations.tsv"
        sheet.write_text("uuid\tname\n", encoding="utf-8")
        links = tmp_path / "links.tsv"
        links.write_text(LINK_HEADER + "\n", encoding="utf-8")

        with pytest.raises(CommandError, match="missing required column"):
            call_command(
                "import_locations", file=sheet, links_file=links, stdout=StringIO()
            )

    def test_unknown_mode_is_an_error(self, tmp_path: Path) -> None:
        """--mode validates against the full list, not argparse's terse form."""
        sheets = write_sheets(tmp_path, [], [])
        with pytest.raises(CommandError, match="unknown mode"):
            call_command(
                "import_locations", "--mode", "purge", stdout=StringIO(), **sheets
            )


@pytest.mark.django_db
class TestCommittedSheets:
    """The sheets actually committed to the repo must import cleanly."""

    def test_repo_sheets_import_without_error(self) -> None:
        """A bare dry run against the real sheets exits 0.

        Cheap guard against a hand-edited sheet reaching main with a typo
        in a kind, a role, or a resort uuid — none of which any other test
        would catch, since every other test writes its own fixture.
        """
        for resort_uuid in (
            "f12cecf9-def8-43d3-b9df-80f93472f16f",
            "532151df-47a0-497b-b168-09f975f7d07d",
            "9cf90fc9-46b0-4ce3-bf4c-5dc9cabd2374",
            "4b0941b3-4293-4343-896a-03cd606bd744",
        ):
            ResortFactory.create(uuid=resort_uuid)

        out = StringIO()
        call_command("import_locations", verbosity=2, stdout=out)

        body = out.getvalue()
        assert "4 location(s) to add" in body
        assert "4 link(s) to add" in body
        assert not Location.objects.exists()
