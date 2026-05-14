"""
tests/regions/management/commands/test_build_france_fixture.py

Covers the ``build_france_fixture`` command:
  - Dry-run (no --commit) writes nothing and exits 0.
  - --commit writes a fixture with the correct entry counts and shapes.
  - Idempotent: second --commit reports 0 change(s).
  - L4 entries have correct region_id, name (from EAWS names lookup),
    subregion FK natural key, and country (inferred via the L1 parent).
  - Names come from regions.names.lookup (monkeypatched in tests).
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


def _make_eaws_geojson(features: list[dict]) -> dict:
    """Wrap a list of features in a GeoJSON FeatureCollection."""
    return {"type": "FeatureCollection", "features": features}


def _make_eaws_feature(region_id: str, coords: list) -> dict:
    """Build a minimal EAWS L4 feature with a Polygon geometry."""
    return {
        "type": "Feature",
        "properties": {"id": region_id},
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords],
        },
    }


def _make_mf_geojson(features: list[dict]) -> dict:
    """Wrap MF massif feature properties into a FeatureCollection."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": props,
                "geometry": None,
            }
            for props in features
        ],
    }


def _write_json(path: Path, data: object) -> None:
    """Write *data* as pretty-printed JSON with a trailing newline."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Fixture seeding
# ---------------------------------------------------------------------------

# Synthetic EAWS name map used in tests — injected via monkeypatch on
# regions.names.lookup so tests do not rely on the real vendored files.
_SYNTHETIC_FR_NAMES: dict[str, str] = {
    "FR-01": "Chablais",
    "FR-02": "Aravis",
    "FR-64": "Pays-Basque",
}


def _seed_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write minimal synthetic source files and return their paths.

    Returns (eaws_geojson, mf_massifs, france_fixture).

    The synthetic data uses 2 mountains, 3 micro-regions:
        mountain "Alpes du Nord" (FR-1 / FR-1A) → FR-01, FR-02
        mountain "Pyrenees"     (FR-3 / FR-3A) → FR-64
    """
    eaws_dir = tmp_path / "eaws" / "micro-regions"
    eaws_dir.mkdir(parents=True)

    # A small unit square for each region — adjacent so unary_union collapses
    # FR-01 and FR-02 to one Polygon.
    eaws_path = eaws_dir / "FR_micro-regions.geojson"
    _write_json(
        eaws_path,
        _make_eaws_geojson(
            [
                _make_eaws_feature(
                    "FR-01",
                    [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
                ),
                _make_eaws_feature(
                    "FR-02",
                    [[1.0, 0.0], [2.0, 0.0], [2.0, 1.0], [1.0, 1.0], [1.0, 0.0]],
                ),
                _make_eaws_feature(
                    "FR-64",
                    [[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 6.0], [5.0, 5.0]],
                ),
            ]
        ),
    )

    mf_dir = tmp_path / "meteofrance"
    mf_dir.mkdir(parents=True)
    mf_path = mf_dir / "liste-massifs.geojson"
    _write_json(
        mf_path,
        _make_mf_geojson(
            [
                {"code": 1, "mountain": "Alpes du Nord"},
                {"code": 2, "mountain": "Alpes du Nord"},
                {"code": 64, "mountain": "Pyrenees"},
            ]
        ),
    )

    fixture_path = tmp_path / "france.json"
    return eaws_path, mf_path, fixture_path


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    eaws_path: Path,
    mf_path: Path,
    fixture_path: Path,
) -> None:
    """Redirect the command's module-level path constants to the tmp_path copies.

    Also monkeypatches ``regions.names.lookup`` to return values from the
    synthetic name map, avoiding dependency on the vendored EAWS files.
    """
    from regions.management.commands import build_france_fixture as mod

    monkeypatch.setattr(mod, "_EAWS_GEOJSON", eaws_path)
    monkeypatch.setattr(mod, "_MF_MASSIFS", mf_path)
    monkeypatch.setattr(mod, "_FRANCE_FIXTURE", fixture_path)

    # Patch the lookup helper so tests use the synthetic name map.
    monkeypatch.setattr(
        mod,
        "lookup",
        lambda key, lang: _SYNTHETIC_FR_NAMES.get(key),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildFranceFixtureDryRun:
    """Read-only (no --commit) behaviour."""

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit the command does not write any file."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        assert not fixture.exists()

        out = StringIO()
        call_command("build_france_fixture", stdout=out)

        assert not fixture.exists()
        assert "Dry-run" in out.getvalue()

    def test_dry_run_reports_built_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run output includes the L1/L2/L4 counts."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        out = StringIO()
        call_command("build_france_fixture", stdout=out)

        output = out.getvalue()
        # Synthetic data: 2 mountains produce 2 L1 + 2 L2; 3 features → L4=3
        assert "L1=2" in output
        assert "L2=2" in output
        assert "L4=3" in output


class TestBuildFranceFixtureCommit:
    """--commit writes the fixture with the expected content."""

    def test_commit_writes_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit writes france.json containing L1 + L2 + L4 entries."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        assert fixture.exists()
        entries = json.loads(fixture.read_text(encoding="utf-8"))

        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        subs = [e for e in entries if e["model"] == "regions.subregion"]
        micros = [e for e in entries if e["model"] == "regions.microregion"]

        assert len(majors) == 2
        assert len(subs) == 2
        assert len(micros) == 3

    def test_commit_l1_entries_have_fr_country(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 MajorRegion entries carry country='FR'."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["country"] == "FR" for e in majors)

    def test_commit_l4_fr64_entry_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 micro-region FR-64 has the expected region_id, name, and subregion FK."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }

        # FR-64 belongs to Pyrenees → FR-3 / FR-3A
        fr64 = micros["FR-64"]
        assert fr64["name"] == "Pays-Basque"
        assert fr64["slug"] == "fr-64"
        assert fr64["subregion"] == ["FR-3A"]
        assert fr64["boundary"] is not None
        assert fr64["centre"] is not None

    def test_commit_l4_name_from_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 names are resolved via EAWS names lookup (monkeypatched here)."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["FR-01"]["name"] == "Chablais"
        assert micros["FR-02"]["name"] == "Aravis"

    def test_commit_l4_name_falls_back_to_region_id_on_miss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When lookup returns None, the L4 name falls back to the region_id."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)
        # Override lookup to always return None
        from regions.management.commands import build_france_fixture as mod

        monkeypatch.setattr(mod, "lookup", lambda key, lang: None)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["FR-64"]["name"] == "FR-64"

    def test_commit_l1_has_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 entries have a non-null boundary computed from child union."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["boundary"] is not None for e in majors)

    def test_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit produces identical bytes and reports 0 change(s)."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())
        after_first = fixture.read_text(encoding="utf-8")

        out = StringIO()
        call_command("build_france_fixture", "--commit", stdout=out)
        after_second = fixture.read_text(encoding="utf-8")

        assert after_first == after_second
        assert "0" in out.getvalue()

    def test_commit_l4_sorted_by_region_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 entries appear in region_id order for stable fixture output."""
        eaws, mf, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, mf, fixture)

        call_command("build_france_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micro_ids = [
            e["fields"]["region_id"]
            for e in entries
            if e["model"] == "regions.microregion"
        ]
        assert micro_ids == sorted(micro_ids)
