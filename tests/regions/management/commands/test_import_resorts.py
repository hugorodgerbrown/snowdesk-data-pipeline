"""
tests/regions/management/commands/test_import_resorts.py.

Covers ``import_resorts``: read-only by default, the three ``--mode``
operations in isolation and combination, deletion of rows the sheet does
not list (marked NOT_A_SKI_RESORT or absent altogether), the region/canton
values a row needs to be created at all, the creation-time-only
``latitude``/``longitude`` pair, and the all-or-nothing behaviour on a
malformed sheet.
"""

from __future__ import annotations

import uuid as uuid_module
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.regions.models import Resort
from tests.factories import MicroRegionFactory, ResortFactory

COLUMNS = [
    "uuid",
    "name",
    "name_alt",
    "region",
    "canton",
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
    "kind",
    "tier",
    "latitude",
    "longitude",
    "geocode_source",
    "geocode_confidence",
    "needs_review",
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
                    "notes": "Combined Zermatt-Cervinia domain",
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
            [{"uuid": str(resort.uuid), "name": "Braunwald", "notes": "closed"}],
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
        """A row missing region/canton is reported rather than guessed at."""
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
                    "status": "NOT_A_SKI_RESORT",
                    "notes": "valley town",
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
        """A row whose status column holds the delete marker is removed."""
        kept = ResortFactory.create(name="Verbier")
        retired = ResortFactory.create(name="Aigle")
        sheet = _sheet(
            tmp_path,
            [
                {"uuid": str(kept.uuid), "name": "Verbier"},
                {
                    "uuid": str(retired.uuid),
                    "name": "Aigle",
                    "status": "NOT_A_SKI_RESORT",
                    "notes": "valley town, no lifts",
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
                    "status": "NOT_A_SKI_RESORT",
                    "notes": "valley town",
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
            [{"uuid": str(resort.uuid), "name": "Verbier", "notes": "kept"}],
            columns=["uuid", "name", "status", "notes"],
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
        call_command("loaddata", "eaws_CH", verbosity=0)
        call_command("import_resorts", commit=True, verbosity=0)

        out = StringIO()
        call_command("import_resorts", stdout=out)

        # The fixture is dumped from a database the sheet has been applied
        # to, so a full reconciliation finds nothing to do.
        assert "No changes" in out.getvalue()


@pytest.mark.django_db
class TestImportResortsCoordinates:
    """The ``latitude``/``longitude`` columns, read only on create (SNOW-544)."""

    def test_creation_stores_the_pin_and_flags_it_for_review(
        self,
        tmp_path: Path,
    ) -> None:
        """A sheet-supplied pin is stamped ``import``, never ``manual``."""
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
                    "latitude": "46.68",
                    "longitude": "8.777",
                }
            ],
        )

        call_command(
            "import_resorts", "--file", sheet, "--mode", "add", "--commit", verbosity=0
        )

        resort = Resort.objects.get(uuid=new_uuid)
        assert (resort.latitude, resort.longitude) == (46.68, 8.777)
        # ``MANUAL`` would claim an operator placed this pin on a map.
        assert resort.geocode_source == Resort.GeocodeSource.IMPORT
        assert resort.geocode_confidence is None
        assert resort.needs_review is True
        assert resort.geocoded_at is not None

    def test_creation_without_coordinates_still_works(self, tmp_path: Path) -> None:
        """The pair stays optional — a row without one creates an unpinned row."""
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
                }
            ],
        )

        call_command(
            "import_resorts", "--file", sheet, "--mode", "add", "--commit", verbosity=0
        )

        resort = Resort.objects.get(uuid=new_uuid)
        assert resort.latitude is None
        assert resort.longitude is None
        assert resort.geocode_source == ""
        assert resort.needs_review is False

    def test_an_existing_pin_is_never_overwritten(self, tmp_path: Path) -> None:
        """The regression this carve-out exists to prevent (SNOW-544).

        Coordinates are read on create only. Were they read on update too,
        every re-run would drag a resort that had been re-pinned in the map
        editor back to whatever the sheet said, silently undoing the
        curation the panel exists to capture.
        """
        resort = ResortFactory.create(
            name="Verbier",
            latitude=46.0955,
            longitude=7.2203,
            geocode_source=Resort.GeocodeSource.MANUAL,
            geocode_confidence=1.0,
            needs_review=False,
        )
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "latitude": "47.0",
                    "longitude": "9.0",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", verbosity=0)

        resort.refresh_from_db()
        assert (resort.latitude, resort.longitude) == (46.0955, 7.2203)
        assert resort.geocode_source == Resort.GeocodeSource.MANUAL
        assert resort.geocode_confidence == 1.0
        assert resort.needs_review is False

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [("46.68", ""), ("", "8.777")],
        ids=["longitude-missing", "latitude-missing"],
    )
    def test_half_a_pair_is_an_error_and_writes_nothing(
        self,
        tmp_path: Path,
        latitude: str,
        longitude: str,
    ) -> None:
        """One axis is not a location — it would save as no location at all."""
        MicroRegionFactory.create(region_id="CH-4115")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(uuid_module.uuid4()),
                    "name": "Brand new resort",
                    "region": "CH-4115",
                    "canton": "VS",
                    "latitude": latitude,
                    "longitude": longitude,
                }
            ],
        )

        with pytest.raises(CommandError, match="1 sheet row"):
            call_command("import_resorts", "--file", sheet, "--mode", "add", "--commit")

        assert Resort.objects.count() == 0

    def test_non_numeric_coordinate_is_an_error(self, tmp_path: Path) -> None:
        """A junk coordinate cell fails the row rather than saving a null pin."""
        MicroRegionFactory.create(region_id="CH-4115")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(uuid_module.uuid4()),
                    "name": "Brand new resort",
                    "region": "CH-4115",
                    "canton": "VS",
                    "latitude": "not-a-number",
                    "longitude": "8.777",
                }
            ],
        )

        with pytest.raises(CommandError, match="1 sheet row"):
            call_command("import_resorts", "--file", sheet, "--mode", "add", "--commit")

        assert Resort.objects.count() == 0

    def test_export_without_the_columns_still_loads(self, tmp_path: Path) -> None:
        """An export predating the columns imports unchanged."""
        MicroRegionFactory.create(region_id="CH-4115")
        new_uuid = str(uuid_module.uuid4())
        columns = [
            column for column in COLUMNS if column not in {"latitude", "longitude"}
        ]
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": new_uuid,
                    "name": "Brand new resort",
                    "region": "CH-4115",
                    "canton": "VS",
                }
            ],
            columns=columns,
        )

        call_command(
            "import_resorts", "--file", sheet, "--mode", "add", "--commit", verbosity=0
        )

        assert Resort.objects.get(uuid=new_uuid).latitude is None


