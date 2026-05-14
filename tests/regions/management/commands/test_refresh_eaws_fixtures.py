"""
tests/regions/management/commands/test_refresh_eaws_fixtures.py

Covers the ``refresh_eaws_fixtures`` command:
  - Read-only by default (no --commit → no file writes).
  - --commit writes updated geometry (centre, bbox, boundary).
  - Idempotent: re-running after a --commit produces no further changes.
  - The SNOW-59 ``_boundary_from_children`` helper: adjacent L4 polygons
    collapse to one Polygon; disjoint ones produce a MultiPolygon;
    output is json-safe (lists, not tuples).
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

FIXTURES_DIR = Path("regions/fixtures")
EAWS_FIXTURE = FIXTURES_DIR / "eaws_ch.json"


class TestRefreshEawsFixtures:
    """Tests for the refresh_eaws_fixtures management command."""

    def test_dry_run_does_not_modify_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit, the command prints a diff but writes nothing."""
        tmp_eaws = _seed_tmp_eaws_fixture(tmp_path)
        _patch_fixture_paths(monkeypatch, tmp_eaws)

        eaws_before = tmp_eaws.read_text()

        out = StringIO()
        call_command("refresh_eaws_fixtures", stdout=out)

        # File contents unchanged.
        assert tmp_eaws.read_text() == eaws_before
        # Output flags dry-run.
        assert "Dry-run" in out.getvalue()

    def test_commit_writes_geometry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit updates centre, bbox and boundary on L1 and L2 entries."""
        tmp_eaws = _seed_tmp_eaws_fixture(tmp_path)
        _patch_fixture_paths(monkeypatch, tmp_eaws)

        call_command("refresh_eaws_fixtures", "--commit", stdout=StringIO())

        entries = json.loads(tmp_eaws.read_text())
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        subs = [e for e in entries if e["model"] == "regions.subregion"]
        assert majors[0]["fields"]["centre"] is not None
        assert majors[0]["fields"]["bbox"] is not None
        assert subs[0]["fields"]["centre"] is not None
        assert subs[0]["fields"]["bbox"] is not None
        # SNOW-59: boundary populated as a GeoJSON Polygon (the two L4
        # children share an edge, so unary_union collapses them to one
        # contiguous Polygon rather than a MultiPolygon).
        assert majors[0]["fields"]["boundary"]["type"] == "Polygon"
        assert subs[0]["fields"]["boundary"]["type"] == "Polygon"


class TestBoundaryFromChildren:
    """Direct tests for the SNOW-59 boundary-union helper."""

    def test_adjacent_polygons_collapse_to_single_polygon(self) -> None:
        """Two L4 children sharing an edge produce one Polygon, not a Multi."""
        from regions.management.commands.refresh_eaws_fixtures import (
            _boundary_from_children,
        )

        children = [
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [0.0, 0.0],
                            [1.0, 0.0],
                            [1.0, 1.0],
                            [0.0, 1.0],
                            [0.0, 0.0],
                        ]
                    ],
                }
            },
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [1.0, 0.0],
                            [2.0, 0.0],
                            [2.0, 1.0],
                            [1.0, 1.0],
                            [1.0, 0.0],
                        ]
                    ],
                }
            },
        ]
        boundary = _boundary_from_children(children)
        assert boundary["type"] == "Polygon"

    def test_disjoint_polygons_yield_multipolygon(self) -> None:
        """Two L4 children with no shared edge yield a MultiPolygon."""
        from regions.management.commands.refresh_eaws_fixtures import (
            _boundary_from_children,
        )

        children = [
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [0.0, 0.0],
                            [1.0, 0.0],
                            [1.0, 1.0],
                            [0.0, 1.0],
                            [0.0, 0.0],
                        ]
                    ],
                }
            },
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [5.0, 5.0],
                            [6.0, 5.0],
                            [6.0, 6.0],
                            [5.0, 6.0],
                            [5.0, 5.0],
                        ]
                    ],
                }
            },
        ]
        boundary = _boundary_from_children(children)
        assert boundary["type"] == "MultiPolygon"
        assert len(boundary["coordinates"]) == 2

    def test_returns_json_safe_lists(self) -> None:
        """Coordinates round-trip through json — no shapely tuple residue."""
        from regions.management.commands.refresh_eaws_fixtures import (
            _boundary_from_children,
        )

        children = [
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [0.0, 0.0],
                            [1.0, 0.0],
                            [1.0, 1.0],
                            [0.0, 1.0],
                            [0.0, 0.0],
                        ]
                    ],
                }
            }
        ]
        boundary = _boundary_from_children(children)
        # Every coord in the ring is a plain list, not a tuple — required
        # for fixture-diff idempotence (lists from JSON read-back must
        # equal what we just wrote).
        for ring in boundary["coordinates"]:
            for coord in ring:
                assert isinstance(coord, list)

    def test_second_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit after the first produces no further changes."""
        tmp_eaws = _seed_tmp_eaws_fixture(tmp_path)
        _patch_fixture_paths(monkeypatch, tmp_eaws)

        call_command("refresh_eaws_fixtures", "--commit", stdout=StringIO())
        after_first = tmp_eaws.read_text()

        out = StringIO()
        call_command("refresh_eaws_fixtures", "--commit", stdout=out)
        after_second = tmp_eaws.read_text()

        assert after_first == after_second
        assert "0 change(s)" in out.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_tmp_eaws_fixture(tmp_path: Path) -> Path:
    """Write a minimal three-section EAWS fixture under ``tmp_path``."""
    path = tmp_path / "eaws_ch.json"
    entries = [
        _major_entry("CH-1", centre=None, bbox=None),
        _sub_entry("CH-11", major="CH-1", centre=None, bbox=None),
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
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return path


def _seed_tmp_fixture(path: Path, entries: list[dict]) -> Path:
    """Write an arbitrary fixture payload to ``path``."""
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return path


def _region_entry(region_id: str, centre: dict, boundary_poly: list) -> dict:
    """Build a minimal regions.microregion fixture entry."""
    return {
        "model": "regions.microregion",
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
    """Build a minimal regions.majorregion fixture entry."""
    return {
        "model": "regions.majorregion",
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
    """Build a minimal regions.subregion fixture entry."""
    return {
        "model": "regions.subregion",
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
    eaws: Path,
) -> None:
    """Redirect the command's module-level fixture path to the tmp_path copy."""
    from regions.management.commands import refresh_eaws_fixtures as mod

    monkeypatch.setattr(mod, "_EAWS_FIXTURE", eaws)
