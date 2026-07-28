"""
tests/regions/management/commands/test_import_resorts.py.

Covers ``import_resorts``: read-only by default, the three ``--mode``
operations in isolation and combination, deletion of rows the sheet does
not list (marked NOT_A_SKI_RESORT or absent altogether), the region/canton
requirement that blocks creation from the editorial export, and the
all-or-nothing behaviour on a malformed sheet.
"""

from __future__ import annotations

import uuid as uuid_module
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from regions.models import Resort
from tests.factories import MicroRegionFactory, ResortFactory

COLUMNS = [
    "uuid",
    "name",
    "name_alt",
    "region",
    "canton",
    "operator_name",
    "website",
    "top_elevation_m",
    "base_elevation_m",
    "num_runs",
    "num_lifts",
    "total_piste_km",
    "typical_season_open",
    "typical_season_close",
    "note",
]


def _sheet(
    tmp_path: Path,
    rows: list[dict[str, str]],
    columns: list[str] | None = None,
) -> str:
    """Write a TSV export holding ``rows`` and return its path as a string."""
    columns = columns or COLUMNS
    lines = ["\t".join(columns)]
    lines += ["\t".join(row.get(column, "") for column in columns) for row in rows]
    path = tmp_path / "resorts.tsv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.mark.django_db
