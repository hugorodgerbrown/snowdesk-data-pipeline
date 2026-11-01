"""
tests/regions/services/test_track_regions.py — the regions a track crosses (SNOW-945).

Covers ``resolve_track_regions`` (the walk: one region, a crossing in
crossing order, A → B → A, outside coverage, a corner cut found only by
the stride, a one-point track, an empty one) and ``track_regions`` (stored
order kept, an unknown id dropped, a null resolved live without writing,
one query on the stored path).

The map: region A is the square 7.0–7.5 E × 46.0–46.5 N, region B the
square east of it, 7.5–8.0 E. The corner-cut test builds its own three
regions, with a 200 m sliver between two squares.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.regions.models import MicroRegion
from apps.regions.services.track_regions import (
    REGION_STRIDE_M,
    resolve_track_regions,
    track_regions,
)
from tests.factories import MicroRegionFactory


def _box(west: float, south: float, east: float, north: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for a lon/lat rectangle."""
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]],
        ],
    }


@pytest.fixture
def regions() -> dict[str, MicroRegion]:
    """Two adjacent square regions, A west of B."""
    return {
        "A": MicroRegionFactory.create(
            region_id="CH-1111", boundary=_box(7.0, 46.0, 7.5, 46.5)
        ),
        "B": MicroRegionFactory.create(
            region_id="CH-2222", boundary=_box(7.5, 46.0, 8.0, 46.5)
        ),
    }


@pytest.mark.django_db
class TestResolveTrackRegions:
    """``resolve_track_regions`` — the walk."""

    def test_one_region(self, regions: dict[str, MicroRegion]) -> None:
        """A track inside A crosses A alone."""
        assert resolve_track_regions([[7.1, 46.1, 0.0], [7.2, 46.2, 0.0]]) == [
            "CH-1111"
        ]

    def test_a_crossing_is_in_crossing_order(
        self, regions: dict[str, MicroRegion]
    ) -> None:
        """East-bound gives A then B; west-bound gives B then A."""
        east = [[7.4, 46.2, 0.0], [7.6, 46.2, 0.0]]
        assert resolve_track_regions(east) == ["CH-1111", "CH-2222"]
        assert resolve_track_regions(list(reversed(east))) == ["CH-2222", "CH-1111"]

    def test_a_return_is_not_a_second_entry(
        self, regions: dict[str, MicroRegion]
    ) -> None:
        """A → B → A is ``[A, B]``."""
        track = [[7.4, 46.2, 0.0], [7.6, 46.2, 0.0], [7.4, 46.3, 0.0]]
        assert resolve_track_regions(track) == ["CH-1111", "CH-2222"]

    def test_outside_coverage_is_empty(self, regions: dict[str, MicroRegion]) -> None:
        """No region is an answer about coverage."""
        assert resolve_track_regions([[9.0, 47.0, 0.0], [9.1, 47.1, 0.0]]) == []

    def test_a_corner_cut_is_found_by_the_stride(self) -> None:
        """A straight leg with both vertices in A still finds the region it cuts.

        Region C is a 0.002° sliver (about 200 m) the leg passes through
        midway; neither vertex is inside it, so only the densified walk
        finds it.
        """
        MicroRegionFactory.create(
            region_id="CH-1111", boundary=_box(7.0, 46.0, 7.5, 46.5)
        )
        MicroRegionFactory.create(
            region_id="CH-3333", boundary=_box(7.5, 46.0, 7.502, 46.5)
        )
        MicroRegionFactory.create(
            region_id="CH-4444", boundary=_box(7.502, 46.0, 8.0, 46.5)
        )
        leg = [[7.45, 46.2, 0.0], [7.55, 46.2, 0.0]]

        assert REGION_STRIDE_M < 150
        assert resolve_track_regions(leg) == ["CH-1111", "CH-3333", "CH-4444"]

    def test_a_one_point_track_uses_its_point(
        self, regions: dict[str, MicroRegion]
    ) -> None:
        """``stride_coordinates`` returns nothing for one point; the resolver doesn't."""
        assert resolve_track_regions([[7.6, 46.2, 0.0]]) == ["CH-2222"]

    def test_an_empty_track_crosses_nothing(self) -> None:
        """No points, or None, is an empty list."""
        assert resolve_track_regions([]) == []
        assert resolve_track_regions(None) == []


@pytest.mark.django_db
class TestTrackRegions:
    """``track_regions`` — the stored list back as rows."""

    def test_stored_order_is_kept(self, regions: dict[str, MicroRegion]) -> None:
        """The list's order, not the table's."""
        assert track_regions(["CH-2222", "CH-1111"], None) == [
            regions["B"],
            regions["A"],
        ]

    def test_an_unknown_id_is_dropped(self, regions: dict[str, MicroRegion]) -> None:
        """A region gone since (a boundary change) is left out."""
        assert track_regions(["CH-9999", "CH-1111"], None) == [regions["A"]]

    def test_null_resolves_live_without_writing(
        self, regions: dict[str, MicroRegion]
    ) -> None:
        """Before the backfill the page is still right."""
        track = [[7.4, 46.2, 0.0], [7.6, 46.2, 0.0]]
        assert track_regions(None, track) == [regions["A"], regions["B"]]

    def test_empty_stored_asks_nothing(self, django_assert_num_queries: Any) -> None:
        """Outside coverage, already known: no query."""
        with django_assert_num_queries(0):
            assert track_regions([], [[7.1, 46.1, 0.0]]) == []

    def test_one_query_on_the_stored_path(
        self, regions: dict[str, MicroRegion], django_assert_num_queries: Any
    ) -> None:
        """The stored path is a single lookup."""
        with django_assert_num_queries(1):
            track_regions(["CH-1111", "CH-2222"], None)
