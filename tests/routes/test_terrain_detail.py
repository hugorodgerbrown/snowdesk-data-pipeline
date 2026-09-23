"""
tests/routes/test_terrain_detail.py — one row per segment, four figures.

Covers ``apps.routes.services.terrain_detail`` (SNOW-1020).

The tracks are straight lines across a synthetic PLANE: a face of known
angle and aspect, and a track whose elevation is exactly what that plane
gives along its heading. On a plane the along-track gradient is known in
closed form — ``tan(gradient) = -tan(slope) × cos(heading − aspect)`` —
so the tests can assert the figure itself rather than a property of it.

**ONE TEST ASSERTS THE PHYSICAL CONSTRAINT**: across every heading, the
track can never be steeper along its own length than the ground it is on.
That is the assertion that catches a sign error, a radians/degrees slip
or a stride mismatch at once, and nothing else here would catch all three.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from apps.core.geo import destination
from apps.routes.services.passages import (
    CLIMBING,
    CROSSING,
    DESCENDING,
    angular_difference,
)
from apps.routes.services.slope_segments import (
    cumulative_distances,
    stride_coordinates,
)
from apps.routes.services.terrain_detail import COLUMNS, terrain_detail

_START = (46.0, 7.0)
_STRIDE_M = 25.0


def _plane_track(
    heading_deg: float,
    slope_deg: float,
    aspect_deg: float,
    *,
    length_m: float = 300.0,
    step_m: float = 10.0,
) -> list[list[float | None]]:
    """Return a straight track across a plane, as ``Route.points``.

    Args:
        heading_deg: The track's compass bearing.
        slope_deg: The plane's angle.
        aspect_deg: The direction the plane faces — downhill.
        length_m: How long the track is.
        step_m: Spacing between stored points.

    Returns:
        ``[[lon, lat, ele], …]``, elevation falling along the aspect.

    """
    positions = [
        destination(_START[0], _START[1], heading_deg, index * step_m)
        for index in range(int(length_m // step_m) + 1)
    ]
    points: list[list[float | None]] = [[lon, lat, None] for lat, lon in positions]
    ratio = math.tan(math.radians(slope_deg)) * math.cos(
        math.radians(heading_deg - aspect_deg)
    )
    for point, along_m in zip(points, cumulative_distances(points), strict=True):
        point[2] = 2000.0 - ratio * along_m
    return points


def _record(
    points: list[list[float | None]],
    slope_deg: float,
    aspect_deg: float | None,
    *,
    unknown_at: int | None = None,
    stride_m: float | None = _STRIDE_M,
) -> dict[str, Any]:
    """Return the slope record the sampler would store for ``points``.

    Args:
        points: The track.
        slope_deg: The angle every known segment carries.
        aspect_deg: The aspect every known segment carries.
        unknown_at: One segment index to store as unknown instead.
        stride_m: The walk's stride, or None to omit the key.

    Returns:
        The record, shaped as ``Route.slope_samples``.

    """
    boundaries = [
        [round(lon, 6), round(lat, 6)]
        for lon, lat in stride_coordinates(points, _STRIDE_M)
    ]
    segments: list[dict[str, Any]] = [
        {"angle_deg": slope_deg, "aspect_deg": aspect_deg}
        for _ in range(len(boundaries) - 1)
    ]
    if unknown_at is not None:
        segments[unknown_at] = {"unknown": "outside_coverage"}
    record: dict[str, Any] = {
        "points": boundaries,
        "segments": segments,
        "summary": {"sampled_m": cumulative_distances(points)[-1]},
    }
    if stride_m is not None:
        record["stride_m"] = stride_m
    return record


def _expected_gradient(
    heading_deg: float, slope_deg: float, aspect_deg: float
) -> float:
    """Return the closed-form along-track gradient on a plane, in degrees."""
    return -math.degrees(
        math.atan(
            math.tan(math.radians(slope_deg))
            * math.cos(math.radians(heading_deg - aspect_deg))
        )
    )


class TestRefusals:
    """Nothing to read is None, not an empty table."""

    def test_an_unsampled_route_is_none(self) -> None:
        """No record at all."""
        assert terrain_detail(None, _plane_track(0, 30, 0)) is None

    def test_a_record_whose_halves_do_not_pair_is_none(self) -> None:
        """N + 1 boundaries must bound N segments."""
        points = _plane_track(0, 30, 0)
        record = _record(points, 30.0, 0.0)
        record["segments"].pop()
        assert terrain_detail(record, points) is None


class TestFigures:
    """Each figure, on a plane whose answer is known."""

    @pytest.mark.parametrize(
        ("heading", "label"),
        [(0.0, DESCENDING), (180.0, CLIMBING), (90.0, CROSSING)],
    )
    def test_gradient_bearing_and_label_on_a_plane(
        self, heading: float, label: str
    ) -> None:
        """A 30 degree north face, crossed down, up and along it."""
        points = _plane_track(heading, 30.0, 0.0)
        rows = terrain_detail(_record(points, 30.0, 0.0), points)
        assert rows is not None
        expected = _expected_gradient(heading, 30.0, 0.0)
        for row in rows:
            assert row["track_gradient_deg"] == pytest.approx(expected, abs=0.2)
            assert angular_difference(row["bearing_deg"], heading) <= 1.0
            assert row["fall_line"] == label
            assert row["angle_deg"] == 30.0
            assert row["aspect_deg"] == 0.0

    def test_a_descent_is_negative(self) -> None:
        """The sign: down the fall line is a negative gradient."""
        points = _plane_track(0.0, 30.0, 0.0)
        rows = terrain_detail(_record(points, 30.0, 0.0), points)
        assert rows is not None
        assert all(row["track_gradient_deg"] < 0 for row in rows)

    @pytest.mark.parametrize("heading", range(0, 360, 30))
    def test_the_track_is_never_steeper_than_the_ground(self, heading: int) -> None:
        """The physical constraint, at every heading.

        A radians/degrees slip, a sign error or a stride mismatch each
        break this, which is why it is the one assertion that must stay.
        """
        points = _plane_track(float(heading), 40.0, 135.0)
        rows = terrain_detail(_record(points, 40.0, 135.0), points)
        assert rows is not None
        for row in rows:
            assert abs(row["track_gradient_deg"]) <= row["angle_deg"] + 0.2
            assert row["track_gradient_deg"] == pytest.approx(
                _expected_gradient(heading, 40.0, 135.0), abs=0.2
            )

    def test_every_row_carries_every_column(self) -> None:
        """The CSV header and the rows cannot list different fields."""
        points = _plane_track(0.0, 30.0, 0.0)
        rows = terrain_detail(_record(points, 30.0, 0.0), points)
        assert rows is not None
        assert all(tuple(row) == COLUMNS for row in rows)


class TestLengths:
    """Distances come from the record's stride, not from chords."""

    def test_the_last_segment_absorbs_the_stub(self) -> None:
        """310 m is twelve strides and a 10 m stub folded into the last."""
        points = _plane_track(0.0, 30.0, 0.0, length_m=310.0)
        rows = terrain_detail(_record(points, 30.0, 0.0), points)
        assert rows is not None
        assert len(rows) == 12
        assert rows[-1]["length_m"] == pytest.approx(35.0, abs=0.2)
        assert rows[-1]["from_m"] == pytest.approx(275.0)
        assert [row["from_m"] for row in rows[:3]] == [0.0, 25.0, 50.0]


