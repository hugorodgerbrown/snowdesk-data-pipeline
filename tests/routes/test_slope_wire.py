"""
tests/routes/test_slope_wire.py — the slope record's ``seams`` on the wire (SNOW-1053).

Covers the ``coordinates=`` half of ``apps.routes.services.slope_wire.
compact_slope``: per boundary, the index of the last geometry coordinate
at or before it, so the map can draw each slope-class segment along the
track's real coordinates instead of a straight chord between boundaries.

  - on the MERGED track ``terrain_points`` sends when the record has
    heights, a boundary's seam is that boundary's own coordinate;
  - on the UNMERGED track, the vertex before it;
  - a boundary the merge dropped onto a vertex seams onto that vertex,
    but a vertex measurably PAST a boundary does not;
  - no ``seams`` key for a stride-count mismatch, a missing or invalid
    ``stride_m``, a track with no length, or a caller passing no
    coordinates;
  - the Hidden Valley canonical track, merged and unmerged: the seams are
    a partition of its coordinates into segments.

The remaining keys ``compact_slope`` sends are covered by the view tests
and by the modules that derive them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.routes.services.canonical import CANONICAL_DIR
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.slope_segments import SAMPLE_STRIDE_M, stride_coordinates
from apps.routes.services.slope_wire import compact_slope
from apps.routes.services.terrain_heights import terrain_points

_RECORDS = Path(__file__).parent / "fixtures" / "slope_records"

# A meridian track about 111 m long with a vertex every ~37 m: the stride
# walk puts boundaries at 0, 25, 50, 75 and the end (the stub absorbed),
# none of them on a vertex.
_TRACK: list[list[float | None]] = [
    [7.0, 46.0 + 0.001 * index / 3, 2000.0 + 10.0 * index] for index in range(4)
]


def _record(track: list[list[float | None]], *, heights: bool = True) -> dict[str, Any]:
    """Return a slope record sampled along ``track``.

    Args:
        track: The stored track.
        heights: Whether to give the record model heights, which makes
            ``terrain_points`` merge its boundaries into the track.

    Returns:
        A record with a stride, boundary points, one segment per stride
        and, optionally, one height per boundary.

    """
    boundaries = stride_coordinates(track, SAMPLE_STRIDE_M)
    record: dict[str, Any] = {
        "stride_m": SAMPLE_STRIDE_M,
        "points": [[round(lon, 6), round(lat, 6)] for lon, lat in boundaries],
        "segments": [{"angle_deg": 20.0} for _ in boundaries[1:]],
    }
    if heights:
        record["heights"] = [2000.0 + index for index in range(len(boundaries))]
    return record


def _seams(track: list[list[float | None]], record: dict[str, Any]) -> list[int] | None:
    """Return the ``seams`` ``compact_slope`` sends for this track.

    Args:
        track: The geometry the feature carries.
        record: The slope record.

    Returns:
        The list, or None when the key is absent.

    """
    slope = compact_slope(record, coordinates=track)
    assert slope is not None
    return slope.get("seams")


def _assert_partition(seams: list[int], coordinate_count: int, segments: int) -> None:
    """Assert ``seams`` cut the coordinates into one slice per segment.

    Segment ``i`` takes the coordinates after ``seams[i]`` up to and
    including ``seams[i + 1]`` (segment 0 also takes the first), so the
    seams partition the track exactly when they never decrease, start at 0
    and end on the last coordinate.

    Args:
        seams: What ``compact_slope`` sent.
        coordinate_count: The geometry's length.
        segments: How many segments the record holds.

    """
    assert len(seams) == segments + 1
    assert seams[0] == 0
    assert seams[-1] == coordinate_count - 1
    assert all(a <= b for a, b in zip(seams, seams[1:], strict=False))
    covered = [0] + [
        index
        for start, end in zip(seams, seams[1:], strict=False)
        for index in range(start + 1, end + 1)
    ]
    assert covered == list(range(coordinate_count))


class TestSeams:
    """Where each boundary lands in the feature's coordinates."""

    def test_merged_track_seams_on_each_boundary_point(self) -> None:
        """On the merged track a boundary's seam is its own coordinate."""
        record = _record(_TRACK)
        merged = terrain_points(_TRACK, record)
        seams = _seams(merged, record)
        assert seams is not None
        # The merge drops the two end boundaries onto the end vertices and
        # interleaves the other three: v0, b1, v1, b2, v2, b3, v3. Each
        # kept boundary's seam names the merged point equal to it.
        for boundary, seam in zip(record["points"][1:-1], seams[1:-1], strict=True):
            assert merged[seam][:2] == boundary
        assert seams == [0, 1, 3, 5, 6]

    def test_unmerged_track_seams_on_the_vertex_before(self) -> None:
        """Without heights a boundary seams onto the vertex before it."""
        record = _record(_TRACK, heights=False)
        # Vertices at 0, ~37, ~74 and ~111 m; boundaries at 0, 25, 50, 75
        # and the end.
        assert _seams(_TRACK, record) == [0, 0, 1, 2, 3]

    def test_boundary_on_a_vertex_seams_onto_the_vertex(self) -> None:
        """A boundary the merge dropped as coincident seams onto the vertex."""
        # Vertices every 25 m along the meridian, so every boundary but
        # the absorbed end lands on one and the merge drops it.
        step = 25.0 / 111_194.9
        track: list[list[float | None]] = [
            [7.0, 46.0 + step * index, 2000.0] for index in range(5)
        ]
        record = _record(track)
        merged = terrain_points(track, record)
        assert len(merged) == len(track)
        assert _seams(merged, record) == list(range(len(track)))

    def test_vertex_just_past_a_boundary_is_not_its_seam(self) -> None:
        """A vertex 0.3 m past a boundary starts the next segment instead.

        The merge drops a boundary within half a metre of a vertex, but
        the seam must not reach that far: the vertex would join the
        segment ending at the boundary, and the drawn path would run out
        to it and double back.
        """
        metre = 1.0 / 111_194.9
        track: list[list[float | None]] = [
            [7.0, 46.0, 2000.0],
            [7.0, 46.0 + 25.3 * metre, 2000.0],
            [7.0, 46.0 + 60.0 * metre, 2000.0],
        ]
        record = _record(track)
        merged = terrain_points(track, record)
        assert len(merged) == len(track)  # both boundaries fell on vertices
        assert _seams(merged, record) == [0, 0, 2]

    def test_mismatched_stride_count_sends_no_seams(self) -> None:
        """Coordinates the record was not sampled along get no seams."""
        record = _record(_TRACK, heights=False)
        longer: list[list[float | None]] = [*_TRACK, [7.0, 46.002, 2100.0]]
        assert _seams(longer, record) is None

    @pytest.mark.parametrize("stride", [None, 0, -25.0, "25", True])
    def test_unusable_stride_sends_no_seams(self, stride: Any) -> None:
        """No usable ``stride_m`` means the walk cannot be repeated."""
        record = _record(_TRACK, heights=False)
        record["stride_m"] = stride
        assert _seams(_TRACK, record) is None

    def test_track_with_no_length_sends_no_seams(self) -> None:
        """A track with no length has no boundaries to place."""
        record = _record(_TRACK, heights=False)
        assert _seams([_TRACK[0], _TRACK[0]], record) is None

    def test_no_coordinates_sends_no_seams(self) -> None:
        """A caller that sends no geometry gets no seams."""
        slope = compact_slope(_record(_TRACK, heights=False))
        assert slope is not None
        assert "seams" not in slope


