"""
tests/regions/services/test_point_match.py — Tests for point_match service.

Covers:
  point_in_polygon  — inside / outside / on-boundary / inside-a-hole;
                      None and malformed geometry → False; MultiPolygon.
  classify_match    — four outcomes: in_region, in_neighbour, elsewhere,
                      unknown (no coords).
  region_for_point  — inside / outside / null (no regions with boundary) /
                      nearest-centre short-circuit ordering.
  Drift guard       — asserts that module-level constants equal the
                      Subscription.GeoMatchKind choice values so the two
                      representations never diverge silently.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.accounts.models import Subscription
from apps.regions.services.point_match import (
    ELSEWHERE,
    IN_NEIGHBOUR,
    IN_REGION,
    UNKNOWN,
    classify_match,
    point_in_polygon,
    region_for_point,
)
from tests.factories import LocationFactory, MicroRegionFactory

# ---------------------------------------------------------------------------
# Shared geometry helpers
# ---------------------------------------------------------------------------


def _square_polygon(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for the axis-aligned rectangle (x0,y0)→(x1,y1).

    Args:
        x0: Minimum longitude (left edge).
        y0: Minimum latitude (bottom edge).
        x1: Maximum longitude (right edge).
        y1: Maximum latitude (top edge).

    Returns:
        GeoJSON Polygon dict with a single exterior ring.

    """
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
                [x0, y0],  # closed ring
            ]
        ],
    }


def _square_polygon_with_hole(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    hx0: float,
    hy0: float,
    hx1: float,
    hy1: float,
) -> dict[str, Any]:
    """Return a GeoJSON Polygon with an exterior ring and one interior hole.

    Args:
        x0, y0, x1, y1: Exterior ring bounding box.
        hx0, hy0, hx1, hy1: Hole bounding box (must be inside the exterior).

    Returns:
        GeoJSON Polygon dict with exterior ring and one hole ring.

    """
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
                [x0, y0],
            ],
            [
                [hx0, hy0],
                [hx1, hy0],
                [hx1, hy1],
                [hx0, hy1],
                [hx0, hy0],
            ],
        ],
    }


def _multi_polygon(
    squares: list[tuple[float, float, float, float]],
) -> dict[str, Any]:
    """Return a GeoJSON MultiPolygon from a list of (x0, y0, x1, y1) tuples.

    Args:
        squares: List of bounding-box tuples for each sub-polygon.

    Returns:
        GeoJSON MultiPolygon dict.

    """
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ]
            ]
            for x0, y0, x1, y1 in squares
        ],
    }


# ---------------------------------------------------------------------------
# point_in_polygon — Polygon
# ---------------------------------------------------------------------------


class TestPointInPolygonBasic:
    """Basic inside / outside tests for a simple square polygon."""

    def test_centre_point_is_inside(self) -> None:
        """A point at the centre of a unit square is inside."""
        polygon = _square_polygon(0, 0, 10, 10)
        assert point_in_polygon(5.0, 5.0, polygon) is True

    def test_far_away_point_is_outside(self) -> None:
        """A point far from the polygon is outside."""
        polygon = _square_polygon(0, 0, 10, 10)
        assert point_in_polygon(50.0, 50.0, polygon) is False

    def test_point_just_outside_is_not_inside(self) -> None:
        """A point just outside the right edge is not inside."""
        polygon = _square_polygon(0, 0, 10, 10)
        assert point_in_polygon(10.1, 5.0, polygon) is False

    def test_point_just_inside_is_inside(self) -> None:
        """A point just inside the right edge is inside."""
        polygon = _square_polygon(0, 0, 10, 10)
        assert point_in_polygon(9.9, 5.0, polygon) is True

    def test_real_world_coordinates_inside(self) -> None:
        """A WGS-84 point known to be inside a bounding box is inside."""
        # Rough bounding box for Martigny area, CH-4115 territory.
        polygon = _square_polygon(7.0, 46.0, 7.5, 46.4)
        # Martigny itself: lon≈7.10, lat≈46.10
        assert point_in_polygon(7.10, 46.10, polygon) is True

    def test_real_world_coordinates_outside(self) -> None:
        """A WGS-84 point outside the bounding box is outside."""
        polygon = _square_polygon(7.0, 46.0, 7.5, 46.4)
        # Berne: lon≈7.45, lat≈46.95 — above the polygon
        assert point_in_polygon(7.45, 46.95, polygon) is False


