"""
tests/scripts/test_build_regions_fixture.py — Tests for the build_regions_fixture script.

Covers:

* ``_compute_neighbour_graph`` — two regions are neighbours when one polygon,
  expanded by ``eps`` degrees, intersects the other.  This absorbs the
  sub-metre float gaps that show up between independently-digitised cantonal
  polygons (where strict ``touches()`` would falsely report a near-miss).

* ``_close_polygon_rings`` — both Polygon and MultiPolygon geometries are
  handled; non-polygon input is returned unchanged.

* ``_derive_euregio_parent_pair`` — all four depth/prefix variants:
  AT-07-NN, AT-07-NN-XX, IT-32-BZ-NN, IT-32-BZ-NN-XX (plus TN equivalents).

* ``build_euregio_fixture`` — end-to-end run against a tiny synthetic
  GeoJSON + bulletin input; asserts fixture structure is correct.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.build_regions_fixture import (
    NEIGHBOUR_EPS_DEGREES,
    _close_polygon_rings,
    _compute_neighbour_graph,
    _derive_euregio_parent_pair,
    _load_bulletin_region_names,
    _load_geojson_features,
    build_euregio_fixture,
)

# ---------------------------------------------------------------------------
# Helpers for building synthetic geometry
# ---------------------------------------------------------------------------


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


def _multi_square(x: float, y: float, side: float = 1.0) -> dict[str, Any]:
    """Return a GeoJSON MultiPolygon wrapping a single square at (x, y)."""
    ring = [
        [x, y],
        [x + side, y],
        [x + side, y + side],
        [x, y + side],
        [x, y],
    ]
    return {
        "type": "MultiPolygon",
        "coordinates": [[ring]],
    }


# ---------------------------------------------------------------------------
# Tests for _compute_neighbour_graph
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

    def test_multipolygon_geometries_are_handled(self) -> None:
        """MultiPolygon input does not raise; adjacency is detected normally."""
        boundaries = [
            ("A", _multi_square(0, 0)),
            ("B", _multi_square(1, 0)),
        ]
        graph = _compute_neighbour_graph(boundaries)
        assert graph == {"A": ["B"], "B": ["A"]}


# ---------------------------------------------------------------------------
# Tests for _close_polygon_rings
# ---------------------------------------------------------------------------


class TestClosePolygonRings:
    """Tests for the polygon ring closing helper."""

    def test_polygon_open_ring_is_closed(self) -> None:
        """An open-ring Polygon gets the first coordinate appended as last."""
        open_poly = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        }
        closed = _close_polygon_rings(open_poly)
        ring = closed["coordinates"][0]
        assert ring[0] == ring[-1], "First and last coordinates should be equal"
        assert len(ring) == 5

    def test_polygon_already_closed_is_unchanged(self) -> None:
        """A ring that is already closed is not duplicated."""
        closed_poly = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }
        result = _close_polygon_rings(closed_poly)
        assert len(result["coordinates"][0]) == 5  # unchanged

    def test_multipolygon_open_rings_are_closed(self) -> None:
        """MultiPolygon rings are closed in all polygons."""
        open_multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1]]],  # open ring
                [[[5, 5], [6, 5], [6, 6], [5, 6]]],  # open ring
            ],
        }
        closed = _close_polygon_rings(open_multi)
        for poly_rings in closed["coordinates"]:
            for ring in poly_rings:
                assert ring[0] == ring[-1], "Each ring should be closed"

    def test_non_polygon_input_returned_unchanged(self) -> None:
        """Point geometry passes through without modification."""
        point = {"type": "Point", "coordinates": [0, 0]}
        assert _close_polygon_rings(point) == point


# ---------------------------------------------------------------------------
# Tests for _derive_euregio_parent_pair
# ---------------------------------------------------------------------------


class TestDeriveEuregioParentPair:
    """Tests for all ID-depth patterns."""

    def test_at07_simple_passthrough(self) -> None:
        """AT-07-NN → major=AT-07, sub=AT-07 (passthrough)."""
        assert _derive_euregio_parent_pair("AT-07-01") == ("AT-07", "AT-07")
        assert _derive_euregio_parent_pair("AT-07-29") == ("AT-07", "AT-07")

    def test_at07_nested(self) -> None:
        """AT-07-NN-XX → major=AT-07, sub=AT-07-NN."""
        assert _derive_euregio_parent_pair("AT-07-02-01") == ("AT-07", "AT-07-02")
        assert _derive_euregio_parent_pair("AT-07-14-05") == ("AT-07", "AT-07-14")
        assert _derive_euregio_parent_pair("AT-07-29-03") == ("AT-07", "AT-07-29")

    def test_it32_bz_simple_passthrough(self) -> None:
        """IT-32-BZ-NN → major=IT-32-BZ, sub=IT-32-BZ (passthrough)."""
        assert _derive_euregio_parent_pair("IT-32-BZ-03") == ("IT-32-BZ", "IT-32-BZ")
        assert _derive_euregio_parent_pair("IT-32-BZ-20") == ("IT-32-BZ", "IT-32-BZ")

    def test_it32_bz_nested(self) -> None:
        """IT-32-BZ-NN-XX → major=IT-32-BZ, sub=IT-32-BZ-NN."""
        assert _derive_euregio_parent_pair("IT-32-BZ-01-01") == (
            "IT-32-BZ",
            "IT-32-BZ-01",
        )
        assert _derive_euregio_parent_pair("IT-32-BZ-08-03") == (
            "IT-32-BZ",
            "IT-32-BZ-08",
        )

    def test_it32_tn_simple_passthrough(self) -> None:
        """IT-32-TN-NN → major=IT-32-TN, sub=IT-32-TN (passthrough)."""
        assert _derive_euregio_parent_pair("IT-32-TN-01") == ("IT-32-TN", "IT-32-TN")
        assert _derive_euregio_parent_pair("IT-32-TN-21") == ("IT-32-TN", "IT-32-TN")

    def test_unknown_scheme_raises(self) -> None:
        """An unrecognised ID raises ValueError."""
        with pytest.raises(ValueError, match="Cannot derive EUREGIO parent pair"):
            _derive_euregio_parent_pair("CH-4115")


# ---------------------------------------------------------------------------
# Tests for _load_geojson_features
# ---------------------------------------------------------------------------


class TestLoadGeojsonFeatures:
    """Tests for GeoJSON feature loading and active-feature selection."""

    def test_active_feature_preferred_over_historical(self, tmp_path: Path) -> None:
        """When a region has two features, the one with end_date=null is kept."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "AT-07-01",
                        "start_date": None,
                        "end_date": "2022-10-01",
                    },
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {
                        "id": "AT-07-01",
                        "start_date": "2022-10-01",
                        "end_date": None,
                    },
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]],
                    },
                },
            ],
        }
        path = tmp_path / "test.geojson"
        path.write_text(json.dumps(geojson))
        features = _load_geojson_features(path)
        assert len(features) == 1
        # The active feature (start_date=2022) has coordinates starting at [2, 2].
        first_coord = features["AT-07-01"]["geometry"]["coordinates"][0][0][0]
        assert first_coord == [2, 2]

    def test_single_feature_loaded(self, tmp_path: Path) -> None:
        """A collection with one feature returns that feature."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "AT-07-99",
                        "start_date": None,
                        "end_date": None,
                    },
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]],
                    },
                }
            ],
        }
        path = tmp_path / "test.geojson"
        path.write_text(json.dumps(geojson))
        features = _load_geojson_features(path)
        assert "AT-07-99" in features


# ---------------------------------------------------------------------------
# Tests for _load_bulletin_region_names
# ---------------------------------------------------------------------------


class TestLoadBulletinRegionNames:
    """Tests for extracting region names from a bulletin file."""

    def test_extracts_names_from_regions(self, tmp_path: Path) -> None:
        """Region names are extracted from all bulletin entries."""
        bulletin = {
            "bulletins": [
                {
                    "bulletinID": "b1",
                    "regions": [
                        {"regionID": "AT-07-01", "name": "Allgäu Alps East"},
                        {"regionID": "AT-07-02-01", "name": "Ammergau Alps South"},
                    ],
                },
                {
                    "bulletinID": "b2",
                    "regions": [
                        {"regionID": "IT-32-TN-01", "name": "Adamello - Presanella"},
                    ],
                },
            ]
        }
        path = tmp_path / "bulletin.json"
        path.write_text(json.dumps(bulletin))
        names = _load_bulletin_region_names(path)
        assert names == {
            "AT-07-01": "Allgäu Alps East",
            "AT-07-02-01": "Ammergau Alps South",
            "IT-32-TN-01": "Adamello - Presanella",
        }

    def test_first_occurrence_wins_on_duplicate(self, tmp_path: Path) -> None:
        """When the same region appears in multiple bulletins, the first name wins."""
        bulletin = {
            "bulletins": [
                {
                    "bulletinID": "b1",
                    "regions": [{"regionID": "AT-07-01", "name": "First"}],
                },
                {
                    "bulletinID": "b2",
                    "regions": [{"regionID": "AT-07-01", "name": "Second"}],
                },
            ]
        }
        path = tmp_path / "bulletin.json"
        path.write_text(json.dumps(bulletin))
        names = _load_bulletin_region_names(path)
        assert names["AT-07-01"] == "First"


# ---------------------------------------------------------------------------
# End-to-end test for build_euregio_fixture
# ---------------------------------------------------------------------------


class TestBuildEuregioFixture:
    """End-to-end test for build_euregio_fixture against synthetic inputs."""

    @pytest.fixture()
    def synthetic_files(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        """
        Write minimal synthetic GeoJSON + bulletin files.

        Geometry is four adjacent unit squares arranged in a 2×2 grid:

          A(0,0)   B(1,0)   → top row (AT-07-01, AT-07-02-01)
          C(0,-1)  D(1,-1)  → bottom row (IT-32-BZ-03, IT-32-TN-01)

        The bulletin references all four IDs; A and C are "passthrough" depth,
        B is nested (AT-07-02 intermediate sub), D is passthrough TN.
        """

        def _make_square_feature(
            rid: str, x: float, y: float, active: bool = True
        ) -> dict[str, Any]:
            ring = [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1], [x, y]]
            return {
                "type": "Feature",
                "properties": {
                    "id": rid,
                    "start_date": "2022-10-01" if active else None,
                    "end_date": None if active else "2022-10-01",
                },
                "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
            }

        at07_geojson = {
            "type": "FeatureCollection",
            "features": [
                _make_square_feature("AT-07-01", 0, 1),  # passthrough sub
                _make_square_feature("AT-07-02-01", 1, 1),  # nested sub AT-07-02
            ],
        }
        bz_geojson = {
            "type": "FeatureCollection",
            "features": [
                _make_square_feature("IT-32-BZ-03", 0, 0),  # passthrough sub
            ],
        }
        tn_geojson = {
            "type": "FeatureCollection",
            "features": [
                _make_square_feature("IT-32-TN-01", 1, 0),  # passthrough sub
            ],
        }
        bulletin = {
            "bulletins": [
                {
                    "bulletinID": "test-b1",
                    "regions": [
                        {"regionID": "AT-07-01", "name": "Test AT Region 1"},
                        {"regionID": "AT-07-02-01", "name": "Test AT Region 2 sub"},
                        {"regionID": "IT-32-BZ-03", "name": "Test BZ Region"},
                        {"regionID": "IT-32-TN-01", "name": "Test TN Region"},
                    ],
                }
            ]
        }

        at07_path = tmp_path / "eaws_regions_at-07.geojson"
        bz_path = tmp_path / "eaws_regions_it-32-bz.geojson"
        tn_path = tmp_path / "eaws_regions_it-32-tn.geojson"
        bulletin_path = tmp_path / "EUREGIO_en_CAAMLv6.json"

        at07_path.write_text(json.dumps(at07_geojson))
        bz_path.write_text(json.dumps(bz_geojson))
        tn_path.write_text(json.dumps(tn_geojson))
        bulletin_path.write_text(json.dumps(bulletin))

        return at07_path, bz_path, tn_path, bulletin_path

    def test_fixture_structure(
        self,
        synthetic_files: tuple[Path, Path, Path, Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        build_euregio_fixture writes correct model counts and valid references.

        Asserts:
        * 3 MajorRegion records (AT-07, IT-32-BZ, IT-32-TN)
        * Correct number of SubRegion records (AT-07 passthrough + AT-07-02
          intermediate + IT-32-BZ passthrough + IT-32-TN passthrough = 4)
        * 4 MicroRegion records
        * All subregion→major FK natural keys are valid
        * All microregion→subregion FK natural keys are valid
        """
        import scripts.build_regions_fixture as brf_module

        at07_path, bz_path, tn_path, bulletin_path = synthetic_files
        fixture_path = tmp_path / "eaws_euregio.json"

        # Patch module-level path constants so the function uses our temp files.
        monkeypatch.setattr(brf_module, "GEOJSON_AT07", at07_path)
        monkeypatch.setattr(brf_module, "GEOJSON_IT_BZ", bz_path)
        monkeypatch.setattr(brf_module, "GEOJSON_IT_TN", tn_path)
        monkeypatch.setattr(brf_module, "BULLETIN_PATH", bulletin_path)
        monkeypatch.setattr(brf_module, "FIXTURE_PATH_EUREGIO", fixture_path)

        build_euregio_fixture()

        assert fixture_path.exists()
        records = json.loads(fixture_path.read_text())

        majors = [r for r in records if r["model"] == "regions.majorregion"]
        subs = [r for r in records if r["model"] == "regions.subregion"]
        micros = [r for r in records if r["model"] == "regions.microregion"]

        # Count checks.
        assert len(majors) == 3, f"Expected 3 majors, got {len(majors)}"
        # Subs: AT-07 (passthrough), AT-07-02 (intermediate), IT-32-BZ (passthrough),
        # IT-32-TN (passthrough) = 4 total.
        assert len(subs) == 4, (
            f"Expected 4 subs, got {len(subs)}: {[s['fields']['prefix'] for s in subs]}"
        )
        assert len(micros) == 4, f"Expected 4 micros, got {len(micros)}"

        # Major prefixes.
        major_prefixes = {r["fields"]["prefix"] for r in majors}
        assert major_prefixes == {"AT-07", "IT-32-BZ", "IT-32-TN"}

        # Sub→major references are valid.
        for sub in subs:
            major_ref = sub["fields"]["major"][0]
            assert major_ref in major_prefixes, (
                f"Sub {sub['fields']['prefix']} references unknown major {major_ref!r}"
            )

        # Micro→sub references are valid.
        sub_prefixes = {r["fields"]["prefix"] for r in subs}
        for micro in micros:
            sub_ref = micro["fields"]["subregion"][0]
            assert sub_ref in sub_prefixes, (
                f"Micro {micro['fields']['region_id']} references unknown sub {sub_ref!r}"
            )

        # Each micro has a boundary and a centre.
        for micro in micros:
            assert micro["fields"]["boundary"] is not None
            assert micro["fields"]["centre"] is not None

    def test_missing_bulletin_id_raises(
        self,
        synthetic_files: tuple[Path, Path, Path, Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If a bulletin region ID is not in the GeoJSON, ValueError is raised."""
        import scripts.build_regions_fixture as brf_module

        at07_path, bz_path, tn_path, bulletin_path = synthetic_files

        # Replace bulletin with one that references an ID not in any GeoJSON.
        bad_bulletin = {
            "bulletins": [
                {
                    "bulletinID": "b1",
                    "regions": [{"regionID": "AT-07-99", "name": "Nonexistent Region"}],
                }
            ]
        }
        bulletin_path.write_text(json.dumps(bad_bulletin))

        fixture_path = tmp_path / "eaws_euregio.json"
        monkeypatch.setattr(brf_module, "GEOJSON_AT07", at07_path)
        monkeypatch.setattr(brf_module, "GEOJSON_IT_BZ", bz_path)
        monkeypatch.setattr(brf_module, "GEOJSON_IT_TN", tn_path)
        monkeypatch.setattr(brf_module, "BULLETIN_PATH", bulletin_path)
        monkeypatch.setattr(brf_module, "FIXTURE_PATH_EUREGIO", fixture_path)

        with pytest.raises(ValueError, match="not found in any GeoJSON source"):
            build_euregio_fixture()