class TestImportResortsUpdate:
    """``update`` mode — overwrite editorial fields on rows that exist."""

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """A bare invocation reports the diff and changes nothing."""
        resort = ResortFactory.create(name="Verbier", operator_name="")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "operator_name": "Téléverbier",
                    "num_lifts": "34",
                }
            ],
        )

        out = StringIO()
        call_command("import_resorts", "--file", sheet, stdout=out, verbosity=2)

        assert "0 resort(s) to add, 1 to update, 0 to delete." in out.getvalue()
        assert "operator_name: '' -> 'Téléverbier'" in out.getvalue()
        assert "Dry-run" in out.getvalue()
        resort.refresh_from_db()
        assert resort.operator_name == ""

    def test_commit_updates_editorial_fields(self, tmp_path: Path) -> None:
        """``--commit`` writes the sheet's values onto the matching resort."""
        resort = ResortFactory.create(name="Zermatt")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Zermatt",
                    "name_alt": "Matterhorn",
                    "operator_name": "Zermatt Bergbahnen AG",
                    "website": "https://www.matterhornparadise.ch",
                    "top_elevation_m": "3899",
                    "base_elevation_m": "1620",
                    "num_lifts": "51",
                    "total_piste_km": "322",
                    "typical_season_open": "11-01",
                    "typical_season_close": "04-30",
                    "note": "Combined Zermatt-Cervinia domain",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", verbosity=0)

        resort.refresh_from_db()
        assert resort.name_alt == "Matterhorn"
        assert resort.operator_name == "Zermatt Bergbahnen AG"
        assert resort.top_elevation_m == 3899
        assert resort.total_piste_km == 322.0
        assert resort.typical_season_open == "11-01"
        assert resort.notes == "Combined Zermatt-Cervinia domain"

    def test_blank_cell_clears_the_field(self, tmp_path: Path) -> None:
        """A blank numeric cell nulls the field — the sheet is authoritative."""
        resort = ResortFactory.create(name="Braunwald", num_lifts=10)
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Braunwald", "note": "closed"}],
        )

        call_command("import_resorts", "--file", sheet, "--commit", verbosity=0)

        resort.refresh_from_db()
        assert resort.num_lifts is None

    def test_region_and_canton_are_not_overwritten(self, tmp_path: Path) -> None:
        """The DB owns region/canton; a sheet value never overrides them."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        other = MicroRegionFactory.create(region_id="CH-9999")
        resort = ResortFactory.create(name="Verbier", region=region, canton="VS")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "region": other.region_id,
                    "canton": "GR",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", verbosity=0)

        resort.refresh_from_db()
        assert resort.region == region
        assert resort.canton == "VS"

    def test_update_mode_alone_neither_adds_nor_deletes(self, tmp_path: Path) -> None:
        """``--mode update`` touches only rows that already exist."""
        existing = ResortFactory.create(name="Verbier", operator_name="")
        unlisted = ResortFactory.create(name="Unlisted")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(existing.uuid),
                    "name": "Verbier",
                    "operator_name": "Téléverbier",
                },
                {"uuid": str(uuid_module.uuid4()), "name": "Brand new"},
            ],
        )

        call_command(
            "import_resorts",
            "--file",
            sheet,
            "--mode",
            "update",
            "--commit",
            verbosity=0,
        )

        existing.refresh_from_db()
        assert existing.operator_name == "Téléverbier"
        assert Resort.objects.filter(pk=unlisted.pk).exists()
        assert Resort.objects.count() == 2


@pytest.mark.django_db
class TestImportResortsAdd:
    """``add`` mode — create resorts the sheet lists but the DB lacks."""

    def test_creates_row_with_region_and_canton(self, tmp_path: Path) -> None:
        """A sheet row carrying region + canton becomes a new resort."""
        MicroRegionFactory.create(region_id="CH-4115")
        new_uuid = str(uuid_module.uuid4())
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": new_uuid,
                    "name": "Brand new resort",
                    "region": "CH-4115",
                    "canton": "VS",
                    "num_lifts": "4",
                }
            ],
        )

        call_command(
            "import_resorts", "--file", sheet, "--mode", "add", "--commit", verbosity=0
        )

        resort = Resort.objects.get(uuid=new_uuid)
        assert resort.name == "Brand new resort"
        assert resort.canton == "VS"
        assert resort.num_lifts == 4

    def test_row_without_region_is_an_error(self, tmp_path: Path) -> None:
        """The editorial export carries no region/canton, so it cannot create."""
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(uuid_module.uuid4()), "name": "Brand new resort"}],
        )

        with pytest.raises(CommandError, match="1 sheet row"):
            call_command("import_resorts", "--file", sheet, "--mode", "add", "--commit")

        assert Resort.objects.count() == 0

    def test_unknown_region_is_an_error(self, tmp_path: Path) -> None:
        """A region_id with no MicroRegion is reported, not silently skipped."""
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(uuid_module.uuid4()),
                    "name": "Brand new resort",
                    "region": "CH-0000",
                    "canton": "VS",
                }
            ],
        )

        err = StringIO()
        with pytest.raises(CommandError, match="1 sheet row"):
            call_command(
                "import_resorts",
                "--file",
                sheet,
                "--mode",
                "add",
                "--commit",
                stderr=err,
            )

        assert "'CH-0000' does not exist" in err.getvalue()

    def test_marked_row_is_never_added(self, tmp_path: Path) -> None:
        """A NOT_A_SKI_RESORT row is retired, so add mode ignores it."""
        MicroRegionFactory.create(region_id="CH-4115")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(uuid_module.uuid4()),
                    "name": "Aigle",
                    "region": "CH-4115",
                    "canton": "VD",
                    "note": "NOT_A_SKI_RESORT - valley town",
                }
            ],
        )

        call_command(
            "import_resorts", "--file", sheet, "--mode", "add", "--commit", verbosity=0
        )

        assert Resort.objects.count() == 0


@pytest.mark.django_db
class TestImportResortsDelete:
    """``delete`` mode — remove resorts the sheet does not list."""

    def test_deletes_marked_rows(self, tmp_path: Path) -> None:
        """A row whose note starts with the delete marker is removed."""
        kept = ResortFactory.create(name="Verbier")
        retired = ResortFactory.create(name="Aigle")
        sheet = _sheet(
            tmp_path,
            [
                {"uuid": str(kept.uuid), "name": "Verbier"},
                {
                    "uuid": str(retired.uuid),
                    "name": "Aigle",
                    "note": "NOT_A_SKI_RESORT - valley town, no lifts",
                },
            ],
        )

        out = StringIO()
        call_command("import_resorts", "--file", sheet, "--commit", stdout=out)

        assert "deleted 1" in out.getvalue()
        assert list(Resort.objects.values_list("name", flat=True)) == ["Verbier"]

    def test_deletes_rows_absent_from_the_sheet(self, tmp_path: Path) -> None:
        """A resort the sheet does not mention at all is also removed."""
        kept = ResortFactory.create(name="Verbier")
        ResortFactory.create(name="Never exported")
        sheet = _sheet(tmp_path, [{"uuid": str(kept.uuid), "name": "Verbier"}])

        call_command(
            "import_resorts",
            "--file",
            sheet,
            "--mode",
            "delete",
            "--commit",
            verbosity=0,
        )

        assert list(Resort.objects.values_list("name", flat=True)) == ["Verbier"]

    def test_omitting_delete_mode_keeps_everything(self, tmp_path: Path) -> None:
        """Without ``delete``, an unlisted resort survives."""
        kept = ResortFactory.create(name="Verbier")
        unlisted = ResortFactory.create(name="Never exported")
        sheet = _sheet(tmp_path, [{"uuid": str(kept.uuid), "name": "Verbier"}])

        call_command(
            "import_resorts",
            "--file",
            sheet,
            "--mode",
            "add",
            "update",
            "--commit",
            verbosity=0,
        )

        assert Resort.objects.filter(pk=unlisted.pk).exists()


@pytest.mark.django_db
class TestImportResortsBehaviour:
    """Cross-cutting behaviour: idempotency, mode parsing, failure handling."""

    def test_rerun_is_a_noop(self, tmp_path: Path) -> None:
        """A second full run reports no changes — deletions stay deleted."""
        kept = ResortFactory.create(name="Verbier", operator_name="")
        retired = ResortFactory.create(name="Aigle")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(kept.uuid),
                    "name": "Verbier",
                    "operator_name": "Téléverbier",
                },
                {
                    "uuid": str(retired.uuid),
                    "name": "Aigle",
                    "note": "NOT_A_SKI_RESORT - valley town",
                },
            ],
        )
        call_command("import_resorts", "--file", sheet, "--commit", verbosity=0)

        out = StringIO()
        call_command("import_resorts", "--file", sheet, "--commit", stdout=out)

        assert "No changes" in out.getvalue()
        assert Resort.objects.count() == 1

    def test_mode_is_case_insensitive(self, tmp_path: Path) -> None:
        """``--mode UPDATE`` resolves like ``update``."""
        resort = ResortFactory.create(name="Verbier", operator_name="")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "operator_name": "Téléverbier",
                }
            ],
        )

        call_command(
            "import_resorts",
            "--file",
            sheet,
            "--mode",
            "UPDATE",
            "--commit",
            verbosity=0,
        )

        resort.refresh_from_db()
        assert resort.operator_name == "Téléverbier"

    def test_unknown_mode_raises(self, tmp_path: Path) -> None:
        """An unrecognised mode names the available ones."""
        sheet = _sheet(tmp_path, [])

        with pytest.raises(CommandError, match="unknown mode"):
            call_command("import_resorts", "--file", sheet, "--mode", "upsert")

    def test_invalid_value_is_an_error_and_writes_nothing(self, tmp_path: Path) -> None:
        """A value the model rejects fails the whole run rather than half-applying."""
        good = ResortFactory.create(name="Verbier", operator_name="")
        bad = ResortFactory.create(name="Zermatt")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(good.uuid),
                    "name": "Verbier",
                    "operator_name": "Téléverbier",
                },
                # Season dates are validated as month-day.
                {
                    "uuid": str(bad.uuid),
                    "name": "Zermatt",
                    "typical_season_open": "99-99",
                },
            ],
        )

        with pytest.raises(CommandError, match="1 sheet row"):
            call_command("import_resorts", "--file", sheet, "--commit")

        good.refresh_from_db()
        assert good.operator_name == ""

    def test_non_numeric_cell_is_an_error(self, tmp_path: Path) -> None:
        """A non-numeric elevation is reported, not raised as a traceback."""
        resort = ResortFactory.create(name="Verbier")
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Verbier", "top_elevation_m": "high"}],
        )

        with pytest.raises(CommandError, match="1 sheet row"):
            call_command("import_resorts", "--file", sheet, "--commit")

    def test_export_without_optional_columns_still_loads(self, tmp_path: Path) -> None:
        """A narrow export missing optional columns is read, not rejected."""
        resort = ResortFactory.create(name="Verbier", operator_name="Old")
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Verbier", "note": "kept"}],
            columns=["uuid", "name", "note"],
        )

        call_command(
            "import_resorts",
            "--file",
            sheet,
            "--mode",
            "update",
            "--commit",
            verbosity=0,
        )

        resort.refresh_from_db()
        assert resort.operator_name == ""
        assert resort.notes == "kept"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """An unreadable sheet path raises CommandError."""
        with pytest.raises(CommandError, match="Failed to read"):
            call_command("import_resorts", "--file", str(tmp_path / "nope.tsv"))

    def test_missing_required_column_raises(self, tmp_path: Path) -> None:
        """A sheet lacking a required column raises rather than silently skipping."""
        path = tmp_path / "resorts.tsv"
        path.write_text("uuid\tname\nabc\tVerbier\n", encoding="utf-8")

        with pytest.raises(CommandError, match="missing required column"):
            call_command("import_resorts", "--file", str(path))


@pytest.mark.django_db
class TestImportResortsAgainstCommittedSheet:
    """The vendored sheet must stay in sync with the committed seed fixture."""

    def test_committed_sheet_is_a_noop_against_the_seed_fixture(self) -> None:
        """A freshly seeded DB is already in the sheet's steady state."""
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

        out = StringIO()
        call_command("import_resorts", stdout=out)

        # The fixture is dumped from a database the sheet has been applied
        # to, so a full reconciliation finds nothing to do.
        assert "No changes" in out.getvalue()
