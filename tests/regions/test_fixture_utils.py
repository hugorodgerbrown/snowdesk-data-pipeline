"""
tests/regions/test_fixture_utils.py — Unit tests for regions/fixture_utils.py.

Covers the helper functions shared across the fixture-build commands
(build_switzerland_fixture, build_france_fixture, build_austria_fixture,
build_italy_fixture):

* build_entries_from_eaws_dir
* bbox_from_children
* centre_from_children
* boundary_from_children
* centre_from_bbox
* _iter_coords_from_geometry
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# build_entries_from_eaws_dir
# ---------------------------------------------------------------------------


def _write_eaws_file(
    directory: Path, code: str, features: list[dict[str, Any]]
) -> None:
    """Write a minimal EAWS-style GeoJSON file for ``code`` into ``directory``."""
    payload = {"type": "FeatureCollection", "features": features}
    (directory / f"{code}_micro-regions.geojson.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestBuildEntriesFromEawsDir:
    """Tests for build_entries_from_eaws_dir."""

    def test_assembles_l1_l2_l4_in_order_with_l4_sorted(self, tmp_path: Path) -> None:
        """Output is L1 entries, then L2 entries, then L4 entries sorted by region_id."""
        from regions.fixture_utils import build_entries_from_eaws_dir

        _write_eaws_file(tmp_path, "AT-02", [{"id": "AT-02-01"}])

        def build_code_entries(
            code: str,
            features: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
            l1 = {"model": "regions.majorregion", "fields": {"prefix": code}}
            l2s = {
                f"{code}-01": {
                    "model": "regions.subregion",
                    "fields": {"prefix": f"{code}-01"},
                }
            }
            l4s = [
                {
                    "model": "regions.microregion",
                    "fields": {"region_id": f"{code}-01-B"},
                },
                {
                    "model": "regions.microregion",
                    "fields": {"region_id": f"{code}-01-A"},
                },
            ]
            return l1, l2s, l4s

        entries = build_entries_from_eaws_dir(
            tmp_path, ["AT-02"], build_code_entries, "test_command"
        )

        assert [e["model"] for e in entries] == [
            "regions.majorregion",
            "regions.subregion",
            "regions.microregion",
            "regions.microregion",
        ]
        # L4 entries are sorted by region_id ascending.
        assert entries[2]["fields"]["region_id"] == "AT-02-01-A"
        assert entries[3]["fields"]["region_id"] == "AT-02-01-B"

    def test_missing_source_file_is_skipped_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A code with no matching file logs a warning and is skipped, not raised."""
        from regions import fixture_utils
        from regions.fixture_utils import build_entries_from_eaws_dir

        _write_eaws_file(tmp_path, "AT-02", [])

        warnings: list[tuple[str, tuple[Any, ...]]] = []
        monkeypatch.setattr(
            fixture_utils.logger,
            "warning",
            lambda msg, *args: warnings.append((msg, args)),
        )

        def build_code_entries(
            code: str,
            features: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
            return (
                {"model": "regions.majorregion", "fields": {"prefix": code}},
                {},
                [],
            )

        entries = build_entries_from_eaws_dir(
            tmp_path,
            ["AT-02", "AT-99"],
            build_code_entries,
            "build_austria_fixture",
        )

        # Only the present code produced an entry — the missing one was skipped.
        assert len(entries) == 1
        assert entries[0]["fields"]["prefix"] == "AT-02"
        # The command_name parameter and missing code appear in the warning args.
        assert len(warnings) == 1
        _, args = warnings[0]
        assert "build_austria_fixture" in args
        assert any("AT-99" in str(a) for a in args)

    def test_empty_region_codes_returns_empty_list(self, tmp_path: Path) -> None:
        """No region codes means no entries — and no callable invocations."""
        from regions.fixture_utils import build_entries_from_eaws_dir

        invocations: list[str] = []

        def build_code_entries(
            code: str,
            features: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
            invocations.append(code)
            return ({}, {}, [])

        entries = build_entries_from_eaws_dir(
            tmp_path, [], build_code_entries, "test_command"
        )

        assert entries == []
        assert invocations == []


# ---------------------------------------------------------------------------
# _iter_coords_from_geometry
# ---------------------------------------------------------------------------


class TestIterCoordsFromGeometry:
    """Tests for the geometry coordinate extractor."""

    def test_polygon_yields_all_ring_coords(self) -> None:
        """All vertices from every ring in a Polygon are returned."""
        from regions.fixture_utils import _iter_coords_from_geometry

        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
            ],
        }
        coords = _iter_coords_from_geometry(geometry)
        assert (0.0, 0.0) in coords
        assert (1.0, 1.0) in coords
        assert len(coords) == 5

    def test_polygon_with_hole_yields_all_rings(self) -> None:
        """Outer ring and hole ring vertices are both included."""
        from regions.fixture_utils import _iter_coords_from_geometry

        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
                [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]],
            ],
        }
        coords = _iter_coords_from_geometry(geometry)
        # Outer ring has 5 vertices; hole has 5 vertices.
        assert len(coords) == 10

    def test_multipolygon_yields_all_member_coords(self) -> None:
        """Vertices from every member polygon are returned."""
        from regions.fixture_utils import _iter_coords_from_geometry

        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
                [[[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 3.0], [2.0, 2.0]]],
            ],
        }
        coords = _iter_coords_from_geometry(geometry)
        assert (0.0, 0.0) in coords
        assert (3.0, 3.0) in coords
        assert len(coords) == 10

    def test_strips_altitude_from_3d_coords(self) -> None:
        """3D positions are reduced to (lon, lat) — altitude is dropped."""
        from regions.fixture_utils import _iter_coords_from_geometry

        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[6.0, 46.0, 0.0], [7.0, 46.0, 0.0], [7.0, 47.0, 0.0]],
            ],
        }
        coords = _iter_coords_from_geometry(geometry)
        assert all(len(c) == 2 for c in coords)
        assert (6.0, 46.0) in coords

    def test_unsupported_type_raises_value_error(self) -> None:
        """An unsupported geometry type raises ValueError."""
        from regions.fixture_utils import _iter_coords_from_geometry

        with pytest.raises(ValueError, match="Unsupported geometry type"):
            _iter_coords_from_geometry({"type": "Point", "coordinates": [6.0, 46.0]})


