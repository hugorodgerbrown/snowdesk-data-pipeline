"""
tests/routes/test_terrain_heights.py — a track's heights from the model (SNOW-1043).

Covers ``apps.routes.services.terrain_heights``:

  - ``terrain_points``: the stored vertices MERGED with the record's
    boundaries, every point on the model's height — so a straight line
    stored as two ends still climbs the hill it crosses, and the leg
    indices ``wire_legs`` sends slice that same list; a run the model does
    not cover REBASED onto the model's datum, so a drifting altimeter makes
    no cliff at a coverage edge; the track returned as stored when the
    record has no heights or was sampled from different points; the input
    never mutated;
  - ``boundary_heights``: the same heights at the boundaries only;
  - ``climb_totals``: the ingest rule for ascent and descent, on any track;
  - ``has_terrain_heights``: a list of nulls is not a record with heights;
  - ``climb_figures``: the one rule every surface shows ascent and descent
    by — the model's totals with heights, the stored columns without;
  - SNOW-1051, the despike: a spike on steep ground replaced by its
    neighbours' median, and nothing else touched (gentle ground, a real
    step, a monotonic climb, holes, a record with no segments), pinned on
    the four canonical tours to the exact boundaries altered;
  - the reason for all of it, on real data: the Mont Fort – Backside tour
    read on its drifting altimeter has a climb at the start that the
    terrain model does not, and loses it on model heights.

The canonical records under ``fixtures/slope_records/`` were captured from
the live tile origin by ``bin/record-slope-fixtures``; the tracks are
re-parsed from the committed GPX so the two can never be a different track.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from apps.routes.services.canonical import CANONICAL_DIR
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.leg_wire import wire_legs
from apps.routes.services.legs import detect_legs
from apps.routes.services.slope_segments import SAMPLE_STRIDE_M, stride_coordinates
from apps.routes.services.terrain_detail import terrain_detail
from apps.routes.services.terrain_heights import (
    ClimbFigures,
    boundary_heights,
    climb_figures,
    climb_totals,
    has_terrain_heights,
    terrain_points,
)
from tests.factories import DRIFTING_TRACK, drifting_track_record

_RECORDS = Path(__file__).parent / "fixtures" / "slope_records"

# A straight meridian track, 0.001° (about 111 m) long, recorded 100 m
# too high at the start and 300 m too high at the end — a drifting
# altimeter in miniature.
_TRACK: list[list[float | None]] = [
    [7.0, 46.0, 2100.0],
    [7.0, 46.0005, 2200.0],
    [7.0, 46.001, 2300.0],
]


def _canonical(stem: str) -> tuple[list[list[float | None]], dict[str, Any]]:
    """Return a canonical track's parsed points and its recorded slope record.

    Args:
        stem: The file name without its extension.

    Returns:
        ``(points, record)``.

    """
    parsed = parse_gpx((CANONICAL_DIR / f"{stem}.gpx").read_bytes())
    record = json.loads((_RECORDS / f"{stem}.json").read_text())
    return parsed.points, record


def _record(
    heights: list[float | None], track: list[list[float | None]] | None = None
) -> dict[str, Any]:
    """Return a minimal slope record for ``track`` carrying ``heights``.

    ``_TRACK`` (the default) is about 111 m, so the stride walk puts
    boundaries at 0, 25, 50, 75 and the end — five, the last absorbing the
    stub.

    Args:
        heights: One height per boundary.
        track: The track the boundaries are placed on.

    Returns:
        A record with a stride, the boundary coordinates and the heights.

    """
    boundaries = stride_coordinates(track or _TRACK, SAMPLE_STRIDE_M)
    return {
        "stride_m": SAMPLE_STRIDE_M,
        "points": [[round(lon, 6), round(lat, 6)] for lon, lat in boundaries],
        "heights": heights,
    }


# A straight line stored as its two ends, 0.009° of meridian (about
# 1,001 m): the shape of a planned route, which carries a vertex only
# where the line turns. The stride walk lands 41 boundaries on it.
_STRAIGHT: list[list[float | None]] = [[7.0, 46.0, 2000.0], [7.0, 46.009, 2000.0]]

# A hill across it: 20 boundaries up at 10 m each, 20 down.
_HILL: list[float | None] = [2000.0 + 10.0 * min(k, 40 - k) for k in range(41)]


def _meridian(
    count: int, length_deg: float, heights: list[float]
) -> list[list[float | None]]:
    """Return a meridian track of ``count`` evenly spaced vertices.

    Args:
        count: How many vertices.
        length_deg: Its length in degrees of latitude.
        heights: One recorded height per vertex.

    Returns:
        ``[[lon, lat, ele], …]``.

    """
    return [
        [7.0, 46.0 + length_deg * index / (count - 1), heights[index]]
        for index in range(count)
    ]


def _heights(track: list[list[float | None]]) -> list[float]:
    """Return a track's heights, asserting every point has one.

    Args:
        track: ``[[lon, lat, ele], …]``.

    Returns:
        The third ordinates, as floats.

    """
    heights = [point[2] for point in track]
    assert all(height is not None for height in heights)
    return [float(height or 0.0) for height in heights]


def _steps(track: list[list[float | None]]) -> list[float]:
    """Return the absolute height change between consecutive points.

    Args:
        track: ``[[lon, lat, ele], …]``, every point carrying a height.

    Returns:
        One step per consecutive pair.

    """
    heights = _heights(track)
    return [abs(b - a) for a, b in zip(heights, heights[1:], strict=False)]


class TestTerrainPoints:
    """The terrain track: vertices and boundaries on the model's heights."""

    def test_the_boundaries_are_merged_in_between_the_vertices(self) -> None:
        """Three vertices and three interior boundaries, in track order.

        The first and last boundaries fall on the first and last vertices
        and are dropped; the vertex at 55.6 m takes the model height
        between the 50 m and 75 m boundaries.
        """
        result = terrain_points(
            _TRACK, _record([2000.0, 2010.0, 2020.0, 2030.0, 2040.0])
        )

        assert [point[2] for point in result] == pytest.approx(
            [2000.0, 2010.0, 2020.0, 2022.2, 2030.0, 2040.0], abs=0.05
        )
        assert result[0][:2] == _TRACK[0][:2]
        assert result[3][:2] == _TRACK[1][:2]
        assert result[-1][:2] == _TRACK[2][:2]
        latitudes = [float(point[1] or 0.0) for point in result]
        assert latitudes == sorted(latitudes)

    def test_a_straight_line_over_a_hill_climbs_the_hill(self) -> None:
        """Two stored ends at the same height, 200 m of hill between them."""
        result = terrain_points(_STRAIGHT, _record(_HILL, _STRAIGHT))

        assert len(result) == 41
        assert climb_totals(result) == (200.0, 200.0)
        assert [leg.climbing for leg in detect_legs(result)] == [True, False]

    def test_the_leg_indices_slice_the_merged_track(self) -> None:
        """``point_from``/``point_to`` index the list the client is sent."""
        record = _record(_HILL, _STRAIGHT)
        result = terrain_points(_STRAIGHT, record)

        legs = wire_legs(result, None)

        assert legs is not None
        climb, descent = legs
        assert climb["point_from"] == 0
        assert descent["point_to"] == len(result) - 1
        heights = _heights(result)
        summit = heights.index(max(heights))
        # The transition is placed on the smoothed profile, so it may sit
        # one 25 m step off the summit — but it indexes THIS list: the
        # slice it names climbs from the start to the top of the hill.
        assert abs(climb["point_to"] - summit) <= 1
        climbed = heights[climb["point_from"] : climb["point_to"] + 1]
        assert climbed[0] == 2000.0
        assert climbed[-1] >= 2190.0
        assert heights[descent["point_to"]] == 2000.0
        assert climb["point_to"] == descent["point_from"]

    def test_an_uncovered_run_is_rebased_and_leaves_no_cliff(self) -> None:
        """A 300 m drift across a coverage gap is not a 300 m step.

        Twenty-one vertices over about 1 km, recorded 300 m above a steady
        model ramp and drifting a further 60 m. The model is missing for
        ten boundaries in the middle; the run between is rebased onto it,
        so the totals match the model-only answer and no step is larger
        than the ramp's own.
        """
        model = [2000.0 + 5.0 * k for k in range(41)]
        device = [2300.0 + 50.0 * i + 3.0 * i for i in range(21)]
        track = _meridian(21, 0.009, device)
        gapped: list[float | None] = [
            None if 15 <= k <= 25 else height for k, height in enumerate(model)
        ]

        rebased = terrain_points(track, _record(gapped, track))
        complete = terrain_points(track, _record(list(model), track))

        steps = _steps(rebased)
        assert max(steps) < 10.0
        assert climb_totals(rebased)[0] == pytest.approx(
            climb_totals(complete)[0], abs=15
        )
        assert climb_totals(rebased)[1] == pytest.approx(0.0, abs=1)

    def test_a_run_at_the_end_takes_the_one_offset_it_has(self) -> None:
        """No model point after the run: the offset before it holds."""
        result = terrain_points(_TRACK, _record([2000.0, None, None, None, None]))

        # Device 2100 → model 2000 at the start: every later point is the
        # recording shifted down 100 m.
        assert [point[2] for point in result] == pytest.approx(
            [2000.0, 2045.0, 2089.9, 2100.0, 2134.9, 2200.0], abs=0.1
        )

    def test_a_record_without_heights_returns_the_track_unchanged(self) -> None:
        """A record written before SNOW-1043 changes nothing."""
        assert terrain_points(_TRACK, {"stride_m": SAMPLE_STRIDE_M}) == _TRACK
        assert terrain_points(_TRACK, None) == _TRACK

    def test_a_record_of_all_null_heights_returns_the_track_unchanged(self) -> None:
        """Wholly outside the model's coverage is as good as no heights."""
        assert terrain_points(_TRACK, _record([None] * 5)) == _TRACK

    def test_a_record_from_other_points_returns_the_track_unchanged(self) -> None:
        """A boundary count that does not match means the wrong ground."""
        record = _record([2000.0] * 5)
        record["points"] = record["points"][:2]
        record["heights"] = [2000.0, 2010.0]
        assert terrain_points(_TRACK, record) == _TRACK

    def test_a_record_with_no_stride_returns_the_track_unchanged(self) -> None:
        """No stride, no way to repeat the walk."""
        record = _record([2000.0] * 5)
        del record["stride_m"]
        assert terrain_points(_TRACK, record) == _TRACK

    def test_the_input_is_never_mutated(self) -> None:
        """The stored track is read, never rewritten."""
        track = copy.deepcopy(_TRACK)
        terrain_points(track, _record([2000.0, None, 2000.0, 2000.0, 2000.0]))

        assert track == _TRACK