@pytest.mark.django_db
class TestImportResortsKind:
    """The ``kind`` column and what it changes about deletion (SNOW-544)."""

    def test_kind_round_trips_from_the_sheet(self, tmp_path: Path) -> None:
        """A TOURING_TERRAIN cell reaches the model."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(name="Grimsel", region=region)
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Grimsel", "kind": "TOURING_TERRAIN"}],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.kind == Resort.Kind.TOURING_TERRAIN

    def test_blank_kind_means_resort(self, tmp_path: Path) -> None:
        """The column is optional — a sheet that omits it still imports."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(
            name="Verbier", region=region, kind=Resort.Kind.TOURING_TERRAIN
        )
        sheet = _sheet(tmp_path, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.kind == Resort.Kind.RESORT

    def test_unknown_kind_is_an_error_and_writes_nothing(self, tmp_path: Path) -> None:
        """A typo must not silently resolve to RESORT.

        Falling back would put lift-less terrain back on the map as a
        resort pin — the exact failure the column exists to prevent.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        resort = ResortFactory.create(name="Grimsel", region=region)
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Grimsel", "kind": "TOURING"}],
        )

        with pytest.raises(CommandError):
            call_command(
                "import_resorts", "--file", sheet, "--commit", "--mode", "update"
            )

        resort.refresh_from_db()
        assert resort.kind == Resort.Kind.RESORT

    def test_touring_terrain_survives_a_delete_run(self, tmp_path: Path) -> None:
        """The core regression this ticket introduces.

        A TOURING_TERRAIN row is a live row: it is listed in the sheet and
        carries no NOT_A_SKI_RESORT marker, so ``--mode delete`` must
        leave it alone. Before ``kind`` existed, the only way to express
        "not a resort" was the marker, which deletes.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        touring = ResortFactory.create(
            name="Grimsel", region=region, kind=Resort.Kind.TOURING_TERRAIN
        )
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(touring.uuid), "name": "Grimsel", "kind": "TOURING_TERRAIN"}],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "delete")

        assert Resort.objects.filter(pk=touring.pk).exists()

    def test_marked_row_is_still_deleted_whatever_its_kind(
        self, tmp_path: Path
    ) -> None:
        """``kind`` does not weaken the marker — the two are independent."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        marked = ResortFactory.create(name="Aigle", region=region)
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(marked.uuid),
                    "name": "Aigle",
                    "kind": "TOURING_TERRAIN",
                    "status": "NOT_A_SKI_RESORT",
                    "notes": "valley town",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "delete")

        assert not Resort.objects.filter(pk=marked.pk).exists()


@pytest.mark.django_db
class TestImportResortsWhyItMatters:
    """The ``why_it_matters`` column (SNOW-542).

    It is an ordinary editorial text column, so it inherits the sheet's
    authoritative-blank behaviour: clearing the cell clears the field. That
    matters more here than for the numeric columns, because the field fills
    up over time and a curator retracting a line must be able to.
    """

    def test_round_trips_from_the_sheet(self, tmp_path: Path) -> None:
        """A curated line reaches the model."""
        resort = ResortFactory.create(name="Tschiertschen")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Tschiertschen",
                    "why_it_matters": (
                        "Nationally known freeride destination. Four lifts, "
                        "disproportionate avalanche relevance."
                    ),
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.why_it_matters.startswith("Nationally known freeride")

    def test_blank_cell_clears_the_line(self, tmp_path: Path) -> None:
        """A blank cell retracts a line rather than leaving the old one."""
        resort = ResortFactory.create(name="Verbier", why_it_matters="Old copy.")
        sheet = _sheet(tmp_path, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.why_it_matters == ""

    def test_over_length_line_is_an_error_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """255 chars is the register: a paragraph no popup can render is rejected."""
        resort = ResortFactory.create(name="Verbier", why_it_matters="Short.")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "why_it_matters": "x" * 256,
                }
            ],
        )

        with pytest.raises(CommandError):
            call_command(
                "import_resorts", "--file", sheet, "--commit", "--mode", "update"
            )

        resort.refresh_from_db()
        assert resort.why_it_matters == "Short."


@pytest.mark.django_db
class TestImportResortsTier:
    """The ``tier`` column (SNOW-543).

    Tier is stored and curated rather than derived: the review's finding is
    that scale is the wrong axis, and no formula promotes a small high area
    like Avers or Bivio above a large low resort. The sheet is therefore
    the source of truth, and the importer's job is to carry it faithfully.
    """

    def test_round_trips_from_the_sheet(self, tmp_path: Path) -> None:
        """A CORE cell reaches the model."""
        resort = ResortFactory.create(name="Zermatt")
        sheet = _sheet(
            tmp_path, [{"uuid": str(resort.uuid), "name": "Zermatt", "tier": "CORE"}]
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.tier == Resort.Tier.CORE

    def test_blank_tier_means_standard(self, tmp_path: Path) -> None:
        """The column is optional — an export predating it still imports."""
        resort = ResortFactory.create(name="Verbier", tier=Resort.Tier.CORE)
        sheet = _sheet(tmp_path, [{"uuid": str(resort.uuid), "name": "Verbier"}])

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.tier == Resort.Tier.STANDARD

    def test_unknown_tier_is_an_error_and_writes_nothing(self, tmp_path: Path) -> None:
        """A typo must not silently demote a Core resort to an ordinary pin."""
        resort = ResortFactory.create(name="Zermatt", tier=Resort.Tier.CORE)
        sheet = _sheet(
            tmp_path, [{"uuid": str(resort.uuid), "name": "Zermatt", "tier": "MAJOR"}]
        )

        with pytest.raises(CommandError):
            call_command(
                "import_resorts", "--file", sheet, "--commit", "--mode", "update"
            )

        resort.refresh_from_db()
        assert resort.tier == Resort.Tier.CORE

    def test_tier_is_case_insensitive(self, tmp_path: Path) -> None:
        """Sheet casing is the curator's business, not the importer's."""
        resort = ResortFactory.create(name="Avers")
        sheet = _sheet(
            tmp_path, [{"uuid": str(resort.uuid), "name": "Avers", "tier": "core"}]
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.tier == Resort.Tier.CORE


@pytest.mark.django_db
class TestImportResortsStatus:
    """The ``status`` column — the retirement marker's own home (SNOW-817).

    It used to be a prefix on the ``note`` column, so one cell carried both
    a curator's prose and a machine-read verdict, and no note could begin
    with those characters without deleting the resort.
    """

    def test_blank_status_means_live(self, tmp_path: Path) -> None:
        """The overwhelming majority of rows say nothing and are kept."""
        resort = ResortFactory.create(name="Verbier")
        sheet = _sheet(
            tmp_path, [{"uuid": str(resort.uuid), "name": "Verbier", "status": ""}]
        )

        call_command("import_resorts", "--file", sheet, "--commit")

        assert Resort.objects.filter(pk=resort.pk).exists()

    def test_status_is_case_insensitive(self, tmp_path: Path) -> None:
        """Sheet casing is the curator's business, not the importer's."""
        resort = ResortFactory.create(name="Brig")
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Brig", "status": "not_a_ski_resort"}],
        )

        call_command("import_resorts", "--file", sheet, "--commit")

        assert not Resort.objects.filter(pk=resort.pk).exists()

    def test_unknown_status_is_an_error_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """A typo must resolve to neither "live" nor "delete this resort"."""
        resort = ResortFactory.create(name="Verbier")
        sheet = _sheet(
            tmp_path,
            [{"uuid": str(resort.uuid), "name": "Verbier", "status": "RETIRED"}],
        )

        with pytest.raises(CommandError):
            call_command("import_resorts", "--file", sheet, "--commit")

        assert Resort.objects.filter(pk=resort.pk).exists()

    def test_notes_no_longer_carry_the_marker(self, tmp_path: Path) -> None:
        """A note may now say anything, including the marker's own words."""
        resort = ResortFactory.create(name="Verbier")
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "status": "",
                    "notes": "NOT_A_SKI_RESORT was considered and rejected",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.notes == "NOT_A_SKI_RESORT was considered and rejected"


