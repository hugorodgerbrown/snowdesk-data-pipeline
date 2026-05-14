"""
tests/regions/management/commands/test_build_austria_fixture.py

Covers the ``build_austria_fixture`` command:
  - Dry-run (no --commit) writes nothing and exits 0.
  - --commit writes a fixture with the correct entry counts and shapes.
  - Idempotent: second --commit reports 0 change(s).
  - L1 entries carry country='AT'.
  - L2 prefix derivation: 3-segment ID → 1:1 synthetic L2; 4-segment → grouped.
  - L4 entries are sorted by region_id.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

# ---------------------------------------------------------------------------
# Synthetic source data helpers
# ---------------------------------------------------------------------------


def _make_geojson(features: list[dict]) -> dict:
    """Wrap a list of features in a GeoJSON FeatureCollection."""
    return {"type": "FeatureCollection", "features": features}


def _make_feature(region_id: str, coords: list) -> dict:
    """Build a minimal EAWS L4 feature with a Polygon geometry."""
    return {
        "type": "Feature",
        "properties": {"id": region_id},
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords],
        },
    }


def _write_json(path: Path, data: object) -> None:
    """Write *data* as pretty-printed JSON with a trailing newline."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Fixture seeding
# ---------------------------------------------------------------------------


def _seed_sources(tmp_path: Path) -> tuple[Path, Path]:
    """Write minimal synthetic source files and return (eaws_dir, fixture_path).

    Synthetic data covers two AT state codes:

    AT-07 (Tirol) — two L4 features with a shared L2 group:
        AT-07-01    → L2 = AT-07-01  (3-segment; 1:1 synthetic L2, strip-last = 'AT-07')
        AT-07-02-01 → L2 = AT-07-02  (4-segment; grouped L2)
        AT-07-02-02 → L2 = AT-07-02  (4-segment; same group)

    AT-08 (Vorarlberg) — one L4 feature:
        AT-08-01    → L2 = AT-08-01  (3-segment; 1:1 synthetic L2)
    """
    eaws_dir = tmp_path / "eaws"
    eaws_dir.mkdir()

    # AT-07: three micro-regions — one 1:1 and two grouped
    _write_json(
        eaws_dir / "AT-07_micro-regions.geojson.json",
        _make_geojson(
            [
                _make_feature(
                    "AT-07-01",
                    [
                        [10.0, 47.0],
                        [11.0, 47.0],
                        [11.0, 48.0],
                        [10.0, 48.0],
                        [10.0, 47.0],
                    ],
                ),
                _make_feature(
                    "AT-07-02-01",
                    [
                        [11.0, 47.0],
                        [12.0, 47.0],
                        [12.0, 48.0],
                        [11.0, 48.0],
                        [11.0, 47.0],
                    ],
                ),
                _make_feature(
                    "AT-07-02-02",
                    [
                        [12.0, 47.0],
                        [13.0, 47.0],
                        [13.0, 48.0],
                        [12.0, 48.0],
                        [12.0, 47.0],
                    ],
                ),
            ]
        ),
    )

    # AT-08: one micro-region (1:1 L2)
    _write_json(
        eaws_dir / "AT-08_micro-regions.geojson.json",
        _make_geojson(
            [
                _make_feature(
                    "AT-08-01",
                    [[9.0, 47.0], [10.0, 47.0], [10.0, 48.0], [9.0, 48.0], [9.0, 47.0]],
                ),
            ]
        ),
    )

    fixture_path = tmp_path / "eaws_AT.json"
    return eaws_dir, fixture_path


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    eaws_dir: Path,
    fixture_path: Path,
) -> None:
    """Redirect the command's module-level path constants to tmp_path copies."""
    from regions.management.commands import build_austria_fixture as mod

    monkeypatch.setattr(mod, "_EAWS_DIR", eaws_dir)
    monkeypatch.setattr(mod, "_AT_STATE_CODES", ["AT-07", "AT-08"])
    monkeypatch.setattr(mod, "_AUSTRIA_FIXTURE", fixture_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildAustriaFixtureDryRun:
    """Read-only (no --commit) behaviour."""

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit the command does not write any file."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        assert not fixture.exists()

        out = StringIO()
        call_command("build_austria_fixture", stdout=out)

        assert not fixture.exists()
        assert "Dry-run" in out.getvalue()

    def test_dry_run_reports_built_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run output includes the L1/L2/L4 counts.

        Synthetic data: 2 state codes → L1=2.
        AT-07: L2=2 (AT-07-01 + AT-07-02), L4=3.
        AT-08: L2=1 (AT-08-01), L4=1.
        Total: L1=2, L2=3, L4=4.
        """
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        out = StringIO()
        call_command("build_austria_fixture", stdout=out)

        output = out.getvalue()
        assert "L1=2" in output
        assert "L2=3" in output
        assert "L4=4" in output


class TestBuildAustriaFixtureCommit:
    """--commit writes the fixture with the expected content."""

    def test_commit_writes_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit writes eaws_AT.json containing L1 + L2 + L4 entries."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        assert fixture.exists()
        entries = json.loads(fixture.read_text(encoding="utf-8"))

        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        subs = [e for e in entries if e["model"] == "regions.subregion"]
        micros = [e for e in entries if e["model"] == "regions.microregion"]

        assert len(majors) == 2
        assert len(subs) == 3
        assert len(micros) == 4

    def test_commit_l1_entries_have_at_country(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 MajorRegion entries carry country='AT'."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["country"] == "AT" for e in majors)

    def test_commit_3segment_id_becomes_own_l2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 3-segment L4 ID (e.g. AT-07-01) is its own synthetic L2 parent."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # AT-07-01 is a 3-segment ID; stripping last gives 'AT-07' == L1 → 1:1 L2
        assert micros["AT-07-01"]["subregion"] == ["AT-07-01"]

    def test_commit_4segment_id_uses_grouped_l2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 4-segment L4 ID (e.g. AT-07-02-01) points to a shared L2 group."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # Both AT-07-02-01 and AT-07-02-02 should share L2 = AT-07-02
        assert micros["AT-07-02-01"]["subregion"] == ["AT-07-02"]
        assert micros["AT-07-02-02"]["subregion"] == ["AT-07-02"]

    def test_commit_l1_has_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 entries have a non-null boundary computed from child union."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["boundary"] is not None for e in majors)

    def test_commit_l4_entry_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 micro-region entries have region_id, name, slug, subregion, boundary."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }

        at07_01 = micros["AT-07-01"]
        assert at07_01["name"] == "AT-07-01"
        assert at07_01["slug"] == "at-07-01"
        assert at07_01["boundary"] is not None
        assert at07_01["centre"] is not None

    def test_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit produces identical bytes and reports 0 change(s)."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())
        after_first = fixture.read_text(encoding="utf-8")

        out = StringIO()
        call_command("build_austria_fixture", "--commit", stdout=out)
        after_second = fixture.read_text(encoding="utf-8")

        assert after_first == after_second
        assert "0" in out.getvalue()

    def test_commit_l4_sorted_by_region_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 entries appear in region_id order for stable fixture output."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_austria_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micro_ids = [
            e["fields"]["region_id"]
            for e in entries
            if e["model"] == "regions.microregion"
        ]
        assert micro_ids == sorted(micro_ids)
