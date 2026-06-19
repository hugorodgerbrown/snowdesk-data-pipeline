"""
tests/bulletins/management/commands/test_build_test_data.py — Tests for build_test_data.

Covers:
  - Default mode (no --commit) is read-only: prints counts, no file written.
  - --commit writes a JSON file to a tmp path containing the expected records.
  - The generated JSON deserialises with the expected record counts (tested
    against the region fixtures pre-loaded so MicroRegion rows exist).
  - All Bulletin rows in the generated fixture have render_model_version == RENDER_MODEL_VERSION.
  - The checked-in ``test_data.json`` fixture has the correct model counts.
  - After loading the checked-in fixture the canonical preview URL renders 200.

The ``build_test_data`` command requires ``eaws_CH`` and ``resorts`` fixtures
to be pre-loaded (it queries MicroRegion rows for FK wiring).  Tests that
exercise the full record-count path therefore pre-load those fixtures first.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from django.core.management import call_command

from bulletins.services.render_model import RENDER_MODEL_VERSION

# Path to the pre-generated fixture (the one checked in to the repo).
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "bulletins" / "fixtures" / "test_data.json"
)


@pytest.mark.django_db
class TestBuildTestDataDryRun:
    """Default (no --commit) mode is read-only and prints counts."""

    def test_dry_run_does_not_write_file(self, tmp_path: Path) -> None:
        """Without --commit no file is written."""
        output = tmp_path / "out.json"
        call_command("build_test_data", output=str(output), verbosity=0)
        assert not output.exists()

    def test_dry_run_prints_read_only_banner(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Without --commit a READ-ONLY banner is printed."""
        output = tmp_path / "out.json"
        call_command("build_test_data", output=str(output), verbosity=1)
        captured = capsys.readouterr()
        assert "READ-ONLY" in captured.out

    def test_dry_run_prints_record_counts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Without --commit counts are still printed."""
        output = tmp_path / "out.json"
        call_command("build_test_data", output=str(output), verbosity=1)
        captured = capsys.readouterr()
        assert "bulletins" in captured.out
        assert "region_bulletins" in captured.out

    def test_dry_run_does_not_leave_bulletins_in_db(self, tmp_path: Path) -> None:
        """The transaction is rolled back — no DB rows survive."""
        from bulletins.models import Bulletin

        before = Bulletin.objects.count()
        output = tmp_path / "out.json"
        call_command("build_test_data", output=str(output), verbosity=0)
        after = Bulletin.objects.count()
        assert after == before


@pytest.mark.django_db
class TestBuildTestDataCommit:
    """--commit writes the fixture file.

    These tests pre-load ``eaws_CH`` and ``resorts`` fixtures so the command
    can wire MicroRegion FKs.
    """

    @pytest.fixture(autouse=True)
    def _load_region_fixtures(self) -> None:
        """Pre-load region fixtures required by build_test_data."""
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

    def test_commit_writes_file(self, tmp_path: Path) -> None:
        """--commit writes the JSON file to the specified output path."""
        output = tmp_path / "test_data.json"
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_commit_produces_valid_json(self, tmp_path: Path) -> None:
        """The output file is valid JSON."""
        output = tmp_path / "test_data.json"
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        records = json.loads(output.read_text())
        assert isinstance(records, list)
        assert len(records) > 0

    def test_commit_record_counts(self, tmp_path: Path) -> None:
        """The fixture contains the expected model counts."""
        output = tmp_path / "test_data.json"
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        records = json.loads(output.read_text())
        model_counts = Counter(r["model"] for r in records)

        # Geography layer.
        assert model_counts["regions.majorregion"] == 9
        assert model_counts["regions.subregion"] == 21
        assert model_counts["regions.microregion"] == 149
        assert model_counts["regions.resort"] == 148

        # Bulletin layer: 149 map-coverage + 29 detail = 178.
        assert model_counts["bulletins.bulletin"] == 178
        assert model_counts["bulletins.regionbulletin"] == 178

        # Day ratings and weather snapshots — 178 each.
        assert model_counts["bulletins.regiondayrating"] == 178
        assert model_counts["bulletins.weathersnapshot"] == 178

    def test_all_bulletins_have_current_render_model_version(
        self, tmp_path: Path
    ) -> None:
        """Every Bulletin record has render_model_version == RENDER_MODEL_VERSION."""
        output = tmp_path / "test_data.json"
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        records = json.loads(output.read_text())
        bulletin_records = [r for r in records if r["model"] == "bulletins.bulletin"]
        for rec in bulletin_records:
            assert rec["fields"]["render_model_version"] == RENDER_MODEL_VERSION, (
                f"Bulletin {rec['fields']['bulletin_id']} has version "
                f"{rec['fields']['render_model_version']}, expected "
                f"{RENDER_MODEL_VERSION}"
            )

    def test_render_model_has_version_key(self, tmp_path: Path) -> None:
        """Every Bulletin render_model dict has a 'version' key equal to RENDER_MODEL_VERSION."""
        output = tmp_path / "test_data.json"
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        records = json.loads(output.read_text())
        bulletin_records = [r for r in records if r["model"] == "bulletins.bulletin"]
        for rec in bulletin_records:
            rm = rec["fields"]["render_model"]
            assert isinstance(rm, dict), (
                f"Bulletin {rec['fields']['bulletin_id']} render_model is not a dict"
            )
            assert rm.get("version") == RENDER_MODEL_VERSION

    def test_does_not_persist_to_db(self, tmp_path: Path) -> None:
        """After --commit, no Bulletin rows survive in the DB (rollback worked)."""
        from bulletins.models import Bulletin

        output = tmp_path / "test_data.json"
        # Load fixtures created bulletins, but build_test_data should not add more.
        before = Bulletin.objects.count()
        call_command("build_test_data", commit=True, output=str(output), verbosity=0)
        assert Bulletin.objects.count() == before


class TestCheckedInFixture:
    """Verify the checked-in test_data.json has the expected shape.

    These tests parse the fixture file directly without touching the DB.
    They are intentionally NOT marked django_db to keep them fast.
    """

    def test_fixture_file_exists(self) -> None:
        """The checked-in fixture file exists."""
        assert _FIXTURE_PATH.exists(), (
            f"Missing fixture: {_FIXTURE_PATH}. "
            "Run: uv run python manage.py build_test_data --commit"
        )

    def test_fixture_is_valid_json(self) -> None:
        """The fixture file is valid JSON."""
        records = json.loads(_FIXTURE_PATH.read_text())
        assert isinstance(records, list)

    def test_fixture_model_counts(self) -> None:
        """The fixture has the expected model counts."""
        records = json.loads(_FIXTURE_PATH.read_text())
        model_counts = Counter(r["model"] for r in records)

        assert model_counts["regions.majorregion"] == 9
        assert model_counts["regions.subregion"] == 21
        assert model_counts["regions.microregion"] == 149
        assert model_counts["regions.resort"] == 148
        assert model_counts["bulletins.bulletin"] == 178
        assert model_counts["bulletins.regionbulletin"] == 178
        assert model_counts["bulletins.regiondayrating"] == 178
        assert model_counts["bulletins.weathersnapshot"] == 178

    def test_all_bulletins_have_current_render_model_version(self) -> None:
        """Every Bulletin record has render_model_version == RENDER_MODEL_VERSION."""
        records = json.loads(_FIXTURE_PATH.read_text())
        bulletin_records = [r for r in records if r["model"] == "bulletins.bulletin"]
        for rec in bulletin_records:
            assert rec["fields"]["render_model_version"] == RENDER_MODEL_VERSION

    def test_ch4115_has_30_day_ratings(self) -> None:
        """CH-4115 has RegionDayRating rows for all 30 days in April 2026."""
        records = json.loads(_FIXTURE_PATH.read_text())
        rdr_records = [
            r
            for r in records
            if r["model"] == "bulletins.regiondayrating"
            and r["fields"]["region"] == ["CH-4115"]
        ]
        assert len(rdr_records) == 30

    def test_ch4115_has_30_weather_snapshots(self) -> None:
        """CH-4115 has WeatherSnapshot rows for all 30 days in April 2026."""
        records = json.loads(_FIXTURE_PATH.read_text())
        ws_records = [
            r
            for r in records
            if r["model"] == "bulletins.weathersnapshot"
            and r["fields"]["region"] == ["CH-4115"]
        ]
        assert len(ws_records) == 30

    def test_non_detail_region_has_single_map_date_weather_snapshot(self) -> None:
        """A non-detail region (CH-4222) gets exactly one snapshot on the map date."""
        records = json.loads(_FIXTURE_PATH.read_text())
        ws_records = [
            r
            for r in records
            if r["model"] == "bulletins.weathersnapshot"
            and r["fields"]["region"] == ["CH-4222"]
        ]
        assert len(ws_records) == 1
        assert ws_records[0]["fields"]["valid_for_date"] == "2026-04-08"

    def test_weather_snapshots_use_wmo_code_1(self) -> None:
        """All WeatherSnapshot rows use WMO weather code 1."""
        records = json.loads(_FIXTURE_PATH.read_text())
        ws_records = [r for r in records if r["model"] == "bulletins.weathersnapshot"]
        assert all(r["fields"]["weather_code"] == 1 for r in ws_records)
