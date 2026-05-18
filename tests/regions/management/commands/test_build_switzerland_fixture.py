"""
tests/regions/management/commands/test_build_switzerland_fixture.py

Covers ``build_switzerland_fixture``:
  - Dry-run (no --commit) writes nothing and exits 0.
  - --commit writes a fixture with the correct entry counts.
  - L4 names are resolved via EAWS de.json lookup (monkeypatched).
  - L4 name falls back to region_id when lookup returns None.
  - L1/L2 name_native/name_en are carried through from the existing fixture.
  - Neighbour graph is non-empty for a known interior region.
  - Re-running over the same data is idempotent (0 change(s) on second run).
  - L4 entries appear sorted by region_id in the output.
  - L1 entries carry country='CH'.
  - Generated fixture has non-null boundary on L1 entries.
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
    """Build a minimal EAWS L4 feature with a MultiPolygon geometry."""
    return {
        "type": "Feature",
        "properties": {"id": region_id},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[coords]],
        },
    }


def _unit_square(x: float, y: float) -> list:
    """Return a closed unit-square ring starting at (x, y)."""
    return [
        [x, y],
        [x + 1.0, y],
        [x + 1.0, y + 1.0],
        [x, y + 1.0],
        [x, y],
    ]


def _write_json(path: Path, data: object) -> None:
    """Write *data* as pretty-printed JSON with a trailing newline."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Synthetic name map and existing fixture
# ---------------------------------------------------------------------------

# Names for the synthetic regions
_SYNTHETIC_NAMES: dict[str, str] = {
    "CH-1111": "Waadtländer Voralpen",
    "CH-1211": "Brienz",
    "CH-2111": "Muotathal",
}

# Existing fixture L1/L2 names — these must be carried through unchanged.
_EXISTING_L1_NAMES: dict[str, tuple[str, str]] = {
    "CH-1": ("Westliche Voralpen", "Western Prealps"),
    "CH-2": ("Zentrale Voralpen", "Central Prealps"),
}
_EXISTING_L2_NAMES: dict[str, tuple[str, str]] = {
    "CH-11": ("Alpes vaudoises", "Vaud Alps"),
    "CH-12": ("Berner Oberland", "Bernese Oberland"),
    "CH-21": ("Zentrale Voralpen", "Central Prealps"),
}