class TestBoundaryHeights:
    """The terrain track's heights at the record's boundaries."""

    def test_one_height_per_boundary(self) -> None:
        """Known boundaries are the model's, a gap is rebased."""
        heights = boundary_heights(
            _TRACK, _record([2000.0, 2010.0, None, 2030.0, 2040.0])
        )

        assert heights == pytest.approx(
            [2000.0, 2010.0, 2020.0, 2030.0, 2040.0], abs=0.1
        )

    def test_no_heights_is_none(self) -> None:
        """Nothing to read means the caller falls back to the track."""
        assert boundary_heights(_TRACK, None) is None


class TestClimbTotals:
    """Ascent and descent, summed independently."""

    def test_climb_and_drop_are_summed_separately(self) -> None:
        """An out-and-back carries both, not zero of each."""
        track: list[list[float | None]] = [
            [7.0, 46.0, 2000.0],
            [7.0, 46.001, 2100.0],
            [7.0, 46.002, 2050.0],
        ]

        assert climb_totals(track) == (100.0, 50.0)

    def test_a_gap_is_skipped_not_counted_as_a_cliff(self) -> None:
        """Only pairs with both elevations contribute."""
        track: list[list[float | None]] = [
            [7.0, 46.0, 2000.0],
            [7.0, 46.001, None],
            [7.0, 46.002, 1000.0],
            [7.0, 46.003, 1010.0],
        ]

        assert climb_totals(track) == (10.0, 0.0)

    def test_no_elevation_is_unknown_not_zero(self) -> None:
        """A track without heights supports no figure."""
        assert climb_totals([[7.0, 46.0, None], [7.0, 46.001, None]]) == (None, None)


