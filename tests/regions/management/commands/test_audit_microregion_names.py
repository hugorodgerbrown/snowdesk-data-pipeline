"""
tests/regions/management/commands/test_audit_microregion_names.py

Covers ``audit_microregion_names``:
  - Read-only by default: prints mismatch table, exits non-zero, leaves
    CSV untouched.
  - ``--commit``: patches the CSV (name column only), calls
    ``_rebuild_fixture``, prints next-step instructions, exits zero.
  - Regions with no RegionBulletin coverage are reported as "no data —
    skipped" and are never written to the CSV.
  - When all names match, exits zero and prints a success message.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from tests.factories import MicroRegionFactory, RegionBulletinFactory


def _write_minimal_csv(path: Path, rows: list[dict]) -> None:
    """Write a minimal CSV with the columns audit_microregion_names reads."""
    fieldnames = ["region_id", "region_name", "slug", "centre", "boundary"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _csv_rows(path: Path) -> list[dict]:
    """Return all rows from a CSV as a list of dicts."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    csv_path: Path,
    fixture_path: Path,
) -> None:
    """Redirect the command's module-level paths to tmp copies."""
    from regions.management.commands import audit_microregion_names as mod

    monkeypatch.setattr(mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(mod, "_FIXTURE_PATH", fixture_path)


@pytest.mark.django_db
class TestAuditMicroregionNamesDryRun:
    """Dry-run (no --commit) behaviour."""

    def test_prints_mismatch_and_exits_nonzero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mismatch is printed; command exits non-zero; CSV unchanged."""
        # Arrange: one region with a stale name.
        region = MicroRegionFactory.create(region_id="CH-1111", name="Old Name")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="New SLF Name",
        )

        csv_rows = [
            {
                "region_id": "CH-1111",
                "region_name": "Old Name",
                "slug": "ch-1111",
                "centre": '{"lon": 7.0, "lat": 46.5}',
                "boundary": '{"type":"Polygon","coordinates":[]}',
            }
        ]
        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(tmp_csv, csv_rows)
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        out = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            call_command("audit_microregion_names", stdout=out)

        assert exc_info.value.code != 0
        output = out.getvalue()
        assert "CH-1111" in output
        assert "Old Name" in output
        assert "New SLF Name" in output
        assert "Dry-run" in output

    def test_csv_unchanged_on_dry_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CSV is not modified when --commit is absent."""
        region = MicroRegionFactory.create(region_id="CH-1112", name="Stale")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="Current SLF",
        )

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-1112",
                    "region_name": "Stale",
                    "slug": "ch-1112",
                    "centre": '{"lon": 7.0, "lat": 46.5}',
                    "boundary": '{"type":"Polygon","coordinates":[]}',
                }
            ],
        )
        before = tmp_csv.read_text(encoding="utf-8")

        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        with pytest.raises(SystemExit):
            call_command("audit_microregion_names", stdout=StringIO())

        assert tmp_csv.read_text(encoding="utf-8") == before

    def test_no_data_region_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regions with no RegionBulletin coverage are listed as 'no data'."""
        # Region with no bulletin coverage at all.
        MicroRegionFactory.create(region_id="CH-1113", name="Uncovered Region")

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-1113",
                    "region_name": "Uncovered Region",
                    "slug": "ch-1113",
                    "centre": '{"lon": 7.0, "lat": 46.5}',
                    "boundary": '{"type":"Polygon","coordinates":[]}',
                }
            ],
        )
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        out = StringIO()
        # No mismatches → exits zero; but we still want the "no data" output.
        call_command("audit_microregion_names", stdout=out)

        output = out.getvalue()
        # Region appears in the "no data" list.
        assert "CH-1113" in output
        assert "skipped" in output.lower() or "no bulletin" in output.lower()

    def test_all_matching_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all names match, command exits zero and reports success."""
        region = MicroRegionFactory.create(region_id="CH-1114", name="Matching Name")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="Matching Name",
        )

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-1114",
                    "region_name": "Matching Name",
                    "slug": "ch-1114",
                    "centre": '{"lon": 7.0, "lat": 46.5}',
                    "boundary": '{"type":"Polygon","coordinates":[]}',
                }
            ],
        )
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        out = StringIO()
        call_command("audit_microregion_names", stdout=out)

        assert "up to date" in out.getvalue().lower()


