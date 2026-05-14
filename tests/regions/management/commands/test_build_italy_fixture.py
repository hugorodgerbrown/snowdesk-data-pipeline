"""
tests/regions/management/commands/test_build_italy_fixture.py

Covers the ``build_italy_fixture`` command:
  - Dry-run (no --commit) writes nothing and exits 0.
  - --commit writes a fixture with the correct entry counts and shapes.
  - Idempotent: second --commit reports 0 change(s).
  - L1 entries carry country='IT'.
  - IT-32-BZ 3-dash L1 prefix: direct children become 1:1 synthetic L2 parents;
    deeper features group under the intermediate prefix.
  - L4 entries are sorted by region_id.
  - L4/L1/L2 names come from EAWS names lookup (monkeypatched in tests).
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
# Synthetic EAWS name map used across tests
# ---------------------------------------------------------------------------

_SYNTHETIC_IT_NAMES: dict[str, dict[str, str]] = {
    "it": {
        "IT-21": "Piemonte",
        "IT-21-TO": "Provincia di Torino",
        "IT-21-TO-05": "Torino Nord",
        "IT-21-TO-06": "Torino Sud",
        "IT-32-BZ": "Alto Adige",
        "IT-32-BZ-17": "Valle Isarco",
        "IT-32-BZ-15": "Val Venosta",
        "IT-32-BZ-15-02": "Val Venosta Est",
    },
    "en": {
        "IT-21": "Piemonte",
        "IT-21-TO": "Province of Turin",
        "IT-21-TO-05": "Turin North",
        "IT-21-TO-06": "Turin South",
        "IT-32-BZ": "South Tyrol",
        "IT-32-BZ-17": "Eisack Valley",
        "IT-32-BZ-15": "Vinschgau",
        "IT-32-BZ-15-02": "Vinschgau East",
    },
}


# ---------------------------------------------------------------------------
# Fixture seeding
# ---------------------------------------------------------------------------


def _seed_sources(tmp_path: Path) -> tuple[Path, Path]:
    """Write minimal synthetic source files and return (eaws_dir, fixture_path).

    Synthetic data covers two IT region codes:

    IT-21 (Piemonte) — two L4 features sharing a province L2:
        IT-21-TO-05 → L2 = IT-21-TO  (strip last → 'IT-21-TO' ≠ 'IT-21')
        IT-21-TO-06 → L2 = IT-21-TO  (same group)

    IT-32-BZ (South Tyrol / 3-dash L1) — two L4 features:
        IT-32-BZ-17    → L2 = IT-32-BZ-17  (strip last → 'IT-32-BZ' == L1 → 1:1)
        IT-32-BZ-15-02 → L2 = IT-32-BZ-15  (strip last → 'IT-32-BZ-15' ≠ L1 → grouped)
    """
    eaws_dir = tmp_path / "eaws" / "micro-regions"
    eaws_dir.mkdir(parents=True)

    # IT-21: two L4 features grouped under IT-21-TO
    _write_json(
        eaws_dir / "IT-21_micro-regions.geojson.json",
        _make_geojson(
            [
                _make_feature(
                    "IT-21-TO-05",
                    [[7.0, 44.0], [8.0, 44.0], [8.0, 45.0], [7.0, 45.0], [7.0, 44.0]],
                ),
                _make_feature(
                    "IT-21-TO-06",
                    [[8.0, 44.0], [9.0, 44.0], [9.0, 45.0], [8.0, 45.0], [8.0, 44.0]],
                ),
            ]
        ),
    )

    # IT-32-BZ: one 1:1 synthetic L2 and one grouped L2
    _write_json(
        eaws_dir / "IT-32-BZ_micro-regions.geojson.json",
        _make_geojson(
            [
                _make_feature(
                    "IT-32-BZ-17",
                    [
                        [10.0, 46.0],
                        [11.0, 46.0],
                        [11.0, 47.0],
                        [10.0, 47.0],
                        [10.0, 46.0],
                    ],
                ),
                _make_feature(
                    "IT-32-BZ-15-02",
                    [
                        [11.0, 46.0],
                        [12.0, 46.0],
                        [12.0, 47.0],
                        [11.0, 47.0],
                        [11.0, 46.0],
                    ],
                ),
            ]
        ),
    )

    fixture_path = tmp_path / "eaws_IT.json"
    return eaws_dir, fixture_path


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    eaws_dir: Path,
    fixture_path: Path,
) -> None:
    """Redirect the command's module-level path constants to tmp_path copies.

    Also monkeypatches ``regions.names.lookup`` to return values from the
    synthetic name map, avoiding dependency on the vendored EAWS files.
    """
    from regions.management.commands import build_italy_fixture as mod

    monkeypatch.setattr(mod, "_EAWS_DIR", eaws_dir)
    monkeypatch.setattr(mod, "_IT_REGION_CODES", ["IT-21", "IT-32-BZ"])
    monkeypatch.setattr(mod, "_ITALY_FIXTURE", fixture_path)

    # Patch the lookup helper so tests use the synthetic name map.
    monkeypatch.setattr(
        mod,
        "lookup",
        lambda key, lang: _SYNTHETIC_IT_NAMES.get(lang, {}).get(key),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildItalyFixtureDryRun:
    """Read-only (no --commit) behaviour."""

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit the command does not write any file."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        assert not fixture.exists()

        out = StringIO()
        call_command("build_italy_fixture", stdout=out)

        assert not fixture.exists()
        assert "Dry-run" in out.getvalue()

    def test_dry_run_reports_built_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run output includes the L1/L2/L4 counts.

        Synthetic data:
          IT-21: L1=1, L2=1 (IT-21-TO), L4=2
          IT-32-BZ: L1=1, L2=2 (IT-32-BZ-17 1:1 + IT-32-BZ-15 grouped), L4=2
          Total: L1=2, L2=3, L4=4.
        """
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        out = StringIO()
        call_command("build_italy_fixture", stdout=out)

        output = out.getvalue()
        assert "L1=2" in output
        assert "L2=3" in output
        assert "L4=4" in output


