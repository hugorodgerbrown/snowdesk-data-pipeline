"""
tests/routes/test_legs.py — cutting a route into legs (SNOW-990).

The corpus does the heavy lifting here. Three properties are asserted
against all four canonical tracks: the legs cover the route exactly, the
detection is stable across the parameter plateau the constants were chosen
from, and — the one this module exists for — the leg count survives the
same route being recorded at a fifth of the density.

That last test is the regression guard on the window's unit. A window
respecified in POINTS would pass every other test here and fail that one,
which is precisely how the bug hid in the first place.
"""

from __future__ import annotations

import pytest

from apps.routes.services.canonical import canonical_documents
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.legs import (
    MIN_LEG_ASCENT_M,
    SMOOTHING_WINDOW_M,
    Leg,
    detect_legs,
)

# Leg counts on the committed corpus at the default constants. Spelled out
# so a change to either constant, or to the algorithm, has to be argued
# rather than absorbed.
EXPECTED_LEGS = {
    "chamonix-col-de-balme.gpx": 2,
    "hidden-valley.gpx": 4,
    "mont-fort-backside.gpx": 4,
    "mont-fort-col-de-la-chaux.gpx": 7,
}


def _track(filename: str) -> list[list[float | None]]:
    """Return one canonical track's parsed points.

    Args:
        filename: The fixture's filename.

    Returns:
        ``[lon, lat, ele]`` coordinates.

    """
    return parse_gpx(dict(canonical_documents())[filename]).points


def _length(points: list[list[float | None]]) -> float:
    """Return the track's own along-track length, for the coverage check.

    Args:
        points: Parsed coordinates.

    Returns:
        Metres.

    """
    from apps.routes.services.legs import _cumulative_distances

    return _cumulative_distances(points)[-1]


class TestTheLegsCoverTheRoute:
    """Nothing is lost between the legs."""

    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_distances_sum_to_the_track(self, filename: str) -> None:
        """The merge conserves distance where dropping a run would not.

        This is the assertion the prototype could not have made: it
        DISCARDED sub-threshold runs, and passed only because one route's
        survivors happened to be contiguous.
        """
        points = _track(filename)

        legs = detect_legs(points)

        assert sum(leg.distance_m for leg in legs) == pytest.approx(
            _length(points), abs=0.5
        )

    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_legs_are_contiguous_and_share_boundaries(self, filename: str) -> None:
        """Leg n ends where leg n+1 starts, first at 0 and last at the end."""
        points = _track(filename)

        legs = detect_legs(points)

        assert legs[0].start == 0
        assert legs[-1].end == len(points) - 1
        for previous, following in zip(legs, legs[1:], strict=False):
            assert previous.end == following.start

    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_indexes_run_from_one(self, filename: str) -> None:
        """A leg names its own position, so a caller need not enumerate."""
        legs = detect_legs(_track(filename))

        assert [leg.index for leg in legs] == list(range(1, len(legs) + 1))


class TestTheCorpusLegCounts:
    """What the detector finds on each committed track."""

    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_finds_the_expected_legs(self, filename: str) -> None:
        """Pinned, so a constant cannot move quietly."""
        assert len(detect_legs(_track(filename))) == EXPECTED_LEGS[filename]

    def test_the_ratchet_alternates(self) -> None:
        """Col de la Chaux is ski/skin seven times, starting downhill.

        Lift-assisted, which is why it opens with a descent — the shape
        every mockup before the real data assumed could not happen.
        """
        legs = detect_legs(_track("mont-fort-col-de-la-chaux.gpx"))

        assert [leg.climbing for leg in legs] == [
            False,
            True,
            False,
            True,
            False,
            True,
            False,
        ]


class TestTheParameterPlateau:
    """The constants sit in the middle of a flat region, not on an edge."""

    @pytest.mark.parametrize("window_m", [75.0, 100.0, 150.0, 200.0, 300.0])
    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_the_window_plateau_is_flat(self, filename: str, window_m: float) -> None:
        """Every window from 75 m to 300 m gives the same answer."""
        legs = detect_legs(_track(filename), window_m=window_m)

        assert len(legs) == EXPECTED_LEGS[filename]

    @pytest.mark.parametrize("threshold_m", [15.0, 20.0, 30.0, 50.0])
    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_the_threshold_plateau_is_flat(
        self, filename: str, threshold_m: float
    ) -> None:
        """Every threshold from 15 m upward gives the same answer."""
        legs = detect_legs(_track(filename), threshold_m=threshold_m)

        assert len(legs) == EXPECTED_LEGS[filename]

    def test_the_defaults_are_inside_both_plateaus(self) -> None:
        """A guard on the constants themselves, not on their effect."""
        assert 75.0 < SMOOTHING_WINDOW_M < 300.0
        assert 15.0 <= MIN_LEG_ASCENT_M < 50.0


