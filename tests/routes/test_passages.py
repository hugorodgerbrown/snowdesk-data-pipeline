"""
tests/routes/test_passages.py — the stretches where the track is no-fall.

Covers ``apps.routes.services.passages`` (SNOW-964).

Two claims carry the module, and both have a way of failing silently:

  - **A ONE-SEGMENT PASSAGE IS THE COMMON CASE.** One steep roll between
    two gentler samples is 25 m of track, and measuring it with the chord
    between its two boundaries makes it shorter than the stride it stands
    for — so a 25 m minimum tested against a chord drops most of what the
    feature is for, and the map goes quiet rather than wrong.
    ``test_a_single_stride_passage_is_not_dropped_by_the_minimum`` is
    named so it cannot be quietly deleted.
  - **THE LABEL DOES NOT FILTER.** A rising traverse of 50 degree ground
    is no-fall ground, and a passage whose aspect nothing could measure
    is still a passage. Both are asserted here, because the honest
    failure of this feature is a mark that is absent.

The tracks are synthetic and run due north, so the bearing of every
segment is 0 and an aspect can be chosen to produce any alignment
directly. Real coordinates are established in ``tests/core/test_geo.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.routes.services.passages import (
    CLIMBING,
    CROSSING,
    DESCENDING,
    FALL_LINE_TOLERANCE_DEG,
    NO_FALL_GATE_DEG,
    angular_difference,
    fall_line_alignment,
    passage_alignment_detail,
    route_passages,
)

# The latitude step between two synthetic boundaries, and the chord it
# makes. A round figure at the record's own six decimal places, so every
# generated chord is the same length rather than varying with rounding —
# and deliberately NOT the 25 m stride, so a test can tell which of the
# two a length came from.
_LATITUDE_STEP = 0.00025
_CHORD_M = 27.8


def _north_track(segment_count: int) -> list[list[float]]:
    """Return N + 1 boundary coordinates running due north.

    Args:
        segment_count: How many segments the track holds.

    Returns:
        ``[[lon, lat], …]``, at the record's six places.

    """
    return [
        [7.0, round(46.0 + index * _LATITUDE_STEP, 6)]
        for index in range(segment_count + 1)
    ]


def _record(
    angles: list[float | None],
    aspects: list[float | None] | None = None,
    *,
    stride_m: float | None = 25.0,
    points: list[list[float]] | None = None,
    sampled_m: float | None = None,
) -> dict[str, Any]:
    """Return a stored slope record for the given per-segment angles.

    Args:
        angles: One angle per segment; None makes an unknown segment,
            which carries a reason and no angle at all.
        aspects: One aspect per segment. Defaults to due north on every
            segment, which on a northbound track is a descent.
        stride_m: The walk's stride, or None to omit the key entirely —
            a record written before the sampler stored one.
        points: Override the generated boundaries, for the degenerate
            and short-chord cases.
        sampled_m: The walk's own total length, written under
            ``summary``. None omits the key, which is a record predating
            SNOW-961 and is what makes the final segment fall back to
            its chord.

    Returns:
        The record, shaped as ``Route.slope_samples``.

    """
    facings = aspects if aspects is not None else [0.0] * len(angles)
    segments: list[dict[str, Any]] = []
    for angle, aspect in zip(angles, facings, strict=True):
        if angle is None:
            segments.append({"unknown": "outside_coverage"})
            continue
        segments.append({"angle_deg": angle, "aspect_deg": aspect})

    record: dict[str, Any] = {
        "points": points if points is not None else _north_track(len(angles)),
        "segments": segments,
    }
    if stride_m is not None:
        record["stride_m"] = stride_m
    if sampled_m is not None:
        record["summary"] = {"sampled_m": sampled_m}
    return record


# Three segments whose LAST chord is about 12 m — half a stride — while
# the two before it are full strides. The shape every final-segment test
# below needs: a track that ends in a bend.
_BENT_TAIL_POINTS: list[list[float]] = [
    [7.0, 46.0],
    [7.0, 46.00025],
    [7.0, 46.0005],
    [7.0, 46.00061],
]


class TestAngularDifference:
    """The separation between two bearings, the short way round."""

    @pytest.mark.parametrize(
        ("first", "second", "expected"),
        [
            (0.0, 0.0, 0.0),
            (0.0, 90.0, 90.0),
            (0.0, 180.0, 180.0),
            (0.0, 270.0, 90.0),
            (350.0, 10.0, 20.0),
            (10.0, 350.0, 20.0),
            (359.0, 1.0, 2.0),
        ],
    )
    def test_takes_the_short_way_round(
        self, first: float, second: float, expected: float
    ) -> None:
        """350 and 10 are 20 degrees apart, not 340."""
        assert angular_difference(first, second) == pytest.approx(expected)

    def test_is_never_more_than_a_half_turn(self) -> None:
        """Every answer is in ``[0, 180]``, which is what makes it symmetric."""
        for first in range(0, 360, 7):
            for second in range(0, 360, 11):
                assert 0.0 <= angular_difference(float(first), float(second)) <= 180.0


class TestFallLineAlignment:
    """What a track is doing with the ground's fall line."""

    def test_with_the_fall_line_is_descending(self) -> None:
        """A track pointing where the ground faces is going down it."""
        assert fall_line_alignment(0.0, 0.0) == DESCENDING

    def test_against_the_fall_line_is_climbing(self) -> None:
        """A track pointing into the slope is going up it."""
        assert fall_line_alignment(0.0, 180.0) == CLIMBING

    def test_across_the_fall_line_is_crossing(self) -> None:
        """A track at right angles to the fall line is traversing."""
        assert fall_line_alignment(0.0, 90.0) == CROSSING

    def test_the_tolerance_is_inclusive(self) -> None:
        """Exactly at the tolerance is still a descent, a shade past is not.

        The boundary is inclusive both ways, matching ``class_for_angle``'s
        convention: a value that lands exactly on a threshold belongs to
        the class named for it.
        """
        assert fall_line_alignment(0.0, FALL_LINE_TOLERANCE_DEG) == DESCENDING
        assert fall_line_alignment(0.0, FALL_LINE_TOLERANCE_DEG + 0.1) == CROSSING
        assert fall_line_alignment(0.0, 180.0 - FALL_LINE_TOLERANCE_DEG) == CLIMBING

    def test_a_missing_input_has_no_label(self) -> None:
        """Level ground faces nowhere and a coincident chord points nowhere.

        Neither is a fourth label: a segment that cannot be measured casts
        no vote rather than voting for "unknown".
        """
        assert fall_line_alignment(None, 0.0) is None
        assert fall_line_alignment(0.0, None) is None
        assert fall_line_alignment(None, None) is None

    def test_the_tolerance_is_an_argument(self) -> None:
        """Widening the tolerance moves a crossing into a descent."""
        assert fall_line_alignment(0.0, 40.0) == CROSSING
        assert fall_line_alignment(0.0, 40.0, tolerance_deg=45.0) == DESCENDING


