"""
tests/routes/test_fall_line.py — which way the ground under a route falls.

Covers ``apps.routes.services.fall_line``.

Three claims carry the module, and each of them fails QUIETLY — the map
draws, nothing throws, and the arrows are simply somewhere else or absent:

  - **A LONE STEEP SEGMENT GETS EXACTLY ONE ARROW.** One 25 m roll is
    the common case, and a spacing rule written as "every 250 m of
    track" rather than "one per run, then every 250 m" drops it
    silently. ``test_a_single_steep_segment_is_marked`` is named so it
    cannot be quietly deleted.
  - **THE SPACING RESETS AT A GAP.** A counter carried across gentle
    ground puts the next arrow at an arbitrary point of the next steep
    stretch rather than at its start, which reads as an arrow missing
    from the top of a face.
  - **THE BEARING IS THE GROUND'S ASPECT, NOT THE TRACK'S HEADING.** The
    one wrong answer that looks right. The tracks below run due NORTH
    while their aspects face elsewhere, so a mark reporting a heading
    instead of an aspect answers 0 and is caught.

The tracks are synthetic and run due north, sharing
``tests/routes/test_passages.py``'s convention — the stride is the only
length that matters here, and a real coordinate would make the arithmetic
unreadable without making it truer.
"""

from __future__ import annotations

from typing import Any

from apps.routes.services.fall_line import (
    FALL_LINE_GATE_DEG,
    FALL_LINE_SPACING_M,
    fall_line_marks,
)
from apps.routes.services.slope_summary import STEEP_THRESHOLD_DEG

# The walk's stride. Ten of them is the default spacing, which makes the
# arithmetic of every spacing test below countable by hand.
_STRIDE_M = 25.0


def _record(
    angles: list[float | None],
    aspects: list[float | None] | None = None,
    *,
    stride_m: float | None = _STRIDE_M,
) -> dict[str, Any]:
    """Return a stored slope record for the given per-segment angles.

    Args:
        angles: One angle per segment; None makes an unknown segment,
            which carries a reason and no angle at all.
        aspects: One aspect per segment. Defaults to 90 — due EAST, on a
            track running due north, so nothing can pass by reporting
            the heading.
        stride_m: The walk's stride, or None to omit the key.

    Returns:
        The record, shaped as ``Route.slope_samples``.

    """
    facings = aspects if aspects is not None else [90.0] * len(angles)
    segments: list[dict[str, Any]] = []
    for angle, aspect in zip(angles, facings, strict=True):
        if angle is None:
            segments.append({"unknown": "outside_coverage"})
            continue
        segments.append({"angle_deg": angle, "aspect_deg": aspect})

    record: dict[str, Any] = {
        # Due north at the record's six decimal places, the shape
        # ``_north_track`` builds in the passages suite.
        "points": [
            [7.0, round(46.0 + index * 0.00025, 6)] for index in range(len(angles) + 1)
        ],
        "segments": segments,
        # Every test here reads the stride, and the summary is what keeps
        # the FINAL segment on it rather than on its chord (see
        # ``slope_summary.segment_lengths_m``). Without it the last
        # segment measures ~27.8 m, which is enough to shift a spacing
        # boundary and would make these counts depend on geometry the
        # tests are not about.
        "summary": {"sampled_m": _STRIDE_M * len(angles)},
    }
    if stride_m is not None:
        record["stride_m"] = stride_m
    return record


