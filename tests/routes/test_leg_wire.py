"""
tests/routes/test_leg_wire.py — a route's legs in slope-sample indices (SNOW-1018).

The assertion this module exists for is the coverage invariant on every
canonical track: the first leg starts at segment 0, the last ends at
segment N − 1, and each leg starts one past the previous one's end. A
point index leaking through in place of a sample index fails it at once,
because the canonical tracks hold several times more points than 25 m
segments — the last ``to`` would run off the end of ``angles``.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.routes.services.canonical import canonical_documents
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.leg_wire import wire_legs
from apps.routes.services.legs import Leg, detect_legs
from apps.routes.services.slope_segments import (
    SAMPLE_STRIDE_M,
    cumulative_distances,
    stride_coordinates,
)

CANONICAL = sorted(name for name, _ in canonical_documents())


def _track(filename: str) -> list[list[float | None]]:
    """Return one canonical track's parsed points.

    Args:
        filename: The fixture's filename.

    Returns:
        ``[lon, lat, ele]`` coordinates.

    """
    return parse_gpx(dict(canonical_documents())[filename]).points


def _record_for(points: list[list[float | None]], **overrides: Any) -> dict[str, Any]:
    """Build a slope record the sampler would have written for ``points``.

    The coordinates come from the sampler's own stride walk, so the
    segment count is the real one; the angles are a stand-in, because the
    legs do not read them.

    Args:
        points: The stored track.
        **overrides: Keys replacing the record's defaults.

    Returns:
        A record of the shape ``build_slope_samples`` returns.

    """
    coordinates = stride_coordinates(points, SAMPLE_STRIDE_M)
    record: dict[str, Any] = {
        "window_m": 10.0,
        "stride_m": SAMPLE_STRIDE_M,
        "grid": "snowdesk-terrain-5m-3035",
        "points": [[lon, lat] for lon, lat in coordinates],
        "segments": [{"angle_deg": 20.0, "aspect_deg": 90.0}] * (len(coordinates) - 1),
    }
    record.update(overrides)
    return record


def _leg(start: int, end: int, *, climbing: bool) -> Leg:
    """Build a Leg with only the fields the wire reads set meaningfully.

    Args:
        start: First point index.
        end: Last point index.
        climbing: Whether the leg gains height.

    Returns:
        A ``Leg``.

    """
    return Leg(
        index=0,
        start=start,
        end=end,
        climbing=climbing,
        elevation_start=0.0,
        elevation_end=0.0,
        net_m=0.0,
        ascent_m=0.0,
        descent_m=0.0,
        distance_m=0.0,
        bearing_deg=0.0,
        compass="N",
        track_angle_deg=0.0,
    )


def _nearest_point(cumulative: list[float], target_m: float) -> int:
    """Return the index of the point whose distance is nearest ``target_m``.

    Args:
        cumulative: Along-track distance of each point.
        target_m: The distance wanted.

    Returns:
        A point index.

    """
    gaps = [abs(distance - target_m) for distance in cumulative]
    return gaps.index(min(gaps))


def _assert_covers(legs: list[dict[str, Any]], segment_count: int) -> None:
    """Assert the legs tile ``0 .. segment_count - 1`` exactly.

    Args:
        legs: The wire legs.
        segment_count: How many segments the record holds.

    """
    assert legs[0]["from"] == 0
    assert legs[-1]["to"] == segment_count - 1
    for previous, current in zip(legs, legs[1:], strict=False):
        assert current["from"] == previous["to"] + 1
    for leg in legs:
        assert leg["from"] <= leg["to"]
    assert [leg["i"] for leg in legs] == list(range(1, len(legs) + 1))


class TestTheLegsCoverTheSamples:
    """The wire legs tile ``slope.angles``, never ``Route.points``."""

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_coverage_is_exact_and_contiguous(self, filename: str) -> None:
        """First from 0, last to N − 1, each from one past the previous to."""
        points = _track(filename)
        record = _record_for(points)

        legs = wire_legs(points, record)

        assert legs is not None
        _assert_covers(legs, len(record["segments"]))

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_the_legs_alternate_and_match_detection(self, filename: str) -> None:
        """Every detected leg survives a 25 m stride, in the same order."""
        points = _track(filename)

        legs = wire_legs(points, _record_for(points))

        assert legs is not None
        assert [leg["climbing"] for leg in legs] == [
            leg.climbing for leg in detect_legs(points)
        ]

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_a_transition_lands_on_its_own_ground(self, filename: str) -> None:
        """Each cut is within one stride of the transition point's distance."""
        points = _track(filename)
        cumulative = cumulative_distances(points)

        legs = wire_legs(points, _record_for(points))

        assert legs is not None
        for wire, detected in zip(legs[1:], detect_legs(points)[1:], strict=True):
            boundary_m = wire["from"] * SAMPLE_STRIDE_M
            assert abs(boundary_m - cumulative[detected.start]) <= SAMPLE_STRIDE_M

    def test_the_shape_on_the_wire(self) -> None:
        """Four keys, nothing from the server-side Leg beyond them."""
        points = _track(CANONICAL[0])

        legs = wire_legs(points, _record_for(points))

        assert legs is not None
        assert set(legs[0]) == {
            "i",
            "from",
            "to",
            "climbing",
            "point_from",
            "point_to",
        }