class TestNothingToRead:
    """The record states that produce no answer at all."""

    def test_an_unsampled_route_has_no_passages(self) -> None:
        """None in, None out — never an empty list, which would be a claim."""
        assert route_passages(None) is None
        assert route_passages({}) is None

    def test_a_malformed_record_is_refused(self) -> None:
        """N + 1 coordinates to N segments, or nothing is marked.

        The same refusal ``compact_slope`` and ``summarise_record`` make:
        a mark placed against the wrong ground is worse than no mark.
        """
        record = _record([52.0, 52.0])
        record["points"] = record["points"][:-1]
        assert route_passages(record) is None

    def test_a_record_with_no_segments_is_refused(self) -> None:
        """A track too short to hold a segment holds no passage either."""
        assert route_passages({"points": [[7.0, 46.0]], "segments": []}) is None


class TestSeeding:
    """Which segments start a passage."""

    def test_the_gate_is_inclusive(self) -> None:
        """Exactly at the gate seeds; a tenth under does not.

        Inclusive, matching ``class_for_angle`` — a sample of exactly 50.0
        is painted ``slope-50``, so it must be exactly as much of a seed
        as the colour under it says it is.
        """
        under = route_passages(_record([10.0, NO_FALL_GATE_DEG - 0.1, 10.0]))
        assert under == []
        at = route_passages(_record([10.0, NO_FALL_GATE_DEG, 10.0]))
        assert at is not None
        assert [(p["from"], p["to"]) for p in at] == [(1, 1)]

    def test_the_grow_floor_alone_marks_nothing(self) -> None:
        """A whole track in the 45–50 band is not a passage.

        The floor exists to EXTEND a passage, never to start one, or every
        moderate-steep face in the Alps would be marked.
        """
        assert route_passages(_record([46.0, 47.0, 48.0, 49.0])) == []

    def test_a_clean_record_answers_an_empty_list(self) -> None:
        """Gentle ground everywhere is a complete answer, not a missing one.

        Unlike ``cruxes``, a passage list needs no probe and so can never
        be incomplete — there is no "we could not look" state to encode.
        """
        assert route_passages(_record([12.0, 18.0, 25.0])) == []


