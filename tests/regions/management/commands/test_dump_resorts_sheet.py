"""
tests/regions/management/commands/test_dump_resorts_sheet.py — dump_resorts_sheet tests.

The command is the DB → sheet half of the resort round trip (SNOW-817).
Three properties matter, and each has a test here:

1. **The round trip is a fixpoint.** Importing the committed sheet and
   dumping it again reproduces the file byte for byte. Without that, every
   dump churns the file and a real coordinate change is lost in the noise.
2. **Retired rows survive.** A resort retired in the database (SNOW-1404)
   is dumped with its status, date and note; a retired sheet row the
   database does not hold comes from the sheet already on disk, unchanged.
3. **Row order is stable.** Existing rows keep their position, so a re-pinned
   coordinate is a one-line diff.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.regions.management.commands.dump_resorts_sheet import (
    SHEET_COLUMNS,
    render_resorts_sheet,
    write_resorts_sheet,
)
from apps.regions.models import Resort
from tests.factories import MicroRegionFactory, PassFactory, ResortFactory

COMMITTED_SHEET = Path("apps/regions/data/resorts.tsv")


def _rows(text: str) -> list[dict[str, str]]:
    """Parse sheet text into a list of row dicts."""
    return list(csv.DictReader(text.splitlines(), delimiter="\t"))


def _write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    """Write ``rows`` to ``path`` using the canonical column order."""
    fieldnames: list[str] = list(SHEET_COLUMNS)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.django_db(transaction=True)
class TestRoundTrip:
    """The committed sheet must survive import → dump unchanged."""

    def test_import_then_dump_reproduces_the_committed_sheet(self) -> None:
        """Sheet → DB → sheet is a fixpoint on the real committed data.

        This is the guard that makes the sheet safe to treat as the single
        source: if a column were dropped, mis-rendered or reordered by the
        dumper, this test fails with the exact diff.
        """
        call_command("loaddata", "eaws_CH", verbosity=0)
        call_command("import_resorts", commit=True, verbosity=0)

        assert render_resorts_sheet(COMMITTED_SHEET) == COMMITTED_SHEET.read_text(
            encoding="utf-8"
        )


@pytest.mark.django_db
class TestRetiredRows:
    """Retired rows: dumped from the database, or carried off the sheet."""

    def test_retired_db_row_is_dumped_with_its_state(self, tmp_path: Path) -> None:
        """A retired resort is written with its reason, date and note."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(
            name="Aigle",
            region=region,
            retired_at=dt.datetime(2025, 4, 30, tzinfo=dt.UTC),
            retired_reason="CLOSED",
            retired_note="lifts shut",
        )
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [{"uuid": str(resort.uuid), "name": "Aigle"}])

        row = _rows(render_resorts_sheet(sheet))[0]

        assert (row["status"], row["retired_at"], row["retired_note"]) == (
            "CLOSED",
            "2025-04-30T00:00:00+00:00",
            "lifts shut",
        )

    def test_retired_round_trip_is_a_fixpoint(self, tmp_path: Path) -> None:
        """Dump a retired resort, import the dump: nothing changes."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(
            name="Aigle", region=region, retired=True, retired_note="shut"
        )
        sheet = tmp_path / "resorts.tsv"
        write_resorts_sheet(sheet)
        before = Resort.all_objects.get(pk=resort.pk)

        call_command("import_resorts", "--file", str(sheet), "--commit", verbosity=0)

        after = Resort.all_objects.get(pk=resort.pk)
        assert (after.retired_at, after.retired_reason, after.retired_note) == (
            before.retired_at,
            before.retired_reason,
            before.retired_note,
        )
        assert render_resorts_sheet(sheet) == sheet.read_text(encoding="utf-8")

    def test_retired_row_is_carried_forward(self, tmp_path: Path) -> None:
        """A NOT_A_SKI_RESORT row survives a dump that cannot see it."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(name="Verbier", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(
            sheet,
            [
                {"uuid": str(resort.uuid), "name": "Verbier", "status": ""},
                {
                    "uuid": "11111111-1111-4111-8111-111111111111",
                    "name": "Brig",
                    "status": "NOT_A_SKI_RESORT",
                    "notes": "Rhône valley town, no lifts",
                },
            ],
        )

        rows = _rows(render_resorts_sheet(sheet))

        retired = [row for row in rows if row["status"] == "NOT_A_SKI_RESORT"]
        assert len(retired) == 1
        assert retired[0]["name"] == "Brig"
        assert retired[0]["notes"] == "Rhône valley town, no lifts"

    def test_live_row_with_no_db_row_is_dropped(self, tmp_path: Path) -> None:
        """A resort somebody deleted leaves the sheet, rather than lingering."""
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(
            sheet,
            [
                {
                    "uuid": "22222222-2222-4222-8222-222222222222",
                    "name": "Deleted",
                    "status": "",
                }
            ],
        )

        assert _rows(render_resorts_sheet(sheet)) == []