class TestSmoothing:
    """The window is honoured and a spike is spread."""

    def _spiked(self) -> tuple[dict[str, Any], list[list[float | None]]]:
        """A level track with one point lifted 20 m at 150 m along."""
        points = _plane_track(90.0, 30.0, 0.0)
        points[15][2] = float(points[15][2] or 0.0) + 20.0
        return _record(points, 30.0, 0.0), points

    def test_a_window_of_zero_is_the_raw_gradient(self) -> None:
        """Each segment's own rise over its own run."""
        record, points = self._spiked()
        rows = terrain_detail(record, points, gradient_window=0)
        assert rows is not None
        steepest = max(abs(row["track_gradient_deg"]) for row in rows)
        # 20 m over the 10 m of track either side of the lifted point.
        assert steepest > 30.0

    def test_the_default_window_spreads_the_spike(self) -> None:
        """Five segments of run take most of the height out of it."""
        record, points = self._spiked()
        raw = terrain_detail(record, points, gradient_window=0)
        smoothed = terrain_detail(record, points)
        assert raw is not None and smoothed is not None
        assert max(abs(r["track_gradient_deg"]) for r in smoothed) < max(
            abs(r["track_gradient_deg"]) for r in raw
        )


class TestMissing:
    """What cannot be derived is None, never zero."""

    def test_an_unknown_segment_keeps_its_reason_and_its_track_figures(self) -> None:
        """The ground is unknown; the track is not."""
        points = _plane_track(0.0, 30.0, 0.0)
        rows = terrain_detail(_record(points, 30.0, 0.0, unknown_at=4), points)
        assert rows is not None
        row = rows[4]
        assert row["unknown"] == "outside_coverage"
        assert row["angle_deg"] is None
        assert row["aspect_deg"] is None
        assert row["fall_line"] is None
        assert row["bearing_deg"] is not None
        assert row["track_gradient_deg"] is not None

    def test_level_ground_has_no_aspect_and_no_label(self) -> None:
        """A known angle with a null aspect faces nowhere."""
        points = _plane_track(0.0, 0.0, 0.0)
        rows = terrain_detail(_record(points, 0.0, None), points)
        assert rows is not None
        assert all(row["fall_line"] is None for row in rows)
        assert all(row["bearing_deg"] is not None for row in rows)

    def test_a_track_without_elevation_has_no_gradient(self) -> None:
        """No third ordinate, nothing to rise over."""
        points = _plane_track(0.0, 30.0, 0.0)
        record = _record(points, 30.0, 0.0)
        for point in points:
            point[2] = None
        rows = terrain_detail(record, points)
        assert rows is not None
        assert all(row["track_gradient_deg"] is None for row in rows)

    def test_points_that_are_not_the_records_have_no_gradient(self) -> None:
        """A re-walk landing a different boundary count is the wrong track."""
        points = _plane_track(0.0, 30.0, 0.0)
        record = _record(points, 30.0, 0.0)
        other = _plane_track(0.0, 30.0, 0.0, length_m=500.0)
        rows = terrain_detail(record, other)
        assert rows is not None
        assert all(row["track_gradient_deg"] is None for row in rows)
        assert all(row["angle_deg"] == 30.0 for row in rows)

    def test_a_record_without_a_stride_has_no_gradient(self) -> None:
        """The walk cannot be repeated without knowing its stride."""
        points = _plane_track(0.0, 30.0, 0.0)
        rows = terrain_detail(_record(points, 30.0, 0.0, stride_m=None), points)
        assert rows is not None
        assert all(row["track_gradient_deg"] is None for row in rows)