class TestGrouping:
    """How the segments either side of a seed are gathered in."""

    def test_a_passage_grows_outward_in_both_directions(self) -> None:
        """The high-forties segments either side of a roll are the same passage.

        A face is not a step function and a sampled angle is an average
        over a 10 m window, so the ground beside a 50 degree roll is
        usually in the band below it and is part of the same committing
        stretch.
        """
        passages = route_passages(_record([20.0, 46.0, 52.0, 47.0, 20.0]))
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(1, 3)]

    def test_two_seeds_in_one_stretch_are_one_passage(self) -> None:
        """Runs that meet are merged, so a face is not reported three times."""
        passages = route_passages(_record([52.0, 46.0, 53.0]))
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(0, 2)]

    def test_gentle_ground_separates_two_passages(self) -> None:
        """A genuine break in the steep ground is a break in the marking."""
        passages = route_passages(_record([52.0, 20.0, 52.0]))
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(0, 0), (2, 2)]

    def test_an_unknown_segment_is_never_inside_a_passage(self) -> None:
        """Unsurveyed ground stops a run and is never marked.

        This is why ``trip-route-slope-unknown`` needs no change: no segment
        can ever carry both ``unknown`` and ``passage``.
        """
        passages = route_passages(_record([52.0, None, 52.0]))
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(0, 0), (2, 2)]

    def test_a_passage_can_run_to_the_end_of_the_track(self) -> None:
        """A run still open at the last segment is closed and reported."""
        passages = route_passages(_record([20.0, 52.0, 52.0]))
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(1, 2)]


class TestLengths:
    """Where a passage's metres come from — and where they must not."""

    def test_a_single_stride_passage_is_not_dropped_by_the_minimum(self) -> None:
        """One 25 m segment survives even when its chord is far shorter.

        THE TRAP THIS WHOLE TEST EXISTS FOR. The chord between two
        boundaries is shorter than the track it stands for — a switchback
        is the extreme — so measuring the passage with chords and then
        testing it against a 25 m minimum would drop the single-segment
        case, which is the common one. The track here doubles back so its
        middle chord is about 12 m; the passage must still be 25 m.
        """
        # A middle segment whose two boundaries are half a stride apart.
        points = [
            [7.0, 46.0],
            [7.0, 46.00025],
            [7.0, 46.00036],  # ~12 m on from the last, not a full stride
            [7.0, 46.00061],
        ]
        passages = route_passages(
            _record([10.0, 52.0, 10.0], points=points, stride_m=25.0)
        )
        assert passages is not None
        assert len(passages) == 1
        assert passages[0]["m"] == pytest.approx(25.0)

    def test_the_metres_are_the_sum_of_the_passage_s_strides(self) -> None:
        """A three-segment passage in the middle of a track is three strides."""
        passages = route_passages(_record([10.0, 52.0, 52.0, 52.0, 10.0]))
        assert passages is not None
        assert passages[0]["m"] == pytest.approx(75.0)

    def test_a_final_segment_passage_is_recovered_from_the_summary(self) -> None:
        """The stub-absorbing last segment is recovered, never chorded.

        THE SAME TRAP AS THE FIRST TEST IN THIS CLASS, on the one segment
        that used to be exempt from the fix. ``stride_distances`` folds
        the track's remainder into the final segment, so it is the one
        length the stride does not state — and measuring it as a chord
        across a bend loses most of it. Here the tail is a genuine 30 m
        of track whose chord is about 12, and a passage that is only that
        segment has to survive the 25 m minimum.
        """
        passages = route_passages(
            _record(
                [10.0, 10.0, 52.0],
                points=_BENT_TAIL_POINTS,
                stride_m=25.0,
                sampled_m=80.0,  # 25 + 25 + a 30 m tail
            )
        )
        assert passages is not None
        assert len(passages) == 1
        assert passages[0]["from"] == 2
        assert passages[0]["m"] == pytest.approx(30.0)

    def test_a_final_segment_without_a_summary_falls_back_to_its_chord(self) -> None:
        """A record predating SNOW-961 has nothing to recover the tail from.

        The chord is then the only figure available and it under-measures
        the bend, so this passage is dropped. Asserted rather than
        wished away: it is the bounded, documented cost of an old record,
        and it is the reason the summary is preferred wherever there is
        one.
        """
        passages = route_passages(
            _record([10.0, 10.0, 52.0], points=_BENT_TAIL_POINTS, stride_m=25.0)
        )
        assert passages == []

    def test_a_summary_that_contradicts_the_segments_is_not_trusted(self) -> None:
        """A remainder outside half-to-one-and-a-half strides is refused.

        ``stride_distances`` bounds the final segment to that range, so a
        summary implying anything else describes a different walk from
        the one the segments describe. Two figures that cannot both be
        right, and the geometry is the one to believe.
        """
        passages = route_passages(
            _record(
                [10.0, 10.0, 52.0],
                points=_BENT_TAIL_POINTS,
                stride_m=25.0,
                sampled_m=500.0,  # implies a 450 m final segment
            )
        )
        assert passages == []

    def test_a_record_with_no_stride_falls_back_to_chords(self) -> None:
        """A record written before the stride was stored measures its geometry.

        All that survives in one of those is the boundary COORDINATES, so
        the chord is the only length available — the read-time half of the
        pair ``slope_summary`` describes.
        """
        passages = route_passages(_record([10.0, 52.0, 10.0], stride_m=None))
        assert passages is not None
        assert passages[0]["m"] == pytest.approx(_CHORD_M, abs=0.2)

    def test_a_stub_shorter_than_the_minimum_is_dropped(self) -> None:
        """The track's last segment is measured as the chord it really is.

        ``stride_distances`` absorbs a track's trailing remainder into its
        last segment, which can be as short as half a stride — so that one
        segment takes its chord, and a short one falls under the minimum.
        """
        points = [[7.0, 46.0], [7.0, 46.00025], [7.0, 46.00029]]  # ~4 m stub
        passages = route_passages(_record([10.0, 52.0], points=points))
        assert passages == []