class TestTheLegsCoverThePoints:
    """``point_from``/``point_to`` tile ``Route.points``, sharing each seam."""

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_the_points_are_covered_end_to_end(self, filename: str) -> None:
        """First point_from 0, last point_to the last point, seams shared."""
        points = _track(filename)

        legs = wire_legs(points, _record_for(points))

        assert legs is not None
        assert legs[0]["point_from"] == 0
        assert legs[-1]["point_to"] == len(points) - 1
        for previous, current in zip(legs, legs[1:], strict=False):
            assert current["point_from"] == previous["point_to"]
        for leg in legs:
            assert leg["point_from"] < leg["point_to"]

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_the_points_are_the_detected_boundaries(self, filename: str) -> None:
        """With no leg folded away, each leg's points are detection's own."""
        points = _track(filename)

        legs = wire_legs(points, _record_for(points))

        assert legs is not None
        assert [(leg["point_from"], leg["point_to"]) for leg in legs] == [
            (leg.start, leg.end) for leg in detect_legs(points)
        ]


class TestUnsampledRoutes:
    """Legs are a fact about the geometry, so an unsampled route has them."""

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_an_unsampled_route_is_cut_as_its_record_will_be(
        self, filename: str
    ) -> None:
        """Never sampled answers the same legs sampling would index."""
        points = _track(filename)

        assert wire_legs(points, None) == wire_legs(points, _record_for(points))

    @pytest.mark.parametrize("filename", CANONICAL)
    def test_an_unsampled_route_tiles_the_stride_walk(self, filename: str) -> None:
        """First from 0, last to N − 1 of the walk, contiguous between."""
        points = _track(filename)
        segment_count = len(stride_coordinates(points, SAMPLE_STRIDE_M)) - 1

        legs = wire_legs(points, None)

        assert legs is not None
        assert len(legs) > 1
        assert legs[0]["from"] == 0
        assert legs[-1]["to"] == segment_count - 1
        for previous, current in zip(legs, legs[1:], strict=False):
            assert current["from"] == previous["to"] + 1

    def test_a_record_with_no_segments_indexes_the_stride_walk(self) -> None:
        """An empty record gives the client no angles; same as unsampled."""
        points = _track(CANONICAL[0])

        assert wire_legs(points, _record_for(points, segments=[])) == wire_legs(
            points, None
        )


class TestNothingToSend:
    """A track that holds no leg sends nothing."""

    def test_a_track_without_elevation_has_no_legs(self) -> None:
        """``detect_legs`` finds nothing, so nothing is sent."""
        points: list[list[float | None]] = [
            [7.4, 46.1, None],
            [7.41, 46.1, None],
            [7.42, 46.1, None],
        ]

        assert wire_legs(points, _record_for(points)) is None