class TestHiddenValley:
    """The seams on a real track partition its coordinates."""

    @pytest.fixture
    def track(self) -> tuple[list[list[float | None]], dict[str, Any]]:
        """Return Hidden Valley's parsed points and recorded slope record."""
        parsed = parse_gpx((CANONICAL_DIR / "hidden-valley.gpx").read_bytes())
        record = json.loads((_RECORDS / "hidden-valley.json").read_text())
        return parsed.points, record

    def test_merged_seams_partition_the_track(
        self, track: tuple[list[list[float | None]], dict[str, Any]]
    ) -> None:
        """On the merged terrain track every coordinate is in one segment."""
        points, record = track
        merged = terrain_points(points, record)
        assert len(merged) > len(points)
        seams = _seams(merged, record)
        assert seams is not None
        _assert_partition(seams, len(merged), len(record["segments"]))

    def test_unmerged_seams_partition_the_track(
        self, track: tuple[list[list[float | None]], dict[str, Any]]
    ) -> None:
        """On the stored track every coordinate is in one segment too."""
        points, record = track
        seams = _seams(points, {**record, "heights": None})
        assert seams is not None
        _assert_partition(seams, len(points), len(record["segments"]))

    def test_merged_boundaries_are_their_own_seams(
        self, track: tuple[list[list[float | None]], dict[str, Any]]
    ) -> None:
        """A boundary kept in the merge is the coordinate its seam names."""
        points, record = track
        merged = terrain_points(points, record)
        seams = _seams(merged, record)
        assert seams is not None
        kept = {tuple(point[:2]) for point in merged}
        for boundary, seam in zip(record["points"], seams, strict=True):
            if tuple(boundary) in kept:
                assert merged[seam][:2] == boundary