class TestAlignment:
    """The word beside a passage, and the vote that chooses it."""

    def test_a_descent_of_the_fall_line(self) -> None:
        """A northbound track on north-facing ground is descending."""
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, 0.0, None]))
        assert passages is not None
        assert passages[0]["fall_line"] == DESCENDING

    def test_a_climb_of_the_fall_line(self) -> None:
        """A northbound track on south-facing ground is climbing."""
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, 180.0, None]))
        assert passages is not None
        assert passages[0]["fall_line"] == CLIMBING

    def test_a_traverse_is_still_a_passage(self) -> None:
        """A rising traverse of 50 degree ground is no-fall ground too.

        The alignment LABELS, it does not filter. Excluding the traverse
        would make it the silent case, which is the one a reader is most
        likely to misjudge.
        """
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, 90.0, None]))
        assert passages is not None
        assert len(passages) == 1
        assert passages[0]["fall_line"] == CROSSING

    def test_the_most_covered_label_wins(self) -> None:
        """Two segments descending and one climbing is a descent."""
        passages = route_passages(
            _record([52.0, 52.0, 52.0], [0.0, 0.0, 180.0]),
        )
        assert passages is not None
        assert passages[0]["fall_line"] == DESCENDING

    def test_a_tie_goes_to_crossing(self) -> None:
        """One segment each way is not a descent, and not a climb either.

        ``crossing`` is the label that claims least, so it is where a
        passage with the evidence for neither reading lands.
        """
        passages = route_passages(
            _record([10.0, 52.0, 52.0, 10.0], [None, 0.0, 180.0, None]),
        )
        assert passages is not None
        assert passages[0]["fall_line"] == CROSSING

    def test_a_passage_nothing_could_classify_carries_no_key(self) -> None:
        """Level ground faces nowhere, and the passage is still reported.

        ABSENT, never null — the rule ``summary["steepest_deg"]``
        follows. Steep ground is what earns the mark; a missing aspect is
        a fact about the survey, not about the track.
        """
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, None, None]))
        assert passages is not None
        assert len(passages) == 1
        assert "fall_line" not in passages[0]

    def test_a_degenerate_chord_casts_no_vote(self) -> None:
        """A segment between two identical boundaries has no direction.

        Its aspect here would say "descending" if it were allowed to
        vote, so a climbing answer is the evidence that it was not — a
        vote from it would have made this a tie and answered
        ``crossing``.
        """
        points = [
            [7.0, 46.0],
            [7.0, 46.0],  # the same coordinate twice: no bearing at all
            [7.0, 46.00025],
            [7.0, 46.00050],
        ]
        passages = route_passages(
            _record([52.0, 52.0, 10.0], [0.0, 180.0, None], points=points),
        )
        assert passages is not None
        assert passages[0]["fall_line"] == CLIMBING