@pytest.mark.django_db
class TestImportResortsProvenance:
    """Coordinate provenance carried by the sheet (SNOW-817).

    The sheet is a mirror of a real database now, so it can say that a pin
    was placed by an operator in the map editor and reviewed. Without these
    columns every import demoted 77 reviewed pins to IMPORT/needs_review.
    """

    def _row(self, **extra: str) -> dict[str, str]:
        """Build a creatable sheet row carrying a coordinate."""
        MicroRegionFactory.create(region_id="CH-4115")
        row = {
            "uuid": "33333333-3333-4333-8333-333333333333",
            "name": "Verbier",
            "region": "CH-4115",
            "canton": "VS",
            "latitude": "46.0956",
            "longitude": "7.2203",
        }
        row.update(extra)
        return row

    def test_sheet_provenance_is_carried_through(self, tmp_path: Path) -> None:
        """A MANUAL, reviewed pin stays MANUAL and reviewed."""
        sheet = _sheet(
            tmp_path,
            [
                self._row(
                    geocode_source="MANUAL",
                    geocode_confidence="1.0",
                    needs_review="false",
                )
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "add")

        resort = Resort.objects.get(name="Verbier")
        assert resort.geocode_source == Resort.GeocodeSource.MANUAL
        assert resort.geocode_confidence == 1.0
        assert resort.needs_review is False

    def test_silent_sheet_falls_back_to_import_and_review(self, tmp_path: Path) -> None:
        """A hand-typed coordinate is still flagged, as it always was."""
        sheet = _sheet(tmp_path, [self._row()])

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "add")

        resort = Resort.objects.get(name="Verbier")
        assert resort.geocode_source == Resort.GeocodeSource.IMPORT
        assert resort.needs_review is True

    def test_unknown_source_is_an_error_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """A typo must not land as a blank provenance nobody notices."""
        sheet = _sheet(tmp_path, [self._row(geocode_source="EYEBALLED")])

        with pytest.raises(CommandError):
            call_command("import_resorts", "--file", sheet, "--commit", "--mode", "add")

        assert not Resort.objects.filter(name="Verbier").exists()

    def test_non_numeric_confidence_is_an_error(self, tmp_path: Path) -> None:
        """A confidence cell that does not parse stops the import."""
        sheet = _sheet(
            tmp_path,
            [self._row(geocode_source="MANUAL", geocode_confidence="high")],
        )

        with pytest.raises(CommandError):
            call_command("import_resorts", "--file", sheet, "--commit", "--mode", "add")

        assert not Resort.objects.filter(name="Verbier").exists()

    def test_provenance_is_never_written_to_an_existing_row(
        self, tmp_path: Path
    ) -> None:
        """The database owns the pin — and its provenance — after creation."""
        resort = ResortFactory.create(
            name="Verbier",
            geocode_source=Resort.GeocodeSource.MANUAL,
            needs_review=False,
        )
        sheet = _sheet(
            tmp_path,
            [
                {
                    "uuid": str(resort.uuid),
                    "name": "Verbier",
                    "geocode_source": "IMPORT",
                    "needs_review": "true",
                }
            ],
        )

        call_command("import_resorts", "--file", sheet, "--commit", "--mode", "update")

        resort.refresh_from_db()
        assert resort.geocode_source == Resort.GeocodeSource.MANUAL
        assert resort.needs_review is False
