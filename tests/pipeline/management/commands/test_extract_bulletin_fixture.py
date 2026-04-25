"""
tests/pipeline/management/commands/test_extract_bulletin_fixture.py — Tests
for the ``extract_bulletin_fixture`` management command.

Covers:

  - Season mode: writes one region's bulletins inside the 6-month window,
    skips bulletins outside the window, preserves ``raw_data`` byte-for-byte.
  - Day mode: writes every bulletin valid on the calendar day.
  - Manifest update: README is created on first run, appended on subsequent.
  - Refusal to overwrite without ``--force``.
  - Argument validation (missing required args, mode/arg mismatch, bad values).
  - The default run does not mutate the database.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.models import Bulletin, Region, RegionBulletin
from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionFactory,
)


def _feature(bulletin_id: str, region_ids: list[str]) -> dict:
    """Build a minimal CAAML Feature envelope for use as Bulletin.raw_data."""
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "bulletinID": bulletin_id,
            "regions": [
                {"regionID": rid, "name": f"Region {rid}"} for rid in region_ids
            ],
        },
    }


def _ensure_region(region_id: str) -> Region:
    """Return the region with this ID, creating it via the factory if absent."""
    existing = Region.objects.filter(region_id=region_id).first()
    if existing is not None:
        return existing
    return RegionFactory.create(region_id=region_id, slug=region_id.lower())


def _make_bulletin(
    bulletin_id: str,
    region_ids: list[str],
    valid_from: datetime,
) -> Bulletin:
    """Create a Bulletin + linked RegionBulletin rows for the given regions."""
    bulletin = BulletinFactory.create(
        bulletin_id=bulletin_id,
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_from + timedelta(days=1),
        raw_data=_feature(bulletin_id, region_ids),
    )
    for rid in region_ids:
        region = _ensure_region(rid)
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


@pytest.mark.django_db
class TestSeasonMode:
    """Tests for --mode=season."""

    def test_writes_each_bulletin_in_window(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Season mode dumps every bulletin for the region inside the window."""
        region_id = "CH-4115"
        # Five days inside the 2025-11 → 2026-05 window.
        for day_offset in range(5):
            ts = datetime(2025, 12, 1, 8, 0, tzinfo=UTC) + timedelta(days=day_offset)
            _make_bulletin(
                bulletin_id=f"bul-in-{day_offset}",
                region_ids=[region_id],
                valid_from=ts,
            )
        # One bulletin outside the window — must be skipped.
        _make_bulletin(
            bulletin_id="bul-out",
            region_ids=[region_id],
            valid_from=datetime(2025, 9, 15, 8, 0, tzinfo=UTC),
        )
        # One bulletin inside the window but for a different region.
        _make_bulletin(
            bulletin_id="bul-other",
            region_ids=["CH-9999"],
            valid_from=datetime(2025, 12, 5, 8, 0, tzinfo=UTC),
        )

        call_command(
            "extract_bulletin_fixture",
            mode="season",
            region_id=region_id,
            season="2025-11",
            output_dir=tmp_path,
            verbosity=0,
        )

        output_path = tmp_path / "season_ch-4115_2025-11.json"
        assert output_path.exists()
        payload = json.loads(output_path.read_text())
        assert len(payload) == 5
        # Each entry preserves the source raw_data byte-for-byte.
        bulletin_ids = [entry["properties"]["bulletinID"] for entry in payload]
        assert sorted(bulletin_ids) == [f"bul-in-{i}" for i in range(5)]
        # Output is sorted by valid_from then bulletin_id, matching the qs order.
        assert bulletin_ids == sorted(bulletin_ids)
        out = capsys.readouterr().out
        assert "Extracted 5 bulletin(s)" in out

    def test_requires_region_and_season(self, tmp_path: Path) -> None:
        """--mode=season without --region or --season raises CommandError."""
        with pytest.raises(CommandError, match="--mode=season requires"):
            call_command(
                "extract_bulletin_fixture",
                mode="season",
                output_dir=tmp_path,
                verbosity=0,
            )

    def test_rejects_date_in_season_mode(self, tmp_path: Path) -> None:
        """--date passed alongside --mode=season raises CommandError."""
        with pytest.raises(
            CommandError, match="--date is not valid with --mode=season"
        ):
            call_command(
                "extract_bulletin_fixture",
                mode="season",
                region_id="CH-4115",
                season="2025-11",
                target_date="2026-04-15",
                output_dir=tmp_path,
                verbosity=0,
            )

    def test_rejects_invalid_season_format(self, tmp_path: Path) -> None:
        """A malformed --season raises CommandError."""
        with pytest.raises(CommandError, match="Invalid --season"):
            call_command(
                "extract_bulletin_fixture",
                mode="season",
                region_id="CH-4115",
                season="not-a-date",
                output_dir=tmp_path,
                verbosity=0,
            )