class TestAlignmentDetail:
    """The per-segment votes the label was reached from.

    The instrument's half of SNOW-964's tuning story, and the reason it
    exists: ``FALL_LINE_TOLERANCE_DEG`` acts on a SEGMENT while the
    alignment table counts PASSAGES, with the coverage vote in between —
    so a passage-level table cannot say whether a ``crossing`` came from
    the tolerance's residue or from the tie-break. None of this reaches
    the wire; ``TestTheWireShape`` below is what holds that line.
    """

    def test_one_entry_per_segment_in_track_order(self) -> None:
        """Every segment of the passage is reported, with its own angle."""
        record = _record([52.0, 52.0], [20.0, 100.0])
        detail = passage_alignment_detail(record, 0, 1)
        assert detail is not None
        assert [segment.index for segment in detail.segments] == [0, 1]
        assert [segment.delta_deg for segment in detail.segments] == [
            pytest.approx(20.0),
            pytest.approx(100.0),
        ]
        assert [segment.label for segment in detail.segments] == [DESCENDING, CROSSING]

    def test_a_segment_with_no_aspect_has_no_angle_and_casts_no_vote(self) -> None:
        """Level ground faces nowhere, so there is no angle to bucket.

        It must be visible as unmeasured rather than dropped: a histogram
        that silently omits what it could not read overstates how much of
        the terrain it describes.
        """
        record = _record([52.0, 52.0], [None, 180.0])
        detail = passage_alignment_detail(record, 0, 1)
        assert detail is not None
        assert detail.segments[0].delta_deg is None
        assert detail.segments[0].label is None
        assert detail.label == CLIMBING

    def test_a_degenerate_chord_has_no_angle_and_casts_no_vote(self) -> None:
        """A segment between two identical boundaries points nowhere.

        Its aspect would say ``descending`` if it were allowed to vote,
        so the climbing answer is the evidence that it was not.
        """
        points = [
            [7.0, 46.0],
            [7.0, 46.0],  # the same coordinate twice: no bearing at all
            [7.0, 46.00025],
        ]
        record = _record([52.0, 52.0], [0.0, 180.0], points=points)
        detail = passage_alignment_detail(record, 0, 1)
        assert detail is not None
        assert detail.segments[0].delta_deg is None
        assert detail.segments[0].label is None
        assert detail.label == CLIMBING

    def test_a_split_passage_records_that_a_tie_chose_its_label(self) -> None:
        """One segment each way is a ``crossing`` the tie-break produced."""
        record = _record([10.0, 52.0, 52.0, 10.0], [None, 0.0, 180.0, None])
        detail = passage_alignment_detail(record, 1, 2)
        assert detail is not None
        assert detail.label == CROSSING
        assert detail.resolved_by_tie is True

    def test_a_clear_winner_is_not_a_tie(self) -> None:
        """Three segments descending against one climbing is no tie."""
        record = _record([52.0, 52.0, 52.0, 52.0, 10.0], [0.0, 0.0, 0.0, 180.0, None])
        detail = passage_alignment_detail(record, 0, 3)
        assert detail is not None
        assert detail.label == DESCENDING
        assert detail.resolved_by_tie is False

    @pytest.mark.parametrize(
        "aspects",
        [
            [None, 0.0, None],
            [None, 180.0, None],
            [None, 90.0, None],
            [None, None, None],
        ],
    )
    def test_the_detail_agrees_with_the_reported_label(
        self, aspects: list[float | None]
    ) -> None:
        """The vote is computed once, so the two can never drift apart.

        The whole reason the walk was extracted rather than reimplemented
        in the tuning command: a histogram of a second measurement would
        describe something nothing ships.
        """
        record = _record([10.0, 52.0, 10.0], aspects)
        passages = route_passages(record)
        assert passages is not None
        detail = passage_alignment_detail(record, 1, 1)
        assert detail is not None
        assert passages[0].get("fall_line") == detail.label

    def test_an_unreadable_record_has_no_detail(self) -> None:
        """The same refusals ``route_passages`` makes, for the same reason."""
        assert passage_alignment_detail(None, 0, 0) is None
        assert passage_alignment_detail({}, 0, 0) is None
        malformed = _record([52.0, 52.0])
        malformed["points"] = malformed["points"][:-1]
        assert passage_alignment_detail(malformed, 0, 1) is None


