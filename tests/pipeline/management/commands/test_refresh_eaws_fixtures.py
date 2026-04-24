"""
tests/pipeline/management/commands/test_refresh_eaws_fixtures.py

Covers the ``refresh_eaws_fixtures`` command:
  - Read-only by default (no --commit → no file writes).
  - --commit writes updated geometry.
  - Idempotent: re-running after a --commit produces no further changes.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

FIXTURES_DIR = Path("pipeline/fixtures")
MAJOR_FIXTURE = FIXTURES_DIR / "eaws_major_regions.json"
SUB_FIXTURE = FIXTURES_DIR / "eaws_sub_regions.json"


class TestRefreshEawsFixtures:
    """Tests for the refresh_eaws_fixtures management command."""

    def test_dry_run_does_not_modify_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit, the command prints a diff but writes nothing."""
        tmp_regions = _seed_tmp_regions_fixture(tmp_path)
        tmp_major = _seed_tmp_fixture(
            tmp_path / "eaws_major_regions.json",
            [_major_entry("CH-1", centre=None, bbox=None)],
        )
        tmp_sub = _seed_tmp_fixture(
            tmp_path / "eaws_sub_regions.json",
            [_sub_entry("CH-11", major="CH-1", centre=None, bbox=None)],
        )
        _patch_fixture_paths(monkeypatch, tmp_regions, tmp_major, tmp_sub)

        major_before = tmp_major.read_text()
        sub_before = tmp_sub.read_text()

        out = StringIO()
        call_command("refresh_eaws_fixtures", stdout=out)

        # File contents unchanged.
        assert tmp_major.read_text() == major_before
        assert tmp_sub.read_text() == sub_before
        # Output flags dry-run.
        assert "Dry-run" in out.getvalue()

    def test_commit_writes_geometry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit updates centre and bbox on L1 and L2 fixtures."""
        tmp_regions = _seed_tmp_regions_fixture(tmp_path)
        tmp_major = _seed_tmp_fixture(
            tmp_path / "eaws_major_regions.json",
            [_major_entry("CH-1", centre=None, bbox=None)],
        )
        tmp_sub = _seed_tmp_fixture(
            tmp_path / "eaws_sub_regions.json",
            [_sub_entry("CH-11", major="CH-1", centre=None, bbox=None)],
        )
        _patch_fixture_paths(monkeypatch, tmp_regions, tmp_major, tmp_sub)

        call_command("refresh_eaws_fixtures", "--commit", stdout=StringIO())

        major = json.loads(tmp_major.read_text())
        sub = json.loads(tmp_sub.read_text())
        assert major[0]["fields"]["centre"] is not None
        assert major[0]["fields"]["bbox"] is not None
        assert sub[0]["fields"]["centre"] is not None
        assert sub[0]["fields"]["bbox"] is not None

    def test_second_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit after the first produces no further changes."""
        tmp_regions = _seed_tmp_regions_fixture(tmp_path)
        tmp_major = _seed_tmp_fixture(
            tmp_path / "eaws_major_regions.json",
            [_major_entry("CH-1", centre=None, bbox=None)],
        )
        tmp_sub = _seed_tmp_fixture(
            tmp_path / "eaws_sub_regions.json",
            [_sub_entry("CH-11", major="CH-1", centre=None, bbox=None)],
        )
        _patch_fixture_paths(monkeypatch, tmp_regions, tmp_major, tmp_sub)

        call_command("refresh_eaws_fixtures", "--commit", stdout=StringIO())
        after_first = tmp_major.read_text(), tmp_sub.read_text()

        out = StringIO()
        call_command("refresh_eaws_fixtures", "--commit", stdout=out)
        after_second = tmp_major.read_text(), tmp_sub.read_text()

        assert after_first == after_second
        assert "0 change(s)" in out.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_tmp_regions_fixture(tmp_path: Path) -> Path:
    """Write a minimal two-L4-region fixture under ``tmp_path``."""
    path = tmp_path / "regions.json"
    path.write_text(
        json.dumps(
            [
                _region_entry(
                    "CH-1111",
                    centre={"lon": 6.94, "lat": 46.47},
                    boundary_poly=[
                        [
                            [6.8, 46.4],
                            [7.0, 46.4],
                            [7.0, 46.5],
                            [6.8, 46.5],
                            [6.8, 46.4],
                        ]
                    ],
                ),
                _region_entry(
                    "CH-1112",
                    centre={"lon": 7.14, "lat": 46.47},
                    boundary_poly=[
                        [
                            [7.0, 46.4],
                            [7.2, 46.4],
                            [7.2, 46.5],
                            [7.0, 46.5],
                            [7.0, 46.4],
                        ]
                    ],
                ),
            ]
        )
    )
    return path


def _seed_tmp_fixture(path: Path, entries: list[dict]) -> Path:
    """Write an arbitrary fixture payload to ``path``."""
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return path


def _region_entry(region_id: str, centre: dict, boundary_poly: list) -> dict:
    """Build a minimal pipeline.region fixture entry."""
    return {
        "model": "pipeline.region",
        "fields": {
            "region_id": region_id,
            "name": f"Test {region_id}",
            "slug": region_id.lower(),
            "subregion": [region_id[:5]],
            "centre": centre,
            "boundary": {"type": "Polygon", "coordinates": boundary_poly},
            "created_at": "2026-04-24T00:00:00Z",
            "updated_at": "2026-04-24T00:00:00Z",
        },
    }


def _major_entry(prefix: str, centre: dict | None, bbox: list | None) -> dict:
    """Build a minimal pipeline.eawsmajorregion fixture entry."""
    return {
        "model": "pipeline.eawsmajorregion",
        "fields": {
            "prefix": prefix,
            "country": "CH",
            "name_native": f"Test {prefix}",
            "name_en": f"Test {prefix}",
            "centre": centre,
            "bbox": bbox,
            "boundary": None,
            "created_at": "2026-04-24T00:00:00Z",
            "updated_at": "2026-04-24T00:00:00Z",
        },
    }


def _sub_entry(prefix: str, major: str, centre: dict | None, bbox: list | None) -> dict:
    """Build a minimal pipeline.eawssubregion fixture entry."""
    return {
        "model": "pipeline.eawssubregion",
        "fields": {
            "prefix": prefix,
            "major": [major],
            "name_native": f"Test {prefix}",
            "name_en": f"Test {prefix}",
            "centre": centre,
            "bbox": bbox,
            "boundary": None,
            "created_at": "2026-04-24T00:00:00Z",
            "updated_at": "2026-04-24T00:00:00Z",
        },
    }


def _patch_fixture_paths(
    monkeypatch: pytest.MonkeyPatch,
    regions: Path,
    major: Path,
    sub: Path,
) -> None:
    """Redirect the command's module-level fixture paths to tmp_path copies."""
    from pipeline.management.commands import refresh_eaws_fixtures as mod

    monkeypatch.setattr(mod, "_REGIONS_FIXTURE", regions)
    monkeypatch.setattr(mod, "_MAJOR_FIXTURE", major)
    monkeypatch.setattr(mod, "_SUB_FIXTURE", sub)