class TestClimbFigures:
    """The ascent and descent every surface shows."""

    def test_model_heights_give_the_model_totals(self) -> None:
        """The drifting track climbs 40 m on the model, not 200 m."""
        figures = climb_figures(DRIFTING_TRACK, drifting_track_record(), 200.0, 0.0)

        assert figures == ClimbFigures(40.0, 0.0)

    def test_no_heights_give_the_stored_columns(self) -> None:
        """A record sampled before SNOW-1043 changes nothing."""
        record = drifting_track_record()
        del record["heights"]

        assert climb_figures(DRIFTING_TRACK, record, 850.0, 1100.0) == (850.0, 1100.0)
        assert climb_figures(DRIFTING_TRACK, None, 850.0, 1100.0) == (850.0, 1100.0)

    def test_a_stored_null_passes_through(self) -> None:
        """Unknown stays unknown, never zero."""
        assert climb_figures(DRIFTING_TRACK, None, None, None) == (None, None)


class TestHasTerrainHeights:
    """Whether a record carries any model height."""

    @pytest.mark.parametrize(
        ("samples", "expected"),
        [
            (None, False),
            ({}, False),
            ({"heights": None}, False),
            ({"heights": [None, None]}, False),
            ({"heights": [None, 2000.0]}, True),
        ],
    )
    def test_only_a_number_counts(self, samples: Any, expected: bool) -> None:
        """A list of nulls is no heights at all."""
        assert has_terrain_heights(samples) is expected