class TestTheWireShape:
    """What a passage dict carries, key for key.

    ``route_passages``' dicts go straight to the wire —
    ``apps.routes.services.slope_wire`` puts them in ``passages`` and
    ``static/js/route_slope_core.js`` reads ``fall_line`` off them — so
    the keys are an interface, not an implementation detail. SNOW-964
    argues deliberately against a raw bearing-derived NUMBER reaching a
    client, which would invite a barb drawn on a direction measured from
    one 25 m chord; SNOW-971 added exactly such numbers for the tuning
    command, and this is the test that keeps them off the wire.
    """

    def test_a_classified_passage_carries_four_keys_and_no_more(self) -> None:
        """``from``, ``to``, ``m`` and ``fall_line`` — nothing else."""
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, 0.0, None]))
        assert passages is not None
        assert set(passages[0]) == {"from", "to", "m", "fall_line"}

    def test_an_unclassifiable_passage_carries_three(self) -> None:
        """``fall_line`` is ABSENT, never null, and no key replaces it."""
        passages = route_passages(_record([10.0, 52.0, 10.0], [None, None, None]))
        assert passages is not None
        assert set(passages[0]) == {"from", "to", "m"}


class TestTheGatesAreArguments:
    """Re-tuning must cost nothing, and this is the proof.

    The four thresholds are keyword arguments with the module constants as
    defaults, so ``report_route_passages`` can sweep candidates over a
    record it has already loaded — no second query, and nothing stored
    that a new threshold would invalidate.

    ``tests/routes/test_slope_summary.py``'s band-table parity has no
    counterpart here ON PURPOSE: these four constants have no JavaScript
    twin to drift from, because the client is handed a label and never a
    threshold.
    """

    def test_the_gates_are_arguments(self) -> None:
        """A lower gate marks ground the default leaves alone."""
        record = _record([10.0, 36.0, 10.0])
        assert route_passages(record) == []
        lowered = route_passages(record, gate_deg=35.0, floor_deg=35.0)
        assert lowered is not None
        assert [(p["from"], p["to"]) for p in lowered] == [(1, 1)]

    def test_the_floor_is_an_argument(self) -> None:
        """Raising the floor to the gate stops a passage growing outward."""
        record = _record([46.0, 52.0, 46.0])
        grown = route_passages(record)
        assert grown is not None
        assert [(p["from"], p["to"]) for p in grown] == [(0, 2)]
        tight = route_passages(record, floor_deg=NO_FALL_GATE_DEG)
        assert tight is not None
        assert [(p["from"], p["to"]) for p in tight] == [(1, 1)]

    def test_a_floor_above_the_gate_still_holds_its_seed(self) -> None:
        """A sweep can pair a floor above the gate; it must answer, not misbehave."""
        passages = route_passages(
            _record([10.0, 52.0, 10.0]), gate_deg=50.0, floor_deg=60.0
        )
        assert passages is not None
        assert [(p["from"], p["to"]) for p in passages] == [(1, 1)]

    def test_the_minimum_is_an_argument(self) -> None:
        """Raising the minimum drops a single-stride passage."""
        record = _record([10.0, 52.0, 10.0])
        assert route_passages(record, min_m=60.0) == []

    def test_the_tolerance_reaches_the_label(self) -> None:
        """The tolerance argument changes what a passage is called."""
        record = _record([10.0, 52.0, 10.0], [None, 40.0, None])
        passages = route_passages(record)
        assert passages is not None
        assert passages[0]["fall_line"] == CROSSING
        widened = route_passages(record, tolerance_deg=45.0)
        assert widened is not None
        assert widened[0]["fall_line"] == DESCENDING