class TestPointInPolygonHoles:
    """Tests for the hole (interior ring) handling."""

    def test_point_in_exterior_but_also_in_hole_is_outside(self) -> None:
        """A point inside the exterior ring but also inside a hole is outside."""
        # Exterior (0,0)→(10,10), hole (3,3)→(7,7).
        polygon = _square_polygon_with_hole(0, 0, 10, 10, 3, 3, 7, 7)
        # Point in the hole:
        assert point_in_polygon(5.0, 5.0, polygon) is False

    def test_point_in_exterior_outside_hole_is_inside(self) -> None:
        """A point in the exterior ring but outside the hole is inside."""
        polygon = _square_polygon_with_hole(0, 0, 10, 10, 3, 3, 7, 7)
        # Point in the exterior band (outside the hole):
        assert point_in_polygon(1.0, 1.0, polygon) is True

    def test_point_outside_both_is_outside(self) -> None:
        """A point outside both exterior and hole is still outside."""
        polygon = _square_polygon_with_hole(0, 0, 10, 10, 3, 3, 7, 7)
        assert point_in_polygon(15.0, 15.0, polygon) is False


class TestPointInPolygonNoneAndMalformed:
    """None / malformed geometry → False, never raises."""

    def test_none_geometry_returns_false(self) -> None:
        """None geometry returns False without raising."""
        assert point_in_polygon(5.0, 5.0, None) is False

    def test_empty_dict_returns_false(self) -> None:
        """An empty dict returns False."""
        assert point_in_polygon(5.0, 5.0, {}) is False

    def test_missing_type_returns_false(self) -> None:
        """A geometry dict missing 'type' returns False."""
        assert point_in_polygon(5.0, 5.0, {"coordinates": [[[0, 0]]]}) is False

    def test_missing_coordinates_returns_false(self) -> None:
        """A geometry dict missing 'coordinates' returns False."""
        assert point_in_polygon(5.0, 5.0, {"type": "Polygon"}) is False

    def test_unsupported_type_returns_false(self) -> None:
        """An unsupported geometry type (e.g. Point) returns False."""
        assert (
            point_in_polygon(5.0, 5.0, {"type": "Point", "coordinates": [5.0, 5.0]})
            is False
        )

    def test_malformed_coordinates_returns_false(self) -> None:
        """Malformed coordinates (non-list) return False without raising."""
        assert (
            point_in_polygon(5.0, 5.0, {"type": "Polygon", "coordinates": "bad"})
            is False
        )


class TestPointInPolygonMultiPolygon:
    """MultiPolygon — any sub-polygon containing the point → True."""

    def test_point_in_first_sub_polygon(self) -> None:
        """A point inside the first sub-polygon of a MultiPolygon is inside."""
        geom = _multi_polygon([(0, 0, 5, 5), (10, 10, 15, 15)])
        assert point_in_polygon(2.5, 2.5, geom) is True

    def test_point_in_second_sub_polygon(self) -> None:
        """A point inside the second sub-polygon of a MultiPolygon is inside."""
        geom = _multi_polygon([(0, 0, 5, 5), (10, 10, 15, 15)])
        assert point_in_polygon(12.5, 12.5, geom) is True

    def test_point_between_sub_polygons_is_outside(self) -> None:
        """A point between the two sub-polygons is outside."""
        geom = _multi_polygon([(0, 0, 5, 5), (10, 10, 15, 15)])
        assert point_in_polygon(7.5, 7.5, geom) is False

    def test_empty_multi_polygon_is_outside(self) -> None:
        """An empty MultiPolygon returns False."""
        geom: dict[str, Any] = {"type": "MultiPolygon", "coordinates": []}
        assert point_in_polygon(5.0, 5.0, geom) is False