class TestTheBacksideOnModelHeights:
    """The ticket's case: an altimeter drifting up while the skier descends."""

    def test_the_device_heights_invent_a_climb_at_the_start(self) -> None:
        """Four legs on the recording, the first of them a climb."""
        points, _ = _canonical("mont-fort-backside")

        legs = detect_legs(points)

        assert [leg.climbing for leg in legs] == [True, False, True, False]

    def test_the_model_heights_read_it_as_the_descent_it_was(self) -> None:
        """Three legs: down, up, down.

        Measured on the committed record, on the merged terrain track,
        after the SNOW-1051 despike: 554.1 m of descent, 274.6 m of
        ascent, then 1,309.1 m of descent.
        The climb ends on the track's high point (``_snap_to_extrema``);
        cut where the smoothed series turned, 39 m short of it, it read
        260.4 m.
        """
        points, record = _canonical("mont-fort-backside")

        legs = detect_legs(terrain_points(points, record))

        assert [leg.climbing for leg in legs] == [False, True, False]
        assert legs[0].descent_m == pytest.approx(554.1, abs=5)
        assert legs[1].ascent_m == pytest.approx(274.6, abs=5)
        assert legs[2].descent_m == pytest.approx(1309.1, abs=5)

    def test_the_model_heights_cut_the_phantom_climb_from_the_totals(self) -> None:
        """Ascent falls from 478.8 m recorded to about 305 m on the model."""
        points, record = _canonical("mont-fort-backside")

        ascent, descent = climb_totals(terrain_points(points, record))

        assert ascent == pytest.approx(304.7, abs=5)
        assert descent == pytest.approx(1873.0, abs=5)

    def test_every_point_of_a_covered_track_takes_a_model_height(self) -> None:
        """Mont Fort is inside the model's coverage end to end."""
        _, record = _canonical("mont-fort-backside")

        assert None not in record["heights"]
        assert len(record["heights"]) == len(record["points"])


class TestTheSmoothingStopsAtTheLeg:
    """The terrain table's gradient is summed inside one leg only."""

    def test_no_window_reaches_into_another_leg(self) -> None:
        """Every segment's window sits inside the leg that holds it."""
        points, record = _canonical("mont-fort-col-de-la-chaux")
        rows = terrain_detail(record, points)
        assert rows is not None

        for leg in wire_legs(terrain_points(points, record), record) or []:
            for row in rows[leg["from"] : leg["to"] + 1]:
                assert leg["from"] <= row["track_gradient_from"] <= row["i"]
                assert row["i"] <= row["track_gradient_to"] <= leg["to"]

    def test_the_first_segment_down_from_the_col_reads_as_the_descent_it_is(
        self,
    ) -> None:
        """Leg 7 opens dropping 5.2 m in 25 m; across the col it read 5.6."""
        points, record = _canonical("mont-fort-col-de-la-chaux")
        rows = terrain_detail(record, points)
        assert rows is not None
        leg = (wire_legs(terrain_points(points, record), record) or [])[6]

        first = rows[leg["from"]]

        assert first["ele_to_m"] < first["ele_from_m"]
        assert first["track_gradient_from"] == leg["from"]
        assert first["track_gradient_deg"] < -10


