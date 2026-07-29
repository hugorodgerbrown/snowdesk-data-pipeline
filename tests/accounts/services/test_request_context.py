"""
tests/accounts/services/test_request_context.py — Tests for geo_match_snapshot.

Covers ``geo_match_snapshot`` across all four classify_match outcomes:
  - in_region: req_log has coords inside the target region's boundary.
  - in_neighbour: coords inside a neighbouring region's boundary.
  - elsewhere: coords outside target and all neighbours.
  - unknown: req_log has no lat/lon.

Plus a SNOW-558 regression guard that the debug log line never emits the
subscriber's coordinates in clear text.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from apps.accounts.services.request_context import geo_match_snapshot
from apps.regions.services.point_match import (
    ELSEWHERE,
    IN_NEIGHBOUR,
    IN_REGION,
    UNKNOWN,
)
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
    """Tests for apps.accounts.services.request_context.geo_match_snapshot."""

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


@pytest.mark.django_db
class TestGeoMatchSnapshotLogging:
    """SNOW-558: the debug log line must never carry the raw coordinates.

    An IP-derived lat/lon is personal data, and CodeQL flags it under
    ``py/clear-text-logging-sensitive-data``. The line still has to say enough
    to debug a mis-classification, so it keeps the region ids and the outcome
    and reports only whether coordinates were present.
    """

    @pytest.fixture(autouse=True)
    def _propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Let caplog see records from the accounts logger hierarchy."""
        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

    def test_coordinates_absent_from_log_output(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No log record contains either coordinate value."""
        region = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 10, 10))
        req_log = RequestLogFactory.create(longitude=5.25, latitude=6.75)

        with caplog.at_level(logging.DEBUG, logger="accounts.services.request_context"):
            geo_match_snapshot(req_log, region)

        messages = [record.getMessage() for record in caplog.records]
        assert messages, "expected at least one debug record"
        for message in messages:
            assert "5.25" not in message, f"longitude leaked into log: {message!r}"
            assert "6.75" not in message, f"latitude leaked into log: {message!r}"

    def test_log_reports_coords_present(self, caplog: pytest.LogCaptureFixture) -> None:
        """The line still records that coordinates were available."""
        region = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 10, 10))
        req_log = RequestLogFactory.create(longitude=5.25, latitude=6.75)

        with caplog.at_level(logging.DEBUG, logger="accounts.services.request_context"):
            geo_match_snapshot(req_log, region)

        assert any("coords=present" in r.getMessage() for r in caplog.records)

    def test_log_reports_coords_absent(self, caplog: pytest.LogCaptureFixture) -> None:
        """A request log with no coordinates is reported as absent."""
        region = MicroRegionFactory.create(boundary=_square_polygon(0, 0, 10, 10))
        req_log = RequestLogFactory.create(longitude=None, latitude=None)

        with caplog.at_level(logging.DEBUG, logger="accounts.services.request_context"):
            geo_match_snapshot(req_log, region)

        assert any("coords=absent" in r.getMessage() for r in caplog.records)
