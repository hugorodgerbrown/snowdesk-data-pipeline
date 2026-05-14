"""
tests/scripts/test_build_regions_fixture.py — Tests for the build_regions_fixture script.

Covers:
  - Neighbour-graph computation: two regions sharing a border (with eps buffer)
    are reported as mutual neighbours.
  - build_fixture preserves existing L1/L2 entries from the fixture file.
  - build_fixture replaces only regions.microregion entries.
  - build_fixture applies EAWS de.json name override when available (via
    monkeypatching ``_get_eaws_name_de``).
  - build_fixture falls back to CSV region_name when EAWS has no entry.
"""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.build_regions_fixture import (
    NEIGHBOUR_EPS_DEGREES,
    _compute_neighbour_graph,
    build_fixture,
)


def _square(x: float, y: float, side: float = 1.0) -> dict[str, Any]:
    """Return a GeoJSON Polygon for an axis-aligned square at (x, y)."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [x, y],
                [x + side, y],
                [x + side, y + side],
                [x, y + side],
                [x, y],
            ]
        ],
    }


# ---------------------------------------------------------------------------
# Helpers for build_fixture tests
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal regions CSV to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["region_id", "region_name", "slug", "centre", "boundary"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _minimal_row(
    region_id: str, name: str, x: float = 7.0, y: float = 47.0
) -> dict[str, str]:
    """Return a minimal CSV row for *region_id* with a 1-degree unit-square polygon."""
    centre = json.dumps({"lon": x + 0.5, "lat": y + 0.5})
    boundary = json.dumps(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [x, y],
                    [x + 1, y],
                    [x + 1, y + 1],
                    [x, y + 1],
                    [x, y],
                ]
            ],
        }
    )
    return {
        "region_id": region_id,
        "region_name": name,
        "slug": region_id.lower().replace("-", "-"),
        "centre": centre,
        "boundary": boundary,
    }


# ---------------------------------------------------------------------------
# Neighbour-graph tests
# ---------------------------------------------------------------------------


class TestComputeNeighbourGraph:
    """Unit tests for the _compute_neighbour_graph helper."""

    def test_squares_sharing_an_edge_are_neighbours(self) -> None:
        """Two unit squares sharing a vertical edge → mutual neighbours."""
        boundaries = [
            ("A", _square(0, 0)),
            ("B", _square(1, 0)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": ["B"], "B": ["A"]}

    def test_squares_far_apart_are_not_neighbours(self) -> None:
        """Squares with a 1-degree gap (~111 km) are not neighbours."""
        boundaries = [
            ("A", _square(0, 0)),
            (
                "B",
                _square(2, 0),
            ),  # Gap of 1.0 between right edge of A and left edge of B.
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": [], "B": []}

    def test_near_miss_within_eps_is_treated_as_neighbour(self) -> None:
        """A sub-eps gap is bridged — guards against float drift in source data."""
        gap = NEIGHBOUR_EPS_DEGREES / 2  # well inside the buffer
        boundaries = [
            ("A", _square(0, 0)),
            ("B", _square(1 + gap, 0)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": ["B"], "B": ["A"]}

    def test_gap_just_outside_eps_is_not_a_neighbour(self) -> None:
        """A gap larger than 2*eps is never bridged."""
        gap = NEIGHBOUR_EPS_DEGREES * 10
        boundaries = [
            ("A", _square(0, 0)),
            ("B", _square(1 + gap, 0)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": [], "B": []}

    def test_corner_only_contact_is_a_neighbour(self) -> None:
        """Squares meeting at a single shared vertex are neighbours.

        Strict ``touches()`` returns True for vertex-only contact; the
        buffered ``intersects()`` we use must agree.
        """
        boundaries = [
            ("A", _square(0, 0)),  # corners (0,0) (1,0) (1,1) (0,1)
            ("B", _square(1, 1)),  # corners (1,1) (2,1) (2,2) (1,2)
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert "B" in graph["A"]
        assert "A" in graph["B"]

    def test_graph_is_symmetric(self) -> None:
        """Every edge appears on both endpoints."""
        boundaries = [
            ("A", _square(0, 0)),
            ("B", _square(1, 0)),
            ("C", _square(0, 1)),
            ("D", _square(5, 5)),  # isolated
        ]
        graph = _compute_neighbour_graph(boundaries)
        for region_id, neighbours in graph.items():
            for neighbour in neighbours:
                assert region_id in graph[neighbour], (
                    f"Asymmetric: {region_id}->{neighbour} but not back"
                )

    def test_neighbour_lists_are_sorted(self) -> None:
        """Neighbour lists are alphabetically sorted for stable fixture output."""
        boundaries = [
            ("CH-2", _square(0, 0)),
            ("CH-3", _square(1, 0)),
            ("CH-1", _square(0, 1)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        # CH-2 borders both CH-3 (to the right) and CH-1 (above).
        assert graph["CH-2"] == sorted(graph["CH-2"])
        assert graph["CH-2"] == ["CH-1", "CH-3"]

    def test_isolated_region_has_empty_neighbour_list(self) -> None:
        """A region disjoint from all others gets an empty list, not a missing key."""
        boundaries = [
            ("A", _square(0, 0)),
            ("B", _square(10, 10)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": [], "B": []}


# ---------------------------------------------------------------------------
# build_fixture tests
# ---------------------------------------------------------------------------


class TestBuildFixture:
    """Tests for the build_fixture function."""

    def test_preserves_existing_l1_l2_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """build_fixture retains non-microregion entries from the existing fixture."""
        import scripts.build_regions_fixture as script_mod

        monkeypatch.setattr(script_mod, "_get_eaws_name_de", lambda rid: None)

        csv_path = tmp_path / "CH_micro-regions.csv"
        fixture_path = tmp_path / "eaws_CH.json"

        # Seed a fixture with L1 + L2 entries that must be preserved.
        existing_entries = [
            {
                "model": "regions.majorregion",
                "fields": {
                    "prefix": "CH-1",
                    "name_native": "Bern",
                    "name_en": "Berne",
                },
            },
            {
                "model": "regions.subregion",
                "fields": {"prefix": "CH-10", "major": ["CH-1"], "name_native": "Bern"},
            },
        ]
        fixture_path.write_text(
            json.dumps(existing_entries, indent=2), encoding="utf-8"
        )

        _write_csv(
            csv_path,
            [_minimal_row("CH-1011", "Brienz", x=8.0, y=46.7)],
        )

        build_fixture(csv_path, fixture_path)

        result = json.loads(fixture_path.read_text(encoding="utf-8"))
        models_present = {e["model"] for e in result}

        assert "regions.majorregion" in models_present
        assert "regions.subregion" in models_present
        assert "regions.microregion" in models_present

        majors = [e for e in result if e["model"] == "regions.majorregion"]
        assert len(majors) == 1
        assert majors[0]["fields"]["prefix"] == "CH-1"

    def test_replaces_microregion_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """build_fixture replaces old microregion rows with freshly built ones."""
        import scripts.build_regions_fixture as script_mod

        monkeypatch.setattr(script_mod, "_get_eaws_name_de", lambda rid: None)

        csv_path = tmp_path / "CH_micro-regions.csv"
        fixture_path = tmp_path / "eaws_CH.json"

        # Old fixture has a stale microregion entry.
        stale = [
            {
                "model": "regions.microregion",
                "fields": {"region_id": "CH-1011", "name": "OLD NAME"},
            }
        ]
        fixture_path.write_text(json.dumps(stale, indent=2), encoding="utf-8")

        _write_csv(
            csv_path,
            [_minimal_row("CH-1011", "Brienz", x=8.0, y=46.7)],
        )

        build_fixture(csv_path, fixture_path)

        result = json.loads(fixture_path.read_text(encoding="utf-8"))
        micros = [e for e in result if e["model"] == "regions.microregion"]
        assert len(micros) == 1
        # Old name is gone — now shows CSV name (EAWS returns None in this test).
        assert micros[0]["fields"]["name"] == "Brienz"

    def test_eaws_name_overrides_csv_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EAWS de.json canonical name is used when available, overriding the CSV."""
        import scripts.build_regions_fixture as script_mod

        eaws_names = {"CH-1011": "Brienz EAWS"}
        monkeypatch.setattr(
            script_mod, "_get_eaws_name_de", lambda rid: eaws_names.get(rid)
        )

        csv_path = tmp_path / "CH_micro-regions.csv"
        fixture_path = tmp_path / "eaws_CH.json"
        fixture_path.write_text("[]", encoding="utf-8")

        _write_csv(
            csv_path,
            [_minimal_row("CH-1011", "Brienz CSV", x=8.0, y=46.7)],
        )

        build_fixture(csv_path, fixture_path)

        result = json.loads(fixture_path.read_text(encoding="utf-8"))
        micros = [e for e in result if e["model"] == "regions.microregion"]
        assert len(micros) == 1
        assert micros[0]["fields"]["name"] == "Brienz EAWS"

    def test_csv_name_fallback_when_eaws_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CSV region_name is used when EAWS returns None for that region_id."""
        import scripts.build_regions_fixture as script_mod

        monkeypatch.setattr(script_mod, "_get_eaws_name_de", lambda rid: None)

        csv_path = tmp_path / "CH_micro-regions.csv"
        fixture_path = tmp_path / "eaws_CH.json"
        fixture_path.write_text("[]", encoding="utf-8")

        _write_csv(
            csv_path,
            [_minimal_row("CH-1011", "Brienz CSV", x=8.0, y=46.7)],
        )

        build_fixture(csv_path, fixture_path)

        result = json.loads(fixture_path.read_text(encoding="utf-8"))
        micros = [e for e in result if e["model"] == "regions.microregion"]
        assert micros[0]["fields"]["name"] == "Brienz CSV"
