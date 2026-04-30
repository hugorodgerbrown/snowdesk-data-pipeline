"""
tests/scripts/test_build_regions_fixture.py — Tests for the build_regions_fixture script.

Covers the neighbour-graph computation: the contract is that two regions
are reported as neighbours when one polygon, expanded by ``eps`` degrees,
intersects the other. This absorbs the sub-metre float gaps that show up
between independently-digitised cantonal polygons (where strict
``touches()`` would falsely report a near-miss).
"""

from typing import Any

from scripts.build_regions_fixture import (
    NEIGHBOUR_EPS_DEGREES,
    _compute_neighbour_graph,
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