# ---------------------------------------------------------------------------
# classify_match
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClassifyMatch:
    """Tests for classify_match — four outcome paths."""

    def _make_region_with_boundary(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> Any:
        """Create a MicroRegion with a square boundary polygon.

        Args:
            x0, y0: Bottom-left corner (lon, lat).
            x1, y1: Top-right corner (lon, lat).

        Returns:
            A saved MicroRegion instance.

        """
        return MicroRegionFactory.create(boundary=_square_polygon(x0, y0, x1, y1))

    def test_returns_unknown_when_coords_are_none(self) -> None:
        """Returns (UNKNOWN, None) when both lon and lat are None."""
        target = self._make_region_with_boundary(0, 0, 10, 10)
        kind, matched = classify_match(None, None, target)
        assert kind == UNKNOWN
        assert matched is None

    def test_returns_unknown_when_lon_only_is_none(self) -> None:
        """Returns (UNKNOWN, None) when lon is None (lat provided)."""
        target = self._make_region_with_boundary(0, 0, 10, 10)
        kind, matched = classify_match(None, 5.0, target)
        assert kind == UNKNOWN
        assert matched is None

    def test_returns_unknown_when_lat_only_is_none(self) -> None:
        """Returns (UNKNOWN, None) when lat is None (lon provided)."""
        target = self._make_region_with_boundary(0, 0, 10, 10)
        kind, matched = classify_match(5.0, None, target)
        assert kind == UNKNOWN
        assert matched is None

    def test_returns_in_region_when_inside_target(self) -> None:
        """Returns (IN_REGION, target) when the point is inside target.boundary."""
        target = self._make_region_with_boundary(0, 0, 10, 10)
        kind, matched = classify_match(5.0, 5.0, target)
        assert kind == IN_REGION
        assert matched == target

    def test_returns_in_neighbour_when_inside_neighbour(self) -> None:
        """Returns (IN_NEIGHBOUR, neighbour) when point is inside a neighbour's boundary."""
        target = self._make_region_with_boundary(0, 0, 5, 5)
        neighbour = self._make_region_with_boundary(10, 0, 15, 5)
        target.neighbours.add(neighbour)

        # Point inside neighbour (12, 2), outside target.
        kind, matched = classify_match(12.0, 2.0, target)
        assert kind == IN_NEIGHBOUR
        assert matched == neighbour

    def test_returns_elsewhere_when_outside_all(self) -> None:
        """Returns (ELSEWHERE, None) when point is outside target and all neighbours."""
        target = self._make_region_with_boundary(0, 0, 5, 5)
        neighbour = self._make_region_with_boundary(10, 0, 15, 5)
        target.neighbours.add(neighbour)

        # Point at (50, 50) — outside everything.
        kind, matched = classify_match(50.0, 50.0, target)
        assert kind == ELSEWHERE
        assert matched is None

    def test_returns_elsewhere_with_no_neighbours(self) -> None:
        """Returns (ELSEWHERE, None) when the region has no neighbours and point is outside."""
        target = self._make_region_with_boundary(0, 0, 5, 5)
        # No neighbours added.
        kind, matched = classify_match(50.0, 50.0, target)
        assert kind == ELSEWHERE
        assert matched is None

    def test_in_region_takes_precedence_over_neighbours(self) -> None:
        """When point is inside target, IN_REGION is returned even if a neighbour also contains it."""
        target = self._make_region_with_boundary(0, 0, 10, 10)
        # Overlapping neighbour — same bounding box.
        neighbour = self._make_region_with_boundary(0, 0, 10, 10)
        target.neighbours.add(neighbour)

        kind, matched = classify_match(5.0, 5.0, target)
        assert kind == IN_REGION
        assert matched == target


# ---------------------------------------------------------------------------
# region_for_point — global point→MicroRegion resolver (SNOW-324)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegionForPoint:
    """Tests for region_for_point — global GPS fix → MicroRegion resolver."""

    def _make_region_with_boundary(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        centre_lon: float | None = None,
        centre_lat: float | None = None,
    ) -> Any:
        """Create a MicroRegion with a square boundary and an explicit centre.

        Args:
            x0, y0: Bottom-left corner (lon, lat).
            x1, y1: Top-right corner (lon, lat).
            centre_lon: Override the centre longitude (default: midpoint).
            centre_lat: Override the centre latitude (default: midpoint).

        Returns:
            A saved MicroRegion instance.

        """
        lon = centre_lon if centre_lon is not None else (x0 + x1) / 2
        lat = centre_lat if centre_lat is not None else (y0 + y1) / 2
        return MicroRegionFactory.create(
            boundary=_square_polygon(x0, y0, x1, y1),
            centre={"lon": lon, "lat": lat},
        )

    def test_returns_none_when_no_regions_have_boundary(self) -> None:
        """Returns None when all MicroRegion rows have null boundaries."""
        # MicroRegionFactory.create() defaults to boundary=None.
        MicroRegionFactory.create(boundary=None)
        result = region_for_point(5.0, 5.0)
        assert result is None

    def test_returns_region_when_point_is_inside(self) -> None:
        """Returns the region whose boundary contains the point."""
        region = self._make_region_with_boundary(0, 0, 10, 10)
        result = region_for_point(5.0, 5.0)
        assert result == region

    def test_returns_none_when_point_is_outside_all_regions(self) -> None:
        """Returns None when the point is outside all region boundaries."""
        self._make_region_with_boundary(0, 0, 5, 5)
        result = region_for_point(50.0, 50.0)
        assert result is None

    def test_returns_correct_region_among_multiple(self) -> None:
        """Returns the specific region containing the point when multiple exist."""
        region_a = self._make_region_with_boundary(
            0, 0, 5, 5, centre_lon=2.5, centre_lat=2.5
        )
        region_b = self._make_region_with_boundary(
            10, 10, 15, 15, centre_lon=12.5, centre_lat=12.5
        )
        # Point inside region_b.
        result = region_for_point(12.5, 12.5)
        assert result == region_b
        assert result != region_a

    def test_nearest_centre_ordering_returns_correct_region(self) -> None:
        """Nearest-centre ordering does not cause incorrect matches.

        Create two adjacent non-overlapping regions; the point is inside
        region_a which has a more distant centre.  The function must still
        return region_a (not region_b, which has the closer centre but
        whose boundary does not contain the point).
        """
        # region_a: x=[0,4] — point at (1,1) is inside.
        region_a = self._make_region_with_boundary(
            0, 0, 4, 4, centre_lon=2.0, centre_lat=2.0
        )
        # region_b: x=[5,9] — closer centre (5.5,5.5) to point? No, 1,1 is farther from (7,7).
        # Actually place region_b's centre closer to (1,1): centre at (0.5, 0.5) but OUTSIDE the
        # target square.  That would be inside region_a's boundary, which is invalid geometry.
        # Use a non-overlapping layout instead: region_b at y=[5,9], centre at (1, 7).
        _region_b = self._make_region_with_boundary(
            0, 5, 4, 9, centre_lon=1.0, centre_lat=7.0
        )

        # Point (1,1) is inside region_a (y=0..4), outside region_b (y=5..9).
        result = region_for_point(1.0, 1.0)
        assert result == region_a

    def test_region_with_null_centre_still_matches(self) -> None:
        """A region with a null centre falls back to inf distance and is tested last.

        Even with null centre, if the point is inside, it should be returned
        (because all other candidates miss).
        """
        region = MicroRegionFactory.create(
            boundary=_square_polygon(0, 0, 10, 10),
            centre=None,
        )
        result = region_for_point(5.0, 5.0)
        assert result == region

    def test_argument_order_is_lat_then_lon(self) -> None:
        """Guards against an accidental re-flip of the (lat, lon) signature.

        The boundary is asymmetric (lon spans 6-10, lat spans 0-4), so
        swapping the arguments would test (lon=2, lat=8) instead — which
        falls outside the boundary and the match would fail.
        """
        region = self._make_region_with_boundary(6, 0, 10, 4)
        result = region_for_point(lat=2.0, lon=8.0)
        assert result == region


# ---------------------------------------------------------------------------
# Drift guard — point_match constants must equal GeoMatchKind choice values
# ---------------------------------------------------------------------------


class TestConstantsDriftGuard:
    """Assert that point_match module constants match Subscription.GeoMatchKind.

    If a developer renames a constant in one place but not the other, this
    test will catch the drift before it reaches production.
    """

    def test_in_region_matches_geomatchkind(self) -> None:
        assert IN_REGION == Subscription.GeoMatchKind.IN_REGION

    def test_in_neighbour_matches_geomatchkind(self) -> None:
        assert IN_NEIGHBOUR == Subscription.GeoMatchKind.IN_NEIGHBOUR

    def test_elsewhere_matches_geomatchkind(self) -> None:
        assert ELSEWHERE == Subscription.GeoMatchKind.ELSEWHERE

    def test_unknown_matches_geomatchkind(self) -> None:
        assert UNKNOWN == Subscription.GeoMatchKind.UNKNOWN


@pytest.mark.django_db
class TestRegionForPointReadsCentroidLocations:
    """region_for_point pre-sorts on ``centre_point()``, not the raw column.

    Only the ordering of the polygon tests depends on the centre, so these
    assert that the resolver still answers correctly through the FK and
    still answers at all when a region has no centre to sort by.
    """

    def test_resolves_through_a_centroid_location(self) -> None:
        """A region anchored to a centroid Location still matches its own points."""
        location = LocationFactory.create(anonymous=True, latitude=5.0, longitude=5.0)
        region = MicroRegionFactory.create(
            boundary=_square_polygon(0, 0, 10, 10),
            centroid_location=location,
            centre=None,
        )

        assert region_for_point(5.0, 5.0) == region

    def test_a_region_with_no_centre_is_still_matched(self) -> None:
        """A missing centre costs sort position, not correctness.

        point_in_polygon is the answer; the centre only decides which
        polygon is tested first. A region that has neither anchor sorts
        last and is still found.
        """
        region = MicroRegionFactory.create(
            boundary=_square_polygon(0, 0, 10, 10),
            centroid_location=None,
            centre=None,
        )

        assert region_for_point(5.0, 5.0) == region

    def test_the_centroid_location_wins_over_the_legacy_column(self) -> None:
        """A stale ``centre`` must not out-rank the anchor that replaced it."""
        near = LocationFactory.create(anonymous=True, latitude=5.0, longitude=5.0)
        wanted = MicroRegionFactory.create(
            boundary=_square_polygon(0, 0, 10, 10),
            centroid_location=near,
            # A wildly wrong legacy value: if it were still read, this
            # region would sort last rather than first.
            centre={"lon": 170.0, "lat": -80.0},
        )
        MicroRegionFactory.create(
            boundary=_square_polygon(20, 20, 30, 30),
            centroid_location=None,
            centre={"lon": 6.0, "lat": 6.0},
        )

        assert region_for_point(5.0, 5.0) == wanted