class TestDensityIndependence:
    """The reason the window is in metres.

    Thinning a track is the same route recorded by a device that samples
    less often. A metres window should not care; a points window does.
    """

    @pytest.mark.parametrize("step", [2, 3, 4, 6])
    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_the_leg_count_survives_thinning(self, filename: str, step: int) -> None:
        """Down to a sixth of the points, the same route has the same legs.

        A window specified in points fails this: at 1/6 it turns Col de la
        Chaux's seven legs into eleven, because ten points then span six
        times the ground they did at full resolution.
        """
        thinned = _track(filename)[::step]

        legs = detect_legs(thinned)

        assert len(legs) == EXPECTED_LEGS[filename]

    @pytest.mark.parametrize("filename", sorted(EXPECTED_LEGS))
    def test_thinning_preserves_each_legs_direction(self, filename: str) -> None:
        """Not just how many legs, but which way each one goes."""
        full = [leg.climbing for leg in detect_legs(_track(filename))]

        thinned = [leg.climbing for leg in detect_legs(_track(filename)[::4])]

        assert thinned == full


class TestWhatALegKnows:
    """The measured figures, and the one that is deliberately absent."""

    def test_reports_the_figures_phase_one_established(self) -> None:
        """Elevations, vertical, distance, bearing and track angle.

        TOLERANCES ARE LOOSE ON PURPOSE where the figure depends on where
        a boundary falls. A leg's first and last elevations, its net and
        its length are all read at the join between two legs, and the join
        moves by metres when the smoothing window changes — this same leg
        measured −152 m over 995 m under the prototype's ten-point window
        and −150 m over 949 m under the 100 m one. Pinning those to the
        metre would make the test a guard on the constant rather than on
        the detector. Direction and compass bearing are exact, because
        those are facts about the ground rather than about the join.
        """
        legs = detect_legs(_track("mont-fort-col-de-la-chaux.gpx"))

        first = legs[0]
        assert isinstance(first, Leg)
        assert not first.climbing
        assert first.compass == "S"
        assert first.elevation_start == pytest.approx(2914, abs=5)
        assert first.elevation_end == pytest.approx(2763, abs=5)
        assert first.net_m == pytest.approx(-151, abs=5)
        assert first.distance_m == pytest.approx(970, abs=50)
        assert first.track_angle_deg == pytest.approx(8.9, abs=0.5)

    def test_the_track_angle_is_the_track_not_the_ground(self) -> None:
        """Every leg here is gentle along its own length.

        Col de la Chaux crosses ground far steeper than any of these
        figures, which is the whole reason ``Route.slope_samples`` exists.
        A reader who mistook this for terrain would be reading a skin
        track's zigzag as the face it climbs.
        """
        legs = detect_legs(_track("mont-fort-col-de-la-chaux.gpx"))

        assert max(leg.track_angle_deg for leg in legs) < 20.0

    def test_ascent_and_descent_are_not_netted(self) -> None:
        """A leg that undulates reports both, like ``Route`` itself does."""
        legs = detect_legs(_track("hidden-valley.gpx"))

        for leg in legs:
            assert leg.ascent_m >= 0
            assert leg.descent_m >= 0
            assert leg.ascent_m - leg.descent_m == pytest.approx(leg.net_m, abs=0.5)


class TestTracksWithNothingToSay:
    """Inputs a leg cannot be read off."""

    def test_a_track_with_no_elevation_has_no_legs(self) -> None:
        """A leg is a change in height; without heights there is no claim."""
        points: list[list[float | None]] = [
            [7.4, 46.1, None],
            [7.41, 46.11, None],
            [7.42, 46.12, None],
        ]

        assert detect_legs(points) == []

    def test_a_partially_elevated_track_has_no_legs(self) -> None:
        """One null is enough — a bridged gap would invent terrain."""
        points: list[list[float | None]] = [
            [7.4, 46.1, 2000.0],
            [7.41, 46.11, None],
            [7.42, 46.12, 2100.0],
        ]

        assert detect_legs(points) == []

    @pytest.mark.parametrize("points", [[], [[7.4, 46.1, 2000.0]]])
    def test_a_track_too_short_has_no_legs(
        self, points: list[list[float | None]]
    ) -> None:
        """One point is not a polyline and has no length to divide."""
        assert detect_legs(points) == []

    def test_a_flat_track_is_one_leg(self) -> None:
        """No transition anywhere, so the whole thing is a single leg."""
        points: list[list[float | None]] = [
            [7.4 + index * 0.001, 46.1, 2000.0] for index in range(50)
        ]

        legs = detect_legs(points)

        assert len(legs) == 1
        assert legs[0].net_m == 0.0