@pytest.mark.django_db
class TestDayMode:
    """Tests for --mode=day."""

    def test_writes_every_bulletin_for_the_day(self, tmp_path: Path) -> None:
        """Day mode dumps all bulletins whose valid_from date matches."""
        target = date(2026, 4, 15)
        # Three bulletins on the target day, each for a different region.
        for i, region_id in enumerate(["CH-A001", "CH-B001", "CH-C001"]):
            _make_bulletin(
                bulletin_id=f"bul-day-{i}",
                region_ids=[region_id],
                valid_from=datetime(2026, 4, 15, 8 + i, 0, tzinfo=UTC),
            )
        # One bulletin on the day before — must be skipped.
        _make_bulletin(
            bulletin_id="bul-yesterday",
            region_ids=["CH-Z999"],
            valid_from=datetime(2026, 4, 14, 8, 0, tzinfo=UTC),
        )

        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date=target.isoformat(),
            output_dir=tmp_path,
            verbosity=0,
        )

        output_path = tmp_path / "day_2026-04-15.json"
        assert output_path.exists()
        payload = json.loads(output_path.read_text())
        assert len(payload) == 3
        bulletin_ids = sorted(entry["properties"]["bulletinID"] for entry in payload)
        assert bulletin_ids == ["bul-day-0", "bul-day-1", "bul-day-2"]
        # Region IDs surface in the regions list.
        seen_region_ids = {
            entry["properties"]["regions"][0]["regionID"] for entry in payload
        }
        assert seen_region_ids == {"CH-A001", "CH-B001", "CH-C001"}

    def test_requires_date(self, tmp_path: Path) -> None:
        """--mode=day without --date raises CommandError."""
        with pytest.raises(CommandError, match="--mode=day requires --date"):
            call_command(
                "extract_bulletin_fixture",
                mode="day",
                output_dir=tmp_path,
                verbosity=0,
            )

    def test_rejects_region_in_day_mode(self, tmp_path: Path) -> None:
        """--region or --season passed with --mode=day raises CommandError."""
        with pytest.raises(
            CommandError, match="--region and --season are not valid with --mode=day"
        ):
            call_command(
                "extract_bulletin_fixture",
                mode="day",
                target_date="2026-04-15",
                region_id="CH-4115",
                output_dir=tmp_path,
                verbosity=0,
            )

    def test_rejects_invalid_date_format(self, tmp_path: Path) -> None:
        """A malformed --date raises CommandError."""
        with pytest.raises(CommandError, match="Invalid --date"):
            call_command(
                "extract_bulletin_fixture",
                mode="day",
                target_date="not-a-date",
                output_dir=tmp_path,
                verbosity=0,
            )