class TestShortAndOddRecords:
    """Single legs, collapsed legs, and records the stride cannot reproduce."""

    def test_a_single_leg_route_has_one_leg_over_every_segment(self) -> None:
        """A steady climb is one leg, ``0 .. N − 1``."""
        points: list[list[float | None]] = [
            [7.4 + index / 1000, 46.1, 1500.0 + index * 10] for index in range(40)
        ]
        record = _record_for(points)

        legs = wire_legs(points, record)

        assert legs == [
            {
                "i": 1,
                "from": 0,
                "to": len(record["segments"]) - 1,
                "climbing": True,
                "point_from": 0,
                "point_to": len(points) - 1,
            }
        ]

    def test_a_leg_inside_one_segment_folds_into_its_neighbours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Up, down, up — the down leg holds no segment, so the climbs merge.

        Detection is stubbed so the transitions can be placed exactly: the
        down leg starts and ends between the same two boundaries, both
        nearest the same one, and so collapses to nothing.
        """
        points: list[list[float | None]] = [
            [7.4 + index / 10000, 46.1, 1500.0 + index] for index in range(40)
        ]
        cumulative = cumulative_distances(points)
        # Point indices nearest 103 m and 109 m: both lie closer to the
        # 100 m boundary than to 125 m, so both cuts land on it.
        near = [_nearest_point(cumulative, target) for target in (103.0, 109.0)]
        legs = [
            _leg(0, near[0], climbing=True),
            _leg(near[0], near[1], climbing=False),
            _leg(near[1], len(points) - 1, climbing=True),
        ]
        monkeypatch.setattr(
            "apps.routes.services.leg_wire.detect_legs", lambda _points: legs
        )
        record = _record_for(points)

        wire = wire_legs(points, record)

        assert wire == [
            {
                "i": 1,
                "from": 0,
                "to": len(record["segments"]) - 1,
                "climbing": True,
                "point_from": 0,
                "point_to": len(points) - 1,
            }
        ]

    def test_a_folded_leg_keeps_the_points_contiguous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Down, up, down, up — the first up folds, and no points go missing.

        Four legs so two survive: the merged descent spans the run up to
        the last climb's start, which is the seam the climb opens on.
        """
        points: list[list[float | None]] = [
            [7.4 + index / 10000, 46.1, 1500.0 + index] for index in range(60)
        ]
        cumulative = cumulative_distances(points)
        near = [_nearest_point(cumulative, target) for target in (103.0, 109.0)]
        last_start = _nearest_point(cumulative, 200.0)
        legs = [
            _leg(0, near[0], climbing=False),
            _leg(near[0], near[1], climbing=True),
            _leg(near[1], last_start, climbing=False),
            _leg(last_start, len(points) - 1, climbing=True),
        ]
        monkeypatch.setattr(
            "apps.routes.services.leg_wire.detect_legs", lambda _points: legs
        )

        wire = wire_legs(points, _record_for(points))

        assert wire is not None
        assert [(leg["point_from"], leg["point_to"]) for leg in wire] == [
            (0, last_start),
            (last_start, len(points) - 1),
        ]
        assert [leg["climbing"] for leg in wire] == [False, True]

    def test_a_record_without_a_stride_places_legs_by_share(self) -> None:
        """No ``stride_m`` still yields exact coverage over the stored segments."""
        points = _track(CANONICAL[0])
        record = _record_for(points, stride_m=None)

        legs = wire_legs(points, record)

        assert legs is not None
        _assert_covers(legs, len(record["segments"]))

    def test_a_record_from_other_points_places_legs_by_share(self) -> None:
        """A segment count the stride walk cannot reproduce falls back to share."""
        points = _track(CANONICAL[0])
        record = _record_for(points, segments=[{}] * 7)

        legs = wire_legs(points, record)

        assert legs is not None
        _assert_covers(legs, 7)