def _build_existing_fixture() -> list[dict]:
    """Build a minimal existing fixture with L1/L2 entries for name carry-through."""
    entries = []
    for prefix, (native, en) in _EXISTING_L1_NAMES.items():
        entries.append(
            {
                "model": "regions.majorregion",
                "fields": {
                    "prefix": prefix,
                    "country": "CH",
                    "name_native": native,
                    "name_en": en,
                    "centre": {"lon": 7.0, "lat": 46.5},
                    "bbox": [6.0, 46.0, 8.0, 47.0],
                    "boundary": {"type": "Polygon", "coordinates": []},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            }
        )
    for prefix, (native, en) in _EXISTING_L2_NAMES.items():
        entries.append(
            {
                "model": "regions.subregion",
                "fields": {
                    "prefix": prefix,
                    "major": [prefix[:4]],
                    "name_native": native,
                    "name_en": en,
                    "centre": {"lon": 7.0, "lat": 46.5},
                    "bbox": [6.0, 46.0, 8.0, 47.0],
                    "boundary": {"type": "Polygon", "coordinates": []},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            }
        )
    return entries


def _seed_sources(
    tmp_path: Path,
) -> tuple[Path, Path]:
    """Write minimal synthetic source files and return (eaws_geojson, fixture) paths.

    The synthetic data uses 3 micro-regions across 3 L2/L1 groups:
        CH-1111 → L2=CH-11, L1=CH-1   (adjacent to CH-1211)
        CH-1211 → L2=CH-12, L1=CH-1   (adjacent to CH-1111)
        CH-2111 → L2=CH-21, L1=CH-2   (isolated — different L1)

    Note: ``CH-1111[:5]`` = ``CH-11`` and ``CH-1211[:5]`` = ``CH-12``, giving
    3 distinct L2 groups across 2 L1 groups.
    """
    eaws_dir = tmp_path / "eaws" / "micro-regions"
    eaws_dir.mkdir(parents=True)

    eaws_path = eaws_dir / "CH_micro-regions.geojson"
    _write_json(
        eaws_path,
        _make_geojson(
            [
                # CH-1111 and CH-1211 are adjacent (share an edge at x=1)
                _make_feature("CH-1111", _unit_square(0.0, 0.0)),
                _make_feature("CH-1211", _unit_square(1.0, 0.0)),
                # CH-2111 is isolated (far away)
                _make_feature("CH-2111", _unit_square(10.0, 10.0)),
            ]
        ),
    )

    fixture_path = tmp_path / "eaws_CH.json"
    _write_json(fixture_path, _build_existing_fixture())

    return eaws_path, fixture_path


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    eaws_path: Path,
    fixture_path: Path,
) -> None:
    """Redirect the command's module-level path constants to tmp copies.

    Also monkeypatches ``lookup`` to return values from the synthetic name
    map, avoiding dependency on the vendored EAWS files.
    """
    from regions.management.commands import build_switzerland_fixture as mod

    monkeypatch.setattr(mod, "_EAWS_GEOJSON", eaws_path)
    monkeypatch.setattr(mod, "_CH_FIXTURE", fixture_path)
    monkeypatch.setattr(
        mod,
        "lookup",
        lambda key, lang: _SYNTHETIC_NAMES.get(key),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildSwitzerlandFixtureDryRun:
    """Read-only (no --commit) behaviour."""

    def test_dry_run_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --commit the command does not modify any file."""
        eaws, fixture = _seed_sources(tmp_path)
        original = fixture.read_text(encoding="utf-8")
        _patch_paths(monkeypatch, eaws, fixture)

        out = StringIO()
        call_command("build_switzerland_fixture", stdout=out)

        assert fixture.read_text(encoding="utf-8") == original
        assert "Dry-run" in out.getvalue()

    def test_dry_run_reports_built_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run output includes the L1/L2/L4 counts."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        out = StringIO()
        call_command("build_switzerland_fixture", stdout=out)

        output = out.getvalue()
        assert "L1=2" in output
        assert "L2=3" in output
        assert "L4=3" in output


class TestBuildSwitzerlandFixtureCommit:
    """``--commit`` writes the fixture with the expected content."""

    def test_commit_writes_fixture_with_correct_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit writes a fixture containing L1 + L2 + L4 entries."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        subs = [e for e in entries if e["model"] == "regions.subregion"]
        micros = [e for e in entries if e["model"] == "regions.microregion"]

        assert len(majors) == 2
        assert len(subs) == 3
        assert len(micros) == 3

    def test_commit_l1_entries_have_ch_country(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 MajorRegion entries carry country='CH'."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["country"] == "CH" for e in majors)

    def test_commit_l4_name_from_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 names are resolved via the lookup helper (monkeypatched here)."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["CH-1111"]["name"] == "Waadtländer Voralpen"
        assert micros["CH-1211"]["name"] == "Brienz"
        assert micros["CH-2111"]["name"] == "Muotathal"

    def test_commit_l4_name_falls_back_to_region_id_on_miss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When lookup returns None, the L4 name falls back to the region_id."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)
        from regions.management.commands import build_switzerland_fixture as mod

        monkeypatch.setattr(mod, "lookup", lambda key, lang: None)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["CH-1111"]["name"] == "CH-1111"
        assert micros["CH-2111"]["name"] == "CH-2111"

    def test_commit_l1_names_carried_from_existing_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 name_native and name_en are preserved from the existing fixture."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = {
            e["fields"]["prefix"]: e["fields"]
            for e in entries
            if e["model"] == "regions.majorregion"
        }
        assert majors["CH-1"]["name_native"] == "Westliche Voralpen"
        assert majors["CH-1"]["name_en"] == "Western Prealps"
        assert majors["CH-2"]["name_native"] == "Zentrale Voralpen"
        assert majors["CH-2"]["name_en"] == "Central Prealps"

    def test_commit_l2_names_carried_from_existing_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L2 name_native and name_en are preserved from the existing fixture."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        subs = {
            e["fields"]["prefix"]: e["fields"]
            for e in entries
            if e["model"] == "regions.subregion"
        }
        assert subs["CH-11"]["name_native"] == "Alpes vaudoises"
        assert subs["CH-11"]["name_en"] == "Vaud Alps"
        assert subs["CH-12"]["name_native"] == "Berner Oberland"
        assert subs["CH-12"]["name_en"] == "Bernese Oberland"

    def test_commit_l2_name_falls_back_when_not_in_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When an L2 prefix has no entry in the existing fixture, it falls back to prefix."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)
        # Remove CH-21 from the existing fixture so it has no name entry.
        existing = json.loads(fixture.read_text(encoding="utf-8"))
        without_ch21 = [
            e
            for e in existing
            if not (
                e["model"] == "regions.subregion" and e["fields"]["prefix"] == "CH-21"
            )
        ]
        fixture.write_text(
            json.dumps(without_ch21, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        subs = {
            e["fields"]["prefix"]: e["fields"]
            for e in entries
            if e["model"] == "regions.subregion"
        }
        assert subs["CH-21"]["name_native"] == "CH-21"
        assert subs["CH-21"]["name_en"] == "CH-21"

    def test_commit_neighbour_graph_non_empty_for_adjacent_regions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adjacent L4 regions appear in each other's neighbour list."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # CH-1111 and CH-1211 share an edge — they should be mutual neighbours.
        ch1111_neighbours = [n[0] for n in micros["CH-1111"]["neighbours"]]
        ch1121_neighbours = [n[0] for n in micros["CH-1211"]["neighbours"]]
        assert "CH-1211" in ch1111_neighbours
        assert "CH-1111" in ch1121_neighbours

    def test_commit_isolated_region_has_no_neighbours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An isolated L4 region has an empty neighbours list."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        # CH-2111 is placed far from the others — should have no neighbours.
        assert micros["CH-2111"]["neighbours"] == []

    def test_commit_l4_sorted_by_region_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 entries appear in region_id order for stable fixture output."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micro_ids = [
            e["fields"]["region_id"]
            for e in entries
            if e["model"] == "regions.microregion"
        ]
        assert micro_ids == sorted(micro_ids)

    def test_commit_l1_has_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L1 entries have a non-null boundary computed from child union."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        majors = [e for e in entries if e["model"] == "regions.majorregion"]
        assert all(e["fields"]["boundary"] is not None for e in majors)
        assert all(e["fields"]["boundary"] != {} for e in majors)

    def test_commit_l4_subregion_fk_matches_l2_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L4 entries reference the correct L2 prefix via natural-key FK."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())

        entries = json.loads(fixture.read_text(encoding="utf-8"))
        micros = {
            e["fields"]["region_id"]: e["fields"]
            for e in entries
            if e["model"] == "regions.microregion"
        }
        assert micros["CH-1111"]["subregion"] == ["CH-11"]
        assert micros["CH-1211"]["subregion"] == ["CH-12"]
        assert micros["CH-2111"]["subregion"] == ["CH-21"]

    def test_commit_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second --commit produces identical bytes and reports 0 change(s)."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        call_command("build_switzerland_fixture", "--commit", stdout=StringIO())
        after_first = fixture.read_text(encoding="utf-8")

        out = StringIO()
        call_command("build_switzerland_fixture", "--commit", stdout=out)
        after_second = fixture.read_text(encoding="utf-8")

        assert after_first == after_second
        assert "0" in out.getvalue()

    def test_commit_output_message_contains_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--commit`` outputs a success message naming the fixture path."""
        eaws, fixture = _seed_sources(tmp_path)
        _patch_paths(monkeypatch, eaws, fixture)

        out = StringIO()
        call_command("build_switzerland_fixture", "--commit", stdout=out)

        assert "Wrote" in out.getvalue()
        assert str(fixture) in out.getvalue()