class TestTheGate:
    """The angle at and above which a fall line is worth drawing."""

    def test_is_the_steep_threshold_the_rest_of_the_product_teaches(self) -> None:
        """The gate is ``STEEP_THRESHOLD_DEG``, and this is the mechanism.

        ``fall_line.py`` deliberately declares its own constant rather
        than importing that one, because a length COUNTED against a
        threshold and a mark DRAWN at one are two product decisions. This
        assertion is what keeps an accidental divergence from shipping: a
        deliberate one edits this test and says why, the same contract
        ``test_slope_summary.py`` uses for the JavaScript band table.
        """
        assert FALL_LINE_GATE_DEG == STEEP_THRESHOLD_DEG

    def test_is_inclusive(self) -> None:
        """Exactly 30 degrees is steep enough to carry an arrow.

        ``band_for_angle``'s convention, and the one that matters: a
        gate that excluded its own boundary would leave the gentlest
        painted band — the one the colour scale calls 30-35 — with no
        arrows at its foot.
        """
        marks = fall_line_marks(_record([FALL_LINE_GATE_DEG]))
        assert marks == [{"i": 0, "deg": 90}]

    def test_gentle_ground_is_never_marked(self) -> None:
        """A whole gentle track carries no arrows and does not fail.

        An empty list rather than None: the record was readable, and
        nothing in it qualified. That is a complete answer.
        """
        assert fall_line_marks(_record([5.0, 12.0, 29.9])) == []

    def test_an_unknown_segment_is_never_marked(self) -> None:
        """Unsurveyed ground is not ground we may point at.

        It carries no angle, so it fails the gate — the rule every
        module in this family follows, and the reason no feature can
        ever be both dashed and arrowed.
        """
        assert fall_line_marks(_record([None, None])) == []

    def test_the_gate_is_an_argument(self) -> None:
        """Re-tuning the gate costs no backfill.

        The mechanical proof: the derivation re-runs over a record
        already in memory, with nothing stored to invalidate.
        """
        marks = fall_line_marks(_record([20.0]), gate_deg=15.0)
        assert marks == [{"i": 0, "deg": 90}]


class TestNothingToRead:
    """Records that cannot produce marks at all."""

    def test_an_unsampled_route_has_no_marks(self) -> None:
        """None, not an empty list — the two are different facts.

        A never-sampled route has no ``slope`` property on the wire, so
        nothing draws it as anything; an empty list would say "we looked
        and nothing qualified", which is a claim about the ground.
        """
        assert fall_line_marks(None) is None

    def test_a_malformed_record_is_refused(self) -> None:
        """N + 1 coordinates to N segments, or nothing is drawn.

        A record whose halves disagree would put an arrow on the wrong
        ground, which is worse than putting none anywhere.
        """
        record = _record([40.0, 40.0])
        record["points"] = record["points"][:-1]
        assert fall_line_marks(record) is None

    def test_a_record_with_no_segments_is_refused(self) -> None:
        """One boundary bounds nothing."""
        assert fall_line_marks({"points": [[7.0, 46.0]], "segments": []}) is None


