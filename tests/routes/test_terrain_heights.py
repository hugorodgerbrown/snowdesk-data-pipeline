"""
tests/routes/test_terrain_heights.py — a track's heights from the model (SNOW-1043).

Covers ``apps.routes.services.terrain_heights``:

  - ``terrain_points``: the third ordinate replaced by the record's model
    heights, interpolated along the track; the device value kept where a
    bracketing height is null; the track returned as stored when the record
    has no heights or was sampled from different points; the input never
    mutated;
  - ``climb_totals``: the ingest rule for ascent and descent, on any track;
  - ``has_terrain_heights``: a list of nulls is not a record with heights;
  - ``climb_figures``: the one rule every surface shows ascent and descent
    by — the model's totals with heights, the stored columns without;
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
from apps.routes.services.legs import detect_legs
from apps.routes.services.slope_segments import SAMPLE_STRIDE_M
from apps.routes.services.terrain_heights import (
    ClimbFigures,
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


def _record(heights: list[float | None]) -> dict[str, Any]:
    """Return a minimal slope record for ``_TRACK`` carrying ``heights``.

    ``_TRACK`` is about 111 m, so the stride walk puts boundaries at 0,
    25, 50, 75 and the end — five, the last absorbing the stub.

    Args:
        heights: One height per boundary.

    Returns:
        A record with a stride and the given heights.

    """
    return {"stride_m": SAMPLE_STRIDE_M, "heights": heights}


class TestTerrainPoints:
    """The track with its heights read from the model."""

    def test_heights_are_interpolated_along_the_track(self) -> None:
        """Start and end take the end boundaries; the middle is between."""
        result = terrain_points(
            _TRACK, _record([2000.0, 2010.0, 2020.0, 2030.0, 2040.0])
        )

        assert [point[:2] for point in result] == [point[:2] for point in _TRACK]
        assert result[0][2] == 2000.0
        assert result[-1][2] == 2040.0
        # 55.6 m along: between the 50 m and 75 m boundaries.
        assert result[1][2] == pytest.approx(2022.2, abs=0.1)

    def test_a_null_height_keeps_the_device_value(self) -> None:
        """Where the model has no ground, the recorded height stands."""
        result = terrain_points(_TRACK, _record([2000.0, 2010.0, None, 2030.0, 2040.0]))

        assert result[0][2] == 2000.0
        # The middle point falls between the null boundary and the next.
        assert result[1][2] == 2200.0
        assert result[-1][2] == 2040.0

    def test_a_record_without_heights_returns_the_track_unchanged(self) -> None:
        """A record written before SNOW-1043 changes nothing."""
        assert terrain_points(_TRACK, {"stride_m": SAMPLE_STRIDE_M}) == _TRACK
        assert terrain_points(_TRACK, None) == _TRACK

    def test_a_record_of_all_null_heights_returns_the_track_unchanged(self) -> None:
        """Wholly outside the model's coverage is as good as no heights."""
        assert terrain_points(_TRACK, _record([None] * 5)) == _TRACK

    def test_a_record_from_other_points_returns_the_track_unchanged(self) -> None:
        """A boundary count that does not match means the wrong ground."""
        assert terrain_points(_TRACK, _record([2000.0, 2010.0])) == _TRACK

    def test_a_record_with_no_stride_returns_the_track_unchanged(self) -> None:
        """No stride, no way to repeat the walk."""
        assert terrain_points(_TRACK, {"heights": [2000.0] * 5}) == _TRACK

    def test_the_input_is_never_mutated(self) -> None:
        """The stored track is read, never rewritten."""
        track = copy.deepcopy(_TRACK)
        terrain_points(track, _record([2000.0] * 5))

        assert track == _TRACK


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

        Measured on the committed record: net -547.6 m (570.0 m of
        descent), +260.6 m, then -1,281.3 m (1,309.5 m of descent).
        """
        points, record = _canonical("mont-fort-backside")

        legs = detect_legs(terrain_points(points, record))

        assert [leg.climbing for leg in legs] == [False, True, False]
        assert legs[0].descent_m == pytest.approx(570.0, abs=10)
        assert legs[1].ascent_m == pytest.approx(270.3, abs=10)
        assert legs[2].descent_m == pytest.approx(1309.5, abs=10)

    def test_the_model_heights_cut_the_phantom_climb_from_the_totals(self) -> None:
        """Ascent falls from 478.8 m recorded to about 321 m on the model."""
        points, record = _canonical("mont-fort-backside")

        ascent, descent = climb_totals(terrain_points(points, record))

        assert ascent == pytest.approx(320.9, abs=10)
        assert descent == pytest.approx(1889.2, abs=10)

    def test_every_point_of_a_covered_track_takes_a_model_height(self) -> None:
        """Mont Fort is inside the model's coverage end to end."""
        _, record = _canonical("mont-fort-backside")

        assert None not in record["heights"]
        assert len(record["heights"]) == len(record["points"])


class TestChamonixAcrossTheCoverageEdge:
    """A track partly outside the model keeps its recorded heights there."""

    def test_uncovered_points_keep_the_device_value(self) -> None:
        """The French end of the track has no model height to take."""
        points, record = _canonical("chamonix-col-de-balme")

        result = terrain_points(points, record)

        kept = sum(
            1
            for stored, read in zip(points, result, strict=True)
            if stored[2] == read[2]
        )
        assert None in record["heights"]
        assert 0 < kept < len(points)