@pytest.mark.django_db
class TestPassesColumn:
    """A resort's passes are written as sorted, comma-joined slugs (SNOW-1083)."""

    def test_passes_render_as_slugs(self, tmp_path: Path) -> None:
        """Several passes are sorted; a resort on none gets a blank cell."""
        leysin = ResortFactory.create(name="Leysin")
        leysin.passes.add(
            PassFactory.create(slug="magic-pass"), PassFactory.create(slug="ikon")
        )
        ResortFactory.create(name="Zermatt")

        rows = {row["name"]: row for row in _rows(render_resorts_sheet(tmp_path / "x"))}

        assert rows["Leysin"]["passes"] == "ikon,magic-pass"
        assert rows["Zermatt"]["passes"] == ""


@pytest.mark.django_db
class TestOrdering:
    """Diff stability — an edit should not reorder the file."""

    def test_existing_rows_keep_their_position(self, tmp_path: Path) -> None:
        """Output follows the on-disk sheet's order, not the database's."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        # Deliberately reverse-alphabetical, so a name-ordered dump would
        # visibly reorder them.
        zermatt = ResortFactory.create(name="Zermatt", region=region)
        arosa = ResortFactory.create(name="Arosa", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(
            sheet,
            [
                {"uuid": str(zermatt.uuid), "name": "Zermatt", "status": ""},
                {"uuid": str(arosa.uuid), "name": "Arosa", "status": ""},
            ],
        )

        names = [row["name"] for row in _rows(render_resorts_sheet(sheet))]
        assert names == ["Zermatt", "Arosa"]

    def test_new_rows_are_appended_in_name_order(self, tmp_path: Path) -> None:
        """A resort the sheet has never seen lands at the end."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        known = ResortFactory.create(name="Zermatt", region=region)
        ResortFactory.create(name="Arosa", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [{"uuid": str(known.uuid), "name": "Zermatt"}])

        names = [row["name"] for row in _rows(render_resorts_sheet(sheet))]
        assert names == ["Zermatt", "Arosa"]

    def test_missing_sheet_writes_every_db_row(self, tmp_path: Path) -> None:
        """A first dump into an empty directory is not an error."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        ResortFactory.create(name="Verbier", region=region)

        rows = _rows(render_resorts_sheet(tmp_path / "absent.tsv"))

        assert [row["name"] for row in rows] == ["Verbier"]


@pytest.mark.django_db
class TestCommandBehaviour:
    """Safe-by-default: read-only without ``--commit``."""

    def test_dry_run_writes_nothing(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """A bare invocation reports the diff and leaves the file alone."""
        import apps.regions.management.commands.dump_resorts_sheet as mod

        region = MicroRegionFactory.create(region_id="CH-4115")
        ResortFactory.create(name="Verbier", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [])
        before = sheet.read_text(encoding="utf-8")
        monkeypatch.setattr(mod, "DEFAULT_SHEET_PATH", sheet)

        call_command("dump_resorts_sheet", verbosity=0)

        assert sheet.read_text(encoding="utf-8") == before

    def test_commit_writes_the_sheet(self, tmp_path: Path) -> None:
        """``write_resorts_sheet`` persists the rendered text."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        ResortFactory.create(name="Verbier", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [])

        write_resorts_sheet(sheet)

        assert [row["name"] for row in _rows(sheet.read_text(encoding="utf-8"))] == [
            "Verbier"
        ]


@pytest.mark.django_db
class TestRenderedValues:
    """Cell rendering that ``import_resorts`` has to be able to read back."""

    def test_integral_floats_lose_their_trailing_zero(self, tmp_path: Path) -> None:
        """210.0 km of piste is written as ``210``, not ``210.0``.

        Rendering every integral float with a ``.0`` would have churned 118
        of the sheet's 186 lines on the first dump, for no change in meaning.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(
            name="Verbier", region=region, total_piste_km=210.0
        )
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        assert _rows(render_resorts_sheet(sheet))[0]["total_piste_km"] == "210"

    def test_fractional_floats_round_trip_exactly(self, tmp_path: Path) -> None:
        """A coordinate written here parses back to the same float."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(
            name="Verbier",
            region=region,
            latitude=46.095584091367755,
            longitude=7.220341699392549,
        )
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        row = _rows(render_resorts_sheet(sheet))[0]
        assert float(row["latitude"]) == 46.095584091367755
        assert float(row["longitude"]) == 7.220341699392549

    def test_a_live_db_row_has_a_blank_status(self, tmp_path: Path) -> None:
        """A live resort is written with ``status`` and its pair blank."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(name="Verbier", region=region)
        sheet = tmp_path / "resorts.tsv"
        _write_sheet(sheet, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        row = _rows(render_resorts_sheet(sheet))[0]
        assert (row["status"], row["retired_at"], row["retired_note"]) == ("", "", "")