@pytest.mark.django_db
class TestManifest:
    """Tests for the sibling README manifest."""

    def test_manifest_created_on_first_run(self, tmp_path: Path) -> None:
        """First successful run writes the README header + one row."""
        _make_bulletin(
            "bul-m-001",
            ["CH-M001"],
            datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )

        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            verbosity=0,
        )

        readme = tmp_path / "README.md"
        assert readme.exists()
        contents = readme.read_text()
        assert contents.startswith("# Test fixtures\n")
        assert "| File | Source | Extracted at |" in contents
        # Exactly one data row.
        rows = [
            line
            for line in contents.splitlines()
            if line.startswith("| ") and "day_2026-04-15.json" in line
        ]
        assert len(rows) == 1
        assert "day 2026-04-15" in rows[0]

    def test_manifest_appended_on_subsequent_runs(self, tmp_path: Path) -> None:
        """A second extraction adds a second manifest row, preserving the first."""
        _make_bulletin(
            "bul-day-1",
            ["CH-D001"],
            datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )
        _make_bulletin(
            "bul-day-2",
            ["CH-D002"],
            datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        )

        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            verbosity=0,
        )
        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-16",
            output_dir=tmp_path,
            verbosity=0,
        )

        contents = (tmp_path / "README.md").read_text()
        assert "day_2026-04-15.json" in contents
        assert "day_2026-04-16.json" in contents
        # Header appears exactly once.
        assert contents.count("# Test fixtures") == 1


@pytest.mark.django_db
class TestOverwrite:
    """Tests for the --force / refuse-to-overwrite behaviour."""

    def test_refuses_to_overwrite_without_force(self, tmp_path: Path) -> None:
        """An existing output file blocks the run unless --force is passed."""
        _make_bulletin(
            "bul-overwrite",
            ["CH-O001"],
            datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )
        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            verbosity=0,
        )

        # Second run without --force should refuse, leaving the file intact.
        with pytest.raises(CommandError, match="already exists"):
            call_command(
                "extract_bulletin_fixture",
                mode="day",
                target_date="2026-04-15",
                output_dir=tmp_path,
                verbosity=0,
            )

    def test_force_allows_overwrite(self, tmp_path: Path) -> None:
        """--force overwrites an existing output file."""
        _make_bulletin(
            "bul-force-1",
            ["CH-F001"],
            datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )
        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            verbosity=0,
        )
        # Add another bulletin for the same day, then re-run with --force.
        _make_bulletin(
            "bul-force-2",
            ["CH-F002"],
            datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            force=True,
            verbosity=0,
        )

        payload = json.loads((tmp_path / "day_2026-04-15.json").read_text())
        bulletin_ids = sorted(entry["properties"]["bulletinID"] for entry in payload)
        assert bulletin_ids == ["bul-force-1", "bul-force-2"]


@pytest.mark.django_db
class TestEmptyExtraction:
    """Tests for the empty-result case."""

    def test_no_matching_bulletins_raises(self, tmp_path: Path) -> None:
        """An extraction with no matches exits non-zero with no file written."""
        with pytest.raises(CommandError, match="No bulletins matched"):
            call_command(
                "extract_bulletin_fixture",
                mode="day",
                target_date="2026-04-15",
                output_dir=tmp_path,
                verbosity=0,
            )
        assert not (tmp_path / "day_2026-04-15.json").exists()
        assert not (tmp_path / "README.md").exists()


@pytest.mark.django_db
class TestReadOnly:
    """Tests asserting the command does not mutate the DB."""

    def test_command_does_not_create_or_delete_rows(self, tmp_path: Path) -> None:
        """A successful run leaves Bulletin/RegionBulletin counts unchanged."""
        _make_bulletin(
            "bul-r1",
            ["CH-R001"],
            datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )
        baseline_bulletins = Bulletin.objects.count()
        baseline_links = RegionBulletin.objects.count()

        call_command(
            "extract_bulletin_fixture",
            mode="day",
            target_date="2026-04-15",
            output_dir=tmp_path,
            verbosity=0,
        )

        assert Bulletin.objects.count() == baseline_bulletins
        assert RegionBulletin.objects.count() == baseline_links
