"""
tests/subscriptions/services/test_request_context.py — Tests for geo_match_snapshot.

Covers ``geo_match_snapshot`` across all four classify_match outcomes:
  - in_region: req_log has coords inside the target region's boundary.
  - in_neighbour: coords inside a neighbouring region's boundary.
  - elsewhere: coords outside target and all neighbours.
  - unknown: req_log has no lat/lon.
"""

from __future__ import annotations

from typing import Any

import pytest

from regions.services.point_match import ELSEWHERE, IN_NEIGHBOUR, IN_REGION, UNKNOWN
from subscriptions.services.request_context import geo_match_snapshot
from tests.factories import MicroRegionFactory, RequestLogFactory


def _square_polygon(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for the rectangle (x0, y0) → (x1, y1)."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
                [x0, y0],
            ]
        ],
    }


@pytest.mark.django_db
class TestGeoMatchSnapshot:
    """Tests for subscriptions.services.request_context.geo_match_snapshot."""

    def test_in_region(self) -> None:
        """Returns geo_match_kind=IN_REGION when coords are inside the target."""
        region = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 10, 10))
        req_log = RequestLogFactory.create(longitude=5.0, latitude=5.0)

        result = geo_match_snapshot(req_log, region)

        assert result["geo_match_kind"] == IN_REGION
        assert result["geo_matched_region"] == region

    def test_in_neighbour(self) -> None:
        """Returns geo_match_kind=IN_NEIGHBOUR when coords are inside a neighbour."""
        target = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 5, 5))
        neighbour = MicroRegionFactory.create(boundary=_square_polygon(10, 0, 15, 5))
        target.neighbours.add(neighbour)

        req_log = RequestLogFactory.create(longitude=12.0, latitude=2.0)
        result = geo_match_snapshot(req_log, target)

        assert result["geo_match_kind"] == IN_NEIGHBOUR
        assert result["geo_matched_region"] == neighbour

    def test_elsewhere(self) -> None:
        """Returns geo_match_kind=ELSEWHERE when coords are outside all regions."""
        target = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 5, 5))
        req_log = RequestLogFactory.create(longitude=50.0, latitude=50.0)

        result = geo_match_snapshot(req_log, target)

        assert result["geo_match_kind"] == ELSEWHERE
        assert result["geo_matched_region"] is None

    def test_unknown_no_coords(self) -> None:
        """Returns geo_match_kind=UNKNOWN when req_log has no lat/lon."""
        target = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 5, 5))
        req_log = RequestLogFactory.create(longitude=None, latitude=None)

        result = geo_match_snapshot(req_log, target)

        assert result["geo_match_kind"] == UNKNOWN
        assert result["geo_matched_region"] is None

    def test_unknown_no_lon(self) -> None:
        """Returns geo_match_kind=UNKNOWN when only lon is absent."""
        target = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 5, 5))
        req_log = RequestLogFactory.create(longitude=None, latitude=5.0)

        result = geo_match_snapshot(req_log, target)

        assert result["geo_match_kind"] == UNKNOWN
        assert result["geo_matched_region"] is None

    def test_unknown_no_lat(self) -> None:
        """Returns geo_match_kind=UNKNOWN when only lat is absent."""
        target = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 5, 5))
        req_log = RequestLogFactory.create(longitude=5.0, latitude=None)

        result = geo_match_snapshot(req_log, target)

        assert result["geo_match_kind"] == UNKNOWN
        assert result["geo_matched_region"] is None

    def test_result_keys_are_correct(self) -> None:
        """The returned dict always has exactly the two expected keys."""
        target = MicroRegionFactory.create(boundary=None)
        req_log = RequestLogFactory.create(longitude=None, latitude=None)

        result = geo_match_snapshot(req_log, target)

        assert set(result.keys()) == {"geo_match_kind", "geo_matched_region"}