class TestSpacing:
    """How many arrows a stretch of steep ground gets, and where."""

    def test_a_single_steep_segment_is_marked(self) -> None:
        """A lone 25 m roll gets exactly one arrow.

        THE NAMED CASE. The spacing is ten strides, so a rule that
        waited for 250 m of steep track before marking anything would
        drop this entirely — and it is the common case, one steep roll
        on an otherwise moderate face.
        """
        marks = fall_line_marks(_record([12.0, 55.0, 12.0]))
        assert marks == [{"i": 1, "deg": 90}]

    def test_a_run_is_marked_at_its_first_segment(self) -> None:
        """The arrow lands at the top of the steep stretch, not inside it.

        A reader scanning a track for where the steep ground STARTS is
        reading the first arrow, so it has to be on the first steep
        segment rather than a spacing's worth further along.
        """
        marks = fall_line_marks(_record([10.0, 10.0, 35.0, 35.0, 35.0]))
        assert marks is not None
        assert [mark["i"] for mark in marks] == [2]

    def test_a_long_run_is_marked_every_spacing(self) -> None:
        """Ten strides of steep ground between two arrows.

        Twenty segments at 25 m is 500 m, which carries the run's own
        first mark and then one at each subsequent 250 m.
        """
        marks = fall_line_marks(_record([40.0] * 20))
        assert marks is not None
        assert [mark["i"] for mark in marks] == [0, 10]

    def test_the_spacing_is_measured_in_metres_of_track(self) -> None:
        """Not in segments — the record's own stride decides.

        A record with a 50 m stride reaches the spacing in five
        segments rather than ten, which is the assertion that stops the
        counter being a segment count wearing a metric name.
        """
        marks = fall_line_marks(_record([40.0] * 12, stride_m=50.0))
        assert marks is not None
        assert [mark["i"] for mark in marks] == [0, 5, 10]

    def test_the_spacing_resets_at_a_gentle_gap(self) -> None:
        """Each steep stretch starts due an arrow of its own.

        A counter carried across the gap would put the second face's
        first arrow part-way down it, which reads as an arrow missing
        from the top.
        """
        marks = fall_line_marks(_record([40.0, 40.0, 5.0, 40.0, 40.0]))
        assert marks is not None
        assert [mark["i"] for mark in marks] == [0, 3]

    def test_the_spacing_resets_across_unsurveyed_ground(self) -> None:
        """An unknown segment ends the run, exactly as gentle ground does."""
        marks = fall_line_marks(_record([40.0, None, 40.0]))
        assert marks is not None
        assert [mark["i"] for mark in marks] == [0, 2]

    def test_the_spacing_is_an_argument(self) -> None:
        """Re-tuning the density costs no backfill either."""
        marks = fall_line_marks(_record([40.0] * 4), spacing_m=50.0)
        assert marks is not None
        assert [mark["i"] for mark in marks] == [0, 2]

    def test_the_default_spacing_is_ten_strides(self) -> None:
        """The constant the counts above are read against.

        Stated once here so a change to it fails one named test rather
        than five arithmetic ones, and the reader of that failure is
        told what the number was for.
        """
        assert FALL_LINE_SPACING_M == _STRIDE_M * 10


class TestTheBearing:
    """What an arrow actually points at."""

    def test_is_the_ground_s_aspect_not_the_track_s_heading(self) -> None:
        """The one wrong answer that looks right.

        The track runs due north throughout; the ground faces
        south-west. An implementation that measured the chord — the
        gradient-along-the-track mistake, one level up — would answer 0.
        """
        marks = fall_line_marks(_record([40.0], aspects=[225.0]))
        assert marks == [{"i": 0, "deg": 225}]

    def test_is_rounded_to_a_whole_degree(self) -> None:
        """A tenth of a degree moves a 20px arrow's tip by a hundredth
        of a pixel, and the bearing came from a 10 m window besides.
        """
        marks = fall_line_marks(_record([40.0], aspects=[112.4]))
        assert marks == [{"i": 0, "deg": 112}]

    def test_due_north_is_reported_as_zero_not_360(self) -> None:
        """Normalised AFTER rounding.

        359.7 rounds to 360, which is a bearing off the end of the
        compass; ``icon-rotate`` would accept it and a reader comparing
        two marks would not, so it is reported as the 0 it is.
        """
        marks = fall_line_marks(_record([40.0], aspects=[359.7]))
        assert marks == [{"i": 0, "deg": 0}]

    def test_a_steep_segment_with_no_aspect_is_not_marked(self) -> None:
        """A null reaching ``icon-rotate`` would be drawn as due north.

        ``TerrainSlope`` guarantees a sloping sample carries an aspect,
        so this is a hand-written record — but the wrong answer here
        looks exactly like a right one, which is why it is refused
        rather than defaulted.
        """
        assert fall_line_marks(_record([40.0], aspects=[None])) == []

    def test_an_unmarkable_segment_does_not_consume_the_spacing(self) -> None:
        """The run's arrow survives a first segment that cannot say where.

        Resetting the counter on a segment that produced no mark would
        silence the whole stretch for a spacing's worth of track, which
        on a short face means silencing it entirely.
        """
        marks = fall_line_marks(_record([40.0, 40.0], aspects=[None, 130.0]))
        assert marks == [{"i": 1, "deg": 130}]
