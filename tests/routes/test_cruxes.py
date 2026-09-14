"""
tests/routes/test_cruxes.py — the passages where the ground above can go.

Covers ``apps.routes.services.cruxes`` (SNOW-911).

The claim this module exists to make is the one a per-point angle cannot:
**a flat traverse under a steep face is a crux.** That case is the first
test here and the reason for all the rest. Its converse matters as much —
a track along the TOP of the same face must NOT be marked, because ground
below cannot be released onto the skier — and it is what the uphill arc
buys over a plain radius search.

The probes are patched per coordinate rather than the transport: the
walk's own answers are established in
``tests/locations/services/test_terrain.py``, and what is under test here
is which ground gets asked about and what is concluded from the answers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.core.geo import destination, haversine_m
from apps.locations.services.terrain import TerrainSlope, TerrainUnknown
from apps.routes.services.cruxes import (
    CRUX_THRESHOLD_DEG,
    PROBE_RADII_M,
    crux_points,
    is_crux,
    uphill_max_angle,
)

_SAMPLE = "apps.routes.services.cruxes.sample_slope"


def _unavailable() -> TerrainSlope:
    """Return the answer an unreachable tile origin gives."""
    return TerrainSlope(
        angle_deg=None,
        aspect_deg=None,
        window_m=10.0,
        unknown=TerrainUnknown.UNAVAILABLE,
        source=None,
    )


def _slope(angle_deg: float | None) -> TerrainSlope:
    """Return a sampler answer, or an out-of-coverage one for None."""
    if angle_deg is None:
        return TerrainSlope(
            angle_deg=None,
            aspect_deg=None,
            window_m=10.0,
            unknown=TerrainUnknown.OUTSIDE_COVERAGE,
            source=None,
        )
    return TerrainSlope(
        angle_deg=angle_deg,
        aspect_deg=180.0,
        window_m=10.0,
        unknown=None,
        source=None,
    )


class TestUphillMaxAngle:
    """Which ground is asked about, and what comes back."""

    def test_it_finds_the_steep_face_a_flat_traverse_runs_under(self) -> None:
        """The whole reason this module exists.

        The track is flat and faces north (aspect 0), so uphill is south.
        The steep ground is placed there; a per-point angle would report
        the traverse as gentle and paint it so.
        """

        def _answer(latitude: float, longitude: float, window_m: Any) -> TerrainSlope:
            # South of the track is uphill: a lower latitude.
            return _slope(42.0 if latitude < 46.0 else 5.0)

        with patch(_SAMPLE, side_effect=_answer):
            probe = uphill_max_angle(46.0, 7.0, aspect_deg=0.0)

        assert probe.steepest_deg == 42.0
        assert probe.unavailable is False

    def test_it_does_not_look_downhill(self) -> None:
        """A track along the TOP of a face is not marked by it.

        The converse of the test above, and what the arc buys over a
        plain radius search: ground below the skier is not ground they
        can be released onto.
        """
        probed: list[float] = []

        def _answer(latitude: float, longitude: float, window_m: Any) -> TerrainSlope:
            probed.append(latitude)
            return _slope(5.0)

        with patch(_SAMPLE, side_effect=_answer):
            # Facing north (downhill is north), so every probe must be
            # SOUTH of the track — a lower latitude.
            uphill_max_angle(46.0, 7.0, aspect_deg=0.0)

        assert probed, "no probe was taken"
        assert all(latitude < 46.0 for latitude in probed)

    def test_it_probes_within_the_stated_radii(self) -> None:
        """A marker is about ground the skier is committed to."""
        distances: list[float] = []

        def _answer(latitude: float, longitude: float, window_m: Any) -> TerrainSlope:
            distances.append(haversine_m(46.0, 7.0, latitude, longitude))
            return _slope(5.0)

        with patch(_SAMPLE, side_effect=_answer):
            uphill_max_angle(46.0, 7.0, aspect_deg=180.0)

        assert distances
        assert max(distances) == pytest.approx(max(PROBE_RADII_M), abs=1.0)

    def test_a_flat_sample_looks_four_ways(self) -> None:
        """No facing means no uphill, and a bench under a face is real."""
        probed: list[tuple[float, float]] = []

        def _answer(latitude: float, longitude: float, window_m: Any) -> TerrainSlope:
            probed.append((latitude, longitude))
            return _slope(5.0)

        with patch(_SAMPLE, side_effect=_answer):
            uphill_max_angle(46.0, 7.0, aspect_deg=None)

        assert len(probed) == 4
        # One probe each side of the origin on both axes.
        assert any(latitude > 46.0 for latitude, _ in probed)
        assert any(latitude < 46.0 for latitude, _ in probed)
        assert any(longitude > 7.0 for _, longitude in probed)
        assert any(longitude < 7.0 for _, longitude in probed)

    def test_ground_nothing_could_answer_for_is_not_gentle_ground(self) -> None:
        """None is "we did not see", and the caller must not read it as
        "nothing steep" — the rule terrain.py sets one layer down.
        """
        with patch(_SAMPLE, return_value=_slope(None)):
            probe = uphill_max_angle(46.0, 7.0, aspect_deg=180.0)

        assert probe.steepest_deg is None
        # Outside coverage is a fact about the GROUND, so the answer is
        # final and the record written over it is complete.
        assert probe.unavailable is False

    def test_an_unreachable_origin_is_reported_as_such(self) -> None:
        """OUR outage, not the survey's — and the distinction is the whole
        retry story: a record stored over one would file "we could not
        look" as "nothing was flagged" and never look again.
        """
        with patch(_SAMPLE, return_value=_unavailable()):
            probe = uphill_max_angle(46.0, 7.0, aspect_deg=180.0)

        assert probe.steepest_deg is None
        assert probe.unavailable is True

    def test_one_unanswerable_probe_does_not_void_the_others(self) -> None:
        answers = [_slope(None), _slope(41.0)] + [_slope(5.0)] * 10

        with patch(_SAMPLE, side_effect=answers):
            assert uphill_max_angle(46.0, 7.0, aspect_deg=180.0).steepest_deg == 41.0


class TestIsCrux:
    """What the two angles together mean."""

    def test_steep_ground_above_a_gentle_track_is_a_crux(self) -> None:
        assert is_crux(8.0, CRUX_THRESHOLD_DEG) is True

    def test_steep_ground_underfoot_is_a_crux_too(self) -> None:
        # The arc search ADDS a case; it does not replace the one the
        # angle already answers. A skier on a 38° slope is standing on
        # the thing that can release.
        assert is_crux(38.0, 5.0) is True

    def test_gentle_everywhere_is_not_a_crux(self) -> None:
        assert is_crux(12.0, 20.0) is False

    def test_the_threshold_is_inclusive(self) -> None:
        assert is_crux(CRUX_THRESHOLD_DEG, None) is True

    def test_two_unknowns_are_not_a_crux_and_not_a_clearance(self) -> None:
        # False here means "no marker", which the help topic and the
        # legend are explicit is not a claim the ground is gentle.
        assert is_crux(None, None) is False


class TestCruxPoints:
    """One passage, one marker."""

    def _points(self, count: int) -> list[list[float]]:
        """Return ``count`` boundary coordinates along a meridian."""
        return [[7.0, 46.0 + index / 10000] for index in range(count)]

    def test_a_run_of_flagged_segments_is_one_marker(self) -> None:
        """A traverse under a face is one passage, not twenty.

        At a 25 m stride a 500 m traverse is twenty flagged segments, and
        a ring on each would bury the track under its own markers.
        """
        segments: list[dict[str, Any]] = [{"crux": True} for _ in range(20)]

        markers = crux_points(self._points(21), segments)

        assert len(markers) == 1

    def test_two_runs_are_two_markers(self) -> None:
        segments: list[dict[str, Any]] = [
            {"crux": True},
            {"crux": True},
            {},
            {},
            {"crux": True},
        ]

        markers = crux_points(self._points(6), segments)

        assert len(markers) == 2
        # In track order, and each inside its own run.
        assert markers[0][1] < markers[1][1]

    def test_a_marker_sits_in_the_middle_of_its_run(self) -> None:
        points = self._points(6)
        segments: list[dict[str, Any]] = [{}, {"crux": True}, {"crux": True}, {}, {}]

        markers = crux_points(points, segments)

        # The run spans boundaries 1..3, so its middle is boundary 2.
        assert markers == [points[2]]

    def test_a_run_reaching_the_end_of_the_track_is_closed(self) -> None:
        segments: list[dict[str, Any]] = [{}, {"crux": True}, {"crux": True}]

        assert len(crux_points(self._points(4), segments)) == 1

    def test_nothing_flagged_is_no_markers(self) -> None:
        assert crux_points(self._points(4), [{}, {}, {}]) == []

    def test_a_record_whose_halves_do_not_pair_up_draws_nothing(self) -> None:
        # A marker against the wrong geometry is worse than no marker.
        assert crux_points(self._points(2), [{"crux": True}, {"crux": True}]) == []


class TestDestination:
    """The geometry the probes are placed with."""

    def test_it_is_the_inverse_of_haversine(self) -> None:
        latitude, longitude = destination(46.0, 7.0, 135.0, 80.0)

        assert haversine_m(46.0, 7.0, latitude, longitude) == pytest.approx(
            80.0, abs=0.1
        )

    def test_north_increases_latitude_and_east_increases_longitude(self) -> None:
        north, _ = destination(46.0, 7.0, 0.0, 100.0)
        _, east = destination(46.0, 7.0, 90.0, 100.0)

        assert north > 46.0
        assert east > 7.0