# ---------------------------------------------------------------------------
# bbox_from_children
# ---------------------------------------------------------------------------


class TestBboxFromChildren:
    """Tests for bbox_from_children."""

    def test_single_child_returns_child_bbox(self) -> None:
        """A list with one child returns that child's bbox."""
        from regions.fixture_utils import bbox_from_children

        child = {
            "boundary": {
                "type": "Polygon",
                "coordinates": [
                    [[6.0, 46.0], [7.0, 46.0], [7.0, 47.0], [6.0, 47.0], [6.0, 46.0]],
                ],
            }
        }
        result = bbox_from_children([child])
        assert result == [6.0, 46.0, 7.0, 47.0]

    def test_multiple_children_returns_enclosing_bbox(self) -> None:
        """Multiple children produce a bbox that spans all of them."""
        from regions.fixture_utils import bbox_from_children

        child_a = {
            "boundary": {
                "type": "Polygon",
                "coordinates": [
                    [[6.0, 46.0], [7.0, 46.0], [7.0, 47.0], [6.0, 47.0], [6.0, 46.0]],
                ],
            }
        }
        child_b = {
            "boundary": {
                "type": "Polygon",
                "coordinates": [
                    [[8.0, 45.0], [9.0, 45.0], [9.0, 46.0], [8.0, 46.0], [8.0, 45.0]],
                ],
            }
        }
        result = bbox_from_children([child_a, child_b])
        assert result == [6.0, 45.0, 9.0, 47.0]

    def test_children_without_boundary_are_skipped(self) -> None:
        """Children with no boundary key do not contribute to the bbox."""
        from regions.fixture_utils import bbox_from_children

        child_with = {
            "boundary": {
                "type": "Polygon",
                "coordinates": [
                    [[6.0, 46.0], [7.0, 46.0], [7.0, 47.0], [6.0, 47.0], [6.0, 46.0]],
                ],
            }
        }
        child_without = {"boundary": None}
        result = bbox_from_children([child_with, child_without])
        assert result == [6.0, 46.0, 7.0, 47.0]


# ---------------------------------------------------------------------------
# centre_from_children
# ---------------------------------------------------------------------------