class TestBuildItalyFixtureCommit:
    """--commit writes the fixture with the expected content."""

    def test_commit_writes_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit writes eaws_IT.json containing L1 + L2 + L4 entries."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        assert fixture.exists()
        entries = json.loads(fixture.read_text(encoding="utf-8"))

        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        subs = [e for e in entries if e["model"] == "regions.subregion"]
        micros = [e for e in entries if e["model"] == "regions.microregion"]

        assert len(majors) == 2
        assert len(subs) == 3
        assert len(micros) == 4

    def test_commit_l1_entries_have_it_country(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 MajorRegion entries carry country='IT'."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["country"] == "IT" for e in majors)

    def test_commit_it21_features_grouped_under_province_l2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IT-21-TO-05 and IT-21-TO-06 both point to the shared L2 IT-21-TO."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["IT-21-TO-05"]["subregion"] == ["IT-21-TO"]
        assert micros["IT-21-TO-06"]["subregion"] == ["IT-21-TO"]

    def test_commit_it32bz_direct_child_becomes_own_l2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IT-32-BZ-17 is a direct child of the 3-dash L1; becomes its own 1:1 L2."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # strip-last of 'IT-32-BZ-17' → 'IT-32-BZ' == L1 → 1:1 synthetic L2
        assert micros["IT-32-BZ-17"]["subregion"] == ["IT-32-BZ-17"]

    def test_commit_it32bz_deeper_feature_uses_grouped_l2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IT-32-BZ-15-02 points to the shared L2 IT-32-BZ-15."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # strip-last of 'IT-32-BZ-15-02' → 'IT-32-BZ-15' ≠ L1 → grouped L2
        assert micros["IT-32-BZ-15-02"]["subregion"] == ["IT-32-BZ-15"]

    def test_commit_l1_has_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 entries have a non-null boundary computed from child union."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["boundary"] is not None for e in majors)

    def test_commit_l4_name_from_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 names come from the EAWS it.json lookup (monkeypatched here)."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }

        it21 = micros["IT-21-TO-05"]
        assert it21["name"] == "Torino Nord"
        assert it21["slug"] == "it-21-to-05"
        assert it21["boundary"] is not None
        assert it21["centre"] is not None

    def test_commit_l4_name_falls_back_to_region_id_on_miss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When lookup returns None, the L4 name falls back to the region_id."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)
        # Override lookup to always return None
        from regions.management.commands import build_italy_fixture as mod

        monkeypatch.setattr(mod, "lookup", lambda key, lang: None)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["IT-21-TO-05"]["name"] == "IT-21-TO-05"

    def test_commit_l1_name_from_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 name_native and name_en come from the EAWS it/en lookup."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = {
            e["fields"]["prefix"]: e["fields"]
            for e in entries
            if e["model"] == "regions.majorregion"
        }
        assert majors["IT-21"]["name_native"] == "Piemonte"
        assert majors["IT-21"]["name_en"] == "Piemonte"

    def test_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit produces identical bytes and reports 0 change(s)."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())
        after_first = fixture.read_text(encoding="utf-8")

        out = StringIO()
        call_command("build_italy_fixture", "--commit", stdout=out)
        after_second = fixture.read_text(encoding="utf-8")

        assert after_first == after_second
        assert "0" in out.getvalue()

    def test_commit_l4_sorted_by_region_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 entries appear in region_id order for stable fixture output."""
        eaws_dir, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws_dir, fixture)

        call_command("build_italy_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micro_ids = [
            e["fields"]["region_id"]
            for e in entries
            if e["model"] == "regions.microregion"
        ]
        assert micro_ids == sorted(micro_ids)