class TestChamonixAcrossTheCoverageEdge:
    """A track partly outside the model is rebased there, not left raw."""

    def test_the_coverage_edge_is_not_a_cliff(self) -> None:
        """No step on the terrain track is larger than the ground's own."""
        points, record = _canonical("chamonix-col-de-balme")

        result = terrain_points(points, record)

        steps = _steps(result)
        assert None in record["heights"]
        assert max(steps) < 10.0

    def test_its_totals(self) -> None:
        """Measured on the committed record: 165.4 m up, 715.5 m down."""
        points, record = _canonical("chamonix-col-de-balme")

        ascent, descent = climb_totals(terrain_points(points, record))

        assert ascent == pytest.approx(165.4, abs=10)
        assert descent == pytest.approx(715.5, abs=10)


# SNOW-1051 -----------------------------------------------------------------


def _level_record(
    heights: list[float | None], angle_deg: float | None
) -> dict[str, Any]:
    """Return a record on ``_STRAIGHT`` whose every segment has one ground angle.

    Args:
        heights: One height per boundary (41 of them).
        angle_deg: The ground angle of every segment, or None for a
            segment carrying none.

    Returns:
        The record, with its ``segments`` list.

    """
    record = _record(heights, _STRAIGHT)
    record["segments"] = [
        {} if angle_deg is None else {"angle_deg": angle_deg}
        for _ in range(len(heights) - 1)
    ]
    return record


def _cleaned(record: dict[str, Any]) -> list[float | None]:
    """Return the boundary heights ``boundary_heights`` reads off ``record``."""
    result = boundary_heights(_STRAIGHT, record)
    assert result is not None
    return result


def _level(spike_at: dict[int, float]) -> list[float | None]:
    """Return 41 level boundaries at 2,000 m with the given indices raised."""
    return [2000.0 + spike_at.get(index, 0.0) for index in range(41)]


class TestDespikeOnSteepGround:
    """A spike on ground of 40° or more is replaced by its window's median."""

    def test_a_single_spike_on_steep_ground_is_replaced(self) -> None:
        """One point 9 m high on a 50° face reads as the level road it is."""
        record = _level_record(_level({20: 9.0}), 50.0)

        assert _cleaned(record)[20] == 2000.0

    def test_a_three_point_bump_on_steep_ground_is_replaced(self) -> None:
        """The ±75 m window is seven points, so three bad ones are outvoted."""
        record = _level_record(_level({19: 8.0, 20: 9.0, 21: 7.0}), 50.0)

        assert _cleaned(record)[19:22] == [2000.0, 2000.0, 2000.0]

    def test_the_same_spike_on_gentle_ground_is_left_alone(self) -> None:
        """Below the gate the point is real until shown otherwise."""
        record = _level_record(_level({20: 9.0}), 30.0)

        assert _cleaned(record)[20] == 2009.0

    def test_the_gate_is_the_steeper_of_the_two_segments(self) -> None:
        """A point on the edge of a steep face is despiked."""
        record = _level_record(_level({20: 9.0}), 10.0)
        record["segments"][19] = {"angle_deg": 45.0}

        assert _cleaned(record)[20] == 2000.0

    def test_a_persistent_step_is_left_alone(self) -> None:
        """Four or more raised points are the track's own new level."""
        record = _level_record(_level({k: 9.0 for k in range(20, 28)}), 50.0)

        assert _cleaned(record)[22:26] == [2009.0] * 4

    def test_a_steady_climb_is_left_alone_however_steep(self) -> None:
        """The median of a one-way window is its middle value."""
        climb = [2000.0 + 20.0 * index for index in range(41)]
        record = _level_record(climb, 60.0)

        assert _cleaned(record) == climb

    def test_a_rise_under_the_threshold_is_left_alone(self) -> None:
        """2 m off the median is inside SPIKE_THRESHOLD_M."""
        record = _level_record(_level({20: 2.0}), 50.0)

        assert _cleaned(record)[20] == 2002.0

    def test_a_hole_far_away_is_preserved_and_the_spike_still_goes(self) -> None:
        """A None stays None (rebased from the device) outside the window."""
        heights = _level({20: 9.0})
        heights[5] = None
        record = _level_record(heights, 50.0)

        cleaned = _cleaned(record)

        assert cleaned[20] == 2000.0
        assert cleaned[5] is not None

    def test_a_hole_inside_the_window_leaves_the_point_alone(self) -> None:
        """A median over a gap is not the point's own on a slope."""
        heights = _level({20: 9.0})
        heights[18] = None
        record = _level_record(heights, 50.0)

        assert _cleaned(record)[20] == 2009.0

    def test_the_ends_of_the_track_are_left_alone(self) -> None:
        """The first and last three boundaries have no whole window."""
        record = _level_record(_level({1: 9.0, 39: 9.0}), 50.0)

        cleaned = _cleaned(record)

        assert cleaned[1] == 2009.0
        assert cleaned[39] == 2009.0

    @pytest.mark.parametrize("segments", [None, [], "nonsense"])
    def test_a_record_without_segments_is_untouched(self, segments: Any) -> None:
        """No ground angle, no claim: every height reads as recorded."""
        record = _record(_level({20: 9.0}), _STRAIGHT)
        if segments is not None:
            record["segments"] = segments

        assert _cleaned(record)[20] == 2009.0

    def test_a_segment_with_no_angle_is_never_altered(self) -> None:
        """An unknown angle does not pass the gate."""
        record = _level_record(_level({20: 9.0}), None)

        assert _cleaned(record)[20] == 2009.0

    def test_the_terrain_track_carries_the_cleaned_height(self) -> None:
        """The merged track, the profile and the totals read the same value."""
        record = _level_record(_level({20: 9.0}), 50.0)

        result = terrain_points(_STRAIGHT, record)

        assert max(_heights(result)) == 2000.0
        assert climb_totals(result) == (0.0, 0.0)

    def test_the_stored_record_is_never_mutated(self) -> None:
        """Nothing is stored: the despike is read-time only."""
        record = _level_record(_level({20: 9.0}), 50.0)
        before = copy.deepcopy(record)

        terrain_points(_STRAIGHT, record)

        assert record == before


