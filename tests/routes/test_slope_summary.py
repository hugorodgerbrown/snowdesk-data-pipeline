"""
tests/routes/test_slope_summary.py — a sampled route reduced to figures.

Covers ``apps.routes.services.slope_summary``:

  - ``class_for_angle``: every boundary lands in the class named for its
    lower bound, and a value that is not a real angle is not classified
    into the gentle one;
  - ``summarise``: the bands, the steep total, the steepest angle, an
    unknown segment contributing to the walk and to nothing else, an
    all-unknown record carrying no bands rather than six zeroes, and a
    mismatched pair refusing to summarise at all;
  - ``summarise_record``: a stored summary returned as-is, a legacy record
    summarised on the fly from its coordinates, and the two shapes that
    answer None;
  - the class table itself agreeing, value for value, with
    ``static/js/route_slope_core.js`` — the guard that stops a route being
    coloured by one table and described by another.

The lengths here are passed in rather than measured, because the
arithmetic under test is the reduction and not the geodesy —
``tests/core/test_geo.py`` is where ``haversine_m`` is established. The
one test that does measure uses a meridian track, where a degree of
latitude is a known distance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from apps.routes.services.slope_summary import (
    SLOPE_CLASSES,
    STEEP_THRESHOLD_DEG,
    class_for_angle,
    segment_lengths_from_points,
    summarise,
    summarise_record,
)

_ROUTE_SLOPE_CORE = (
    Path(__file__).parent.parent.parent / "static" / "js" / "route_slope_core.js"
)


def _known(angle_deg: float) -> dict[str, Any]:
    """Return a segment the terrain answered for."""
    return {"angle_deg": angle_deg, "aspect_deg": 180.0}


def _unknown(reason: str = "outside_coverage") -> dict[str, Any]:
    """Return a segment the terrain had no answer for."""
    return {"unknown": reason}


class TestClassForAngle:
    """The six classes, and what falls where."""

    @pytest.mark.parametrize(
        ("angle_deg", "expected_id"),
        [
            (0.0, "slope-gentle"),
            (29.9, "slope-gentle"),
            # Each boundary belongs to the class NAMED for it, which is
            # the direction that matters: the other way round would report
            # every boundary sample one class too gentle.
            (30.0, "slope-30"),
            (34.9, "slope-30"),
            (35.0, "slope-35"),
            (40.0, "slope-40"),
            (45.0, "slope-45"),
            (50.0, "slope-50"),
            (89.0, "slope-50"),
        ],
    )
    def test_an_angle_lands_in_the_class_named_for_its_lower_bound(
        self, angle_deg: float, expected_id: str
    ) -> None:
        slope_class = class_for_angle(angle_deg)
        assert slope_class is not None
        assert slope_class.id == expected_id

    def test_a_negative_angle_is_gentle_rather_than_unclassified(self) -> None:
        # The sampler cannot produce one, but a hand-written record could,
        # and falling off the end of the table would be worse than the
        # nearest honest answer.
        slope_class = class_for_angle(-1.0)
        assert slope_class is not None
        assert slope_class.id == "slope-gentle"

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_value_that_is_not_an_angle_is_not_classified_as_gentle(
        self, value: float
    ) -> None:
        # "Not a number" and "not steep" must not collapse into one answer.
        assert class_for_angle(value) is None


class TestSummarise:
    """The reduction: bands, the steep total, and the honest gaps."""

    def test_it_totals_each_class_and_the_walk(self) -> None:
        summary = summarise(
            [_known(10.0), _known(32.0), _known(37.0), _known(55.0)],
            [100.0, 200.0, 300.0, 400.0],
        )

        assert summary == {
            "sampled_m": 1000.0,
            "surveyed_m": 1000.0,
            # 200 + 300 + 400 — everything at or above 30°.
            "steep_m": 900.0,
            "bands": {
                "slope-gentle": 100.0,
                "slope-30": 200.0,
                "slope-35": 300.0,
                "slope-50": 400.0,
            },
            "steepest_deg": 55.0,
        }

    def test_the_steep_total_counts_the_threshold_itself(self) -> None:
        summary = summarise([_known(STEEP_THRESHOLD_DEG)], [100.0])
        assert summary is not None
        assert summary["steep_m"] == 100.0

    def test_an_unknown_segment_is_walked_but_not_surveyed(self) -> None:
        summary = summarise([_known(20.0), _unknown()], [100.0, 400.0])

        assert summary is not None
        # It is ground we crossed, so it counts to the walk …
        assert summary["sampled_m"] == 500.0
        # … and ground we did not see, so it counts to nothing else.
        assert summary["surveyed_m"] == 100.0
        assert summary["bands"] == {"slope-gentle": 100.0}

    def test_an_all_unknown_record_carries_no_bands_and_no_steepest(self) -> None:
        summary = summarise([_unknown(), _unknown("unavailable")], [100.0, 100.0])

        assert summary is not None
        assert summary["sampled_m"] == 200.0
        assert summary["surveyed_m"] == 0.0
        # Not six zeroes, and not a null steepest: a zero in a class is a
        # claim about ground nothing looked at.
        assert summary["bands"] == {}
        assert "steepest_deg" not in summary

    def test_a_mismatched_pair_refuses_to_summarise(self) -> None:
        # Two segments against three lengths would attribute one
        # segment's steepness to another's ground.
        assert summarise([_known(20.0), _known(40.0)], [100.0, 100.0, 100.0]) is None

    def test_an_empty_walk_summarises_to_nothing_measured(self) -> None:
        assert summarise([], []) == {
            "sampled_m": 0.0,
            "surveyed_m": 0.0,
            "steep_m": 0.0,
            "bands": {},
        }


class TestSegmentLengthsFromPoints:
    """The read-time length source."""

    def test_it_measures_between_consecutive_boundaries(self) -> None:
        # A meridian track: one hundredth of a degree of latitude is about
        # 1,111 m, and the two segments below are one and two of those.
        lengths = segment_lengths_from_points([[7.0, 46.0], [7.0, 46.01], [7.0, 46.03]])

        assert len(lengths) == 2
        assert lengths[0] == pytest.approx(1111.9, abs=1.0)
        assert lengths[1] == pytest.approx(2223.9, abs=1.0)

    def test_a_single_coordinate_bounds_no_segment(self) -> None:
        assert segment_lengths_from_points([[7.0, 46.0]]) == []


class TestSummariseRecord:
    """The one entry point, and its fallback."""

    def test_a_stored_summary_is_returned_as_it_stands(self) -> None:
        stored = {"sampled_m": 1.0, "surveyed_m": 1.0, "steep_m": 0.0, "bands": {}}
        record = {"points": [], "segments": [], "summary": stored}

        assert summarise_record(record) is stored

    def test_a_record_written_before_the_summary_is_summarised_on_read(self) -> None:
        # The legacy shape: coordinates and segments, no summary. Measured
        # from the coordinates rather than from boundaries that are gone.
        record = {
            "points": [[7.0, 46.0], [7.0, 46.01], [7.0, 46.02]],
            "segments": [_known(10.0), _known(40.0)],
        }

        summary = summarise_record(record)

        assert summary is not None
        assert summary["steepest_deg"] == 40.0
        assert summary["sampled_m"] == pytest.approx(2223.9, abs=2.0)
        assert summary["steep_m"] == pytest.approx(1111.9, abs=1.0)

    def test_a_route_that_was_never_sampled_has_no_summary(self) -> None:
        assert summarise_record(None) is None
        assert summarise_record({}) is None

    def test_a_malformed_record_has_no_summary(self) -> None:
        # Three coordinates bound two segments, not three. Same refusal
        # ``_compact_slope`` makes, and for the same reason.
        record = {
            "points": [[7.0, 46.0], [7.0, 46.01], [7.0, 46.02]],
            "segments": [_known(10.0), _known(20.0), _known(30.0)],
        }

        assert summarise_record(record) is None


class TestBandTableParity:
    """The Python table and the JavaScript one are the same table."""

    def test_the_bands_match_route_slope_core(self) -> None:
        # `map.js` paints from the JS table and the popup describes from
        # this one. A route coloured by one and described by the other
        # would contradict itself on a single screen, and nothing else in
        # the build would notice.
        source = _ROUTE_SLOPE_CORE.read_text(encoding="utf-8")
        found = re.findall(
            r"id:\s*'(?P<id>[a-z0-9-]+)',\s*from:\s*(?P<from>\d+),\s*"
            r"to:\s*(?P<to>\d+|null)",
            source,
        )

        assert found, "no CLASSES table found in route_slope_core.js"
        assert [
            (slope_class.id, slope_class.lower_deg, slope_class.upper_deg)
            for slope_class in SLOPE_CLASSES
        ] == [
            (class_id, float(lower), None if upper == "null" else float(upper))
            for class_id, lower, upper in found
        ]

    def test_the_steep_threshold_matches_route_slope_core(self) -> None:
        source = _ROUTE_SLOPE_CORE.read_text(encoding="utf-8")
        match = re.search(r"const STEEP_THRESHOLD_DEG = (\d+);", source)

        assert match is not None
        assert float(match.group(1)) == STEEP_THRESHOLD_DEG