class TestCentreFromChildren:
    """Tests for centre_from_children."""

    def test_single_child_returns_that_centre(self) -> None:
        """A single child with a centre returns that centre unchanged."""
        from regions.fixture_utils import centre_from_children

        child = {"centre": {"lon": 6.5, "lat": 46.5}}
        result = centre_from_children([child])
        assert result == {"lon": 6.5, "lat": 46.5}

    def test_multiple_children_returns_arithmetic_mean(self) -> None:
        """The result is the arithmetic mean of all children's centres."""
        from regions.fixture_utils import centre_from_children

        children = [
            {"centre": {"lon": 6.0, "lat": 46.0}},
            {"centre": {"lon": 8.0, "lat": 48.0}},
        ]
        result = centre_from_children(children)
        assert result["lon"] == pytest.approx(7.0)
        assert result["lat"] == pytest.approx(47.0)

    def test_children_without_centre_are_skipped(self) -> None:
        """Children without a centre key are excluded from the average."""
        from regions.fixture_utils import centre_from_children

        children: list[dict[str, Any]] = [
            {"centre": {"lon": 6.0, "lat": 46.0}},
            {"centre": None},
            {"other_key": "value"},
        ]
        result = centre_from_children(children)
        assert result["lon"] == pytest.approx(6.0)
        assert result["lat"] == pytest.approx(46.0)

    def test_empty_list_raises_value_error(self) -> None:
        """An empty children list raises ValueError rather than ZeroDivisionError."""
        from regions.fixture_utils import centre_from_children

        with pytest.raises(ValueError, match="empty children list"):
            centre_from_children([])

    def test_all_children_lack_centre_raises_value_error(self) -> None:
        """A list where no child has a centre raises ValueError."""
        from regions.fixture_utils import centre_from_children

        with pytest.raises(ValueError, match="empty children list"):
            centre_from_children([{"boundary": {}}, {"boundary": {}}])


# ---------------------------------------------------------------------------
# centre_from_bbox
# ---------------------------------------------------------------------------


class TestCentreFromBbox:
    """Tests for centre_from_bbox."""

    def test_returns_midpoint_of_polygon(self) -> None:
        """Returns the bbox midpoint of a simple square Polygon."""
        from regions.fixture_utils import centre_from_bbox

        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[6.0, 46.0], [8.0, 46.0], [8.0, 48.0], [6.0, 48.0], [6.0, 46.0]],
            ],
        }
        result = centre_from_bbox(geometry)
        assert result["lon"] == pytest.approx(7.0)
        assert result["lat"] == pytest.approx(47.0)

    def test_returns_midpoint_of_multipolygon(self) -> None:
        """Returns the bbox midpoint spanning all members of a MultiPolygon."""
        from regions.fixture_utils import centre_from_bbox

        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[6.0, 46.0], [7.0, 46.0], [7.0, 47.0], [6.0, 47.0], [6.0, 46.0]]],
                [[[8.0, 47.0], [9.0, 47.0], [9.0, 48.0], [8.0, 48.0], [8.0, 47.0]]],
            ],
        }
        result = centre_from_bbox(geometry)
        # Enclosing bbox is (6, 46, 9, 48); midpoint is (7.5, 47.0).
        assert result["lon"] == pytest.approx(7.5)
        assert result["lat"] == pytest.approx(47.0)


# ---------------------------------------------------------------------------
# boundary_from_children
# ---------------------------------------------------------------------------


class TestBoundaryFromChildren:
    """Tests for boundary_from_children."""

    def test_returns_multipolygon_covering_children(self) -> None:
        """The union of child boundaries is returned as a GeoJSON geometry."""
        from regions.fixture_utils import boundary_from_children

        children = [
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [6.0, 46.0],
                            [7.0, 46.0],
                            [7.0, 47.0],
                            [6.0, 47.0],
                            [6.0, 46.0],
                        ],
                    ],
                }
            },
            {
                "boundary": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [7.0, 46.0],
                            [8.0, 46.0],
                            [8.0, 47.0],
                            [7.0, 47.0],
                            [7.0, 46.0],
                        ],
                    ],
                }
            },
        ]
        result = boundary_from_children(children)
        # Shapely merges two adjacent squares into a single Polygon.
        assert result["type"] in ("Polygon", "MultiPolygon")
        assert "coordinates" in result