# The boundaries the despike alters on each canonical tour, measured over
# the committed records (SNOW-1051 sweep, ±75 m / 3 m / 40°). 15 of 1,606.
_ALTERED = {
    "chamonix-col-de-balme": [],
    "hidden-valley": [225, 226, 227],
    "mont-fort-backside": [22, 23, 25, 26, 329, 330, 354, 356],
    "mont-fort-col-de-la-chaux": [468, 469, 471, 502],
}


class TestDespikeOnTheCanonicalTours:
    """The sweep's result, pinned: which boundaries move, and what does not."""

    @pytest.mark.parametrize("stem", sorted(_ALTERED))
    def test_exactly_these_boundaries_are_altered(self, stem: str) -> None:
        """Compared with the record's own heights, boundary by boundary."""
        points, record = _canonical(stem)
        cleaned = boundary_heights(points, record)
        assert cleaned is not None

        altered = [
            index
            for index, (raw, clean) in enumerate(
                zip(record["heights"], cleaned, strict=True)
            )
            if raw is not None and abs(clean - raw) > 0.05
        ]

        assert altered == _ALTERED[stem]

    @pytest.mark.parametrize("stem", sorted(_ALTERED))
    def test_no_leg_changes_kind_or_count(self, stem: str) -> None:
        """Compared with the same record with its segments removed."""
        points, record = _canonical(stem)
        undespiked = {key: value for key, value in record.items() if key != "segments"}

        with_despike = detect_legs(terrain_points(points, record))
        without = detect_legs(terrain_points(points, undespiked))

        assert [leg.climbing for leg in with_despike] == [
            leg.climbing for leg in without
        ]

    def test_the_corpus_ascent_falls_by_62_m(self) -> None:
        """62 m of 1,528 m (4.1%) across the four tours, none on Balme."""
        removed = 0.0
        for stem in _ALTERED:
            points, record = _canonical(stem)
            undespiked = {k: v for k, v in record.items() if k != "segments"}
            before = climb_totals(terrain_points(points, undespiked))[0]
            after = climb_totals(terrain_points(points, record))[0]
            assert before is not None and after is not None
            removed += before - after

        assert removed == pytest.approx(62, abs=3)

    def test_the_col_de_la_chaux_road_reads_level(self) -> None:
        """The ticket's case: boundaries 463–474 sit within 2.5 m of 2,194 m."""
        points, record = _canonical("mont-fort-col-de-la-chaux")
        cleaned = boundary_heights(points, record)
        assert cleaned is not None

        window = [height for height in cleaned[463:475] if height is not None]

        assert max(window) - min(window) < 4.0