@pytest.mark.django_db
class TestAuditMicroregionNamesCommit:
    """``--commit`` behaviour."""

    def test_commit_patches_csv_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CSV region_name is updated for the mismatched row."""
        region = MicroRegionFactory.create(region_id="CH-2111", name="Old Name")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="New SLF Name",
        )

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-2111",
                    "region_name": "Old Name",
                    "slug": "ch-2111",
                    "centre": '{"lon": 7.5, "lat": 46.8}',
                    "boundary": '{"type":"Polygon","coordinates":[[[7.0,46.0],[8.0,46.0],[8.0,47.0],[7.0,47.0],[7.0,46.0]]]}',
                }
            ],
        )

        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        # Stub _rebuild_fixture so we don't need scripts/ on sys.path.
        rebuild_calls: list[tuple] = []

        def fake_rebuild(csv_path: Path, fixture_path: Path) -> None:
            """Record the call and write a stub fixture."""
            rebuild_calls.append((csv_path, fixture_path))
            fixture_path.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_microregion_names as mod

        monkeypatch.setattr(mod, "_rebuild_fixture", fake_rebuild)
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        out = StringIO()
        call_command("audit_microregion_names", "--commit", stdout=out)

        # CSV name is updated.
        rows = _csv_rows(tmp_csv)
        assert len(rows) == 1
        assert rows[0]["region_name"] == "New SLF Name"
        # Other columns unchanged.
        assert rows[0]["slug"] == "ch-2111"

        # _rebuild_fixture was called once.
        assert len(rebuild_calls) == 1

    def test_commit_prints_next_step_instructions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Next-step instructions are printed after a successful commit."""
        region = MicroRegionFactory.create(region_id="CH-2112", name="Stale")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="Updated",
        )

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-2112",
                    "region_name": "Stale",
                    "slug": "ch-2112",
                    "centre": '{"lon": 7.5, "lat": 46.8}',
                    "boundary": '{"type":"Polygon","coordinates":[[[7.0,46.0],[8.0,46.0],[8.0,47.0],[7.0,47.0],[7.0,46.0]]]}',
                }
            ],
        )
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_microregion_names as mod

        monkeypatch.setattr(
            mod,
            "_rebuild_fixture",
            lambda csv_path, fixture_path: fixture_path.write_text(
                "[]", encoding="utf-8"
            ),
        )
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        out = StringIO()
        call_command("audit_microregion_names", "--commit", stdout=out)

        output = out.getvalue()
        assert "refresh_eaws_fixtures" in output
        assert "loaddata" in output

    def test_commit_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--commit does not sys.exit with non-zero code."""
        region = MicroRegionFactory.create(region_id="CH-2113", name="Old")
        RegionBulletinFactory.create(
            region=region,
            region_name_at_time="New",
        )

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-2113",
                    "region_name": "Old",
                    "slug": "ch-2113",
                    "centre": '{"lon": 7.5, "lat": 46.8}',
                    "boundary": '{"type":"Polygon","coordinates":[[[7.0,46.0],[8.0,46.0],[8.0,47.0],[7.0,47.0],[7.0,46.0]]]}',
                }
            ],
        )
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_microregion_names as mod

        monkeypatch.setattr(
            mod,
            "_rebuild_fixture",
            lambda csv_path, fixture_path: fixture_path.write_text(
                "[]", encoding="utf-8"
            ),
        )
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        # Should not raise SystemExit.
        call_command("audit_microregion_names", "--commit", stdout=StringIO())

    def test_no_data_region_not_written_to_csv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Region with no bulletin coverage is not modified in the CSV."""
        # A region with no bulletin coverage.
        MicroRegionFactory.create(region_id="CH-2114", name="Uncovered")
        # A region with a mismatch — ensures the commit path runs.
        region2 = MicroRegionFactory.create(region_id="CH-2115", name="Old")
        RegionBulletinFactory.create(region=region2, region_name_at_time="New")

        tmp_csv = tmp_path / "eaws_regions_ch.csv"
        _write_minimal_csv(
            tmp_csv,
            [
                {
                    "region_id": "CH-2114",
                    "region_name": "Uncovered",
                    "slug": "ch-2114",
                    "centre": '{"lon": 7.5, "lat": 46.8}',
                    "boundary": '{"type":"Polygon","coordinates":[[[7.0,46.0],[8.0,46.0],[8.0,47.0],[7.0,47.0],[7.0,46.0]]]}',
                },
                {
                    "region_id": "CH-2115",
                    "region_name": "Old",
                    "slug": "ch-2115",
                    "centre": '{"lon": 7.5, "lat": 46.8}',
                    "boundary": '{"type":"Polygon","coordinates":[[[8.0,46.0],[9.0,46.0],[9.0,47.0],[8.0,47.0],[8.0,46.0]]]}',
                },
            ],
        )
        tmp_fixture = tmp_path / "eaws.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_microregion_names as mod

        monkeypatch.setattr(
            mod,
            "_rebuild_fixture",
            lambda csv_path, fixture_path: fixture_path.write_text(
                "[]", encoding="utf-8"
            ),
        )
        _patch_paths(monkeypatch, tmp_csv, tmp_fixture)

        call_command("audit_microregion_names", "--commit", stdout=StringIO())

        rows = _csv_rows(tmp_csv)
        by_id = {r["region_id"]: r for r in rows}
        # Uncovered region name is unchanged.
        assert by_id["CH-2114"]["region_name"] == "Uncovered"
        # Mismatch region is patched.
        assert by_id["CH-2115"]["region_name"] == "New"
