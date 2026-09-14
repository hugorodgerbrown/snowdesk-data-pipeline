"""
tests/routes/test_bulletin_join.py — where a route meets today's problem.

Covers ``apps.routes.services.bulletin_join`` (SNOW-839), the pure half of
the join. Nothing here touches the database.

The claims worth testing are the ones a reader's safety rests on:

  - an aspect the problem does not name is NOT counted, which is the
    whole discrimination the feature exists to make;
  - an empty aspect list is CAAML for "every aspect" and must not be read
    as "no aspects";
  - a height outside the band is not counted, and a height INSIDE one
    bounded only by the treeline is neither counted nor silently dropped
    — it is reported as undecided, because a reader told nothing about a
    band will assume it did not apply;
  - a segment the terrain could not answer for is absent from every
    problem rather than being attributed to one or excused from all.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.routes.services.bulletin_join import (
    OCTANTS,
    SegmentFacts,
    elevation_matches,
    octant_for,
    overlaps,
)


def _problem(
    problem_type: str = "persistent_weak_layers",
    aspects: list[str] | None = None,
    elevation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a render-model problem with the given geography."""
    return {
        "problem_type": problem_type,
        "danger_rating_value": "considerable",
        "aspects": aspects if aspects is not None else [],
        "elevation": elevation,
    }


def _band(lower: int | None = None, upper: int | None = None) -> dict[str, Any]:
    """Return a parsed elevation band with numeric bounds."""
    return {
        "lower": lower,
        "upper": upper,
        "treeline": False,
        "treeline_side": None,
    }


class TestOctantFor:
    """Which compass sector a bearing is in."""

    @pytest.mark.parametrize(
        ("bearing", "expected"),
        [
            (0.0, "N"),
            (10.0, "N"),
            (350.0, "N"),
            (45.0, "NE"),
            (90.0, "E"),
            (180.0, "S"),
            (270.0, "W"),
            (315.0, "NW"),
        ],
    )
    def test_a_band_is_centred_on_its_own_name(
        self, bearing: float, expected: str
    ) -> None:
        # Due north is the MIDDLE of N, not its edge — which is what a
        # bulletin means by "north facing".
        assert octant_for(bearing) == expected

    def test_every_octant_is_reachable(self) -> None:
        found = {octant_for(bearing) for bearing in range(0, 360, 5)}
        assert found == set(OCTANTS)

    def test_no_bearing_is_no_octant(self) -> None:
        assert octant_for(None) is None


class TestElevationMatches:
    """Whether a height is inside a problem's band."""

    def test_no_stated_band_applies_everywhere(self) -> None:
        # An answer, not an absence: a problem with no elevation is a
        # problem at every elevation.
        assert elevation_matches(None, 2500.0) is True

    def test_a_height_inside_a_lower_bound_matches(self) -> None:
        assert elevation_matches(_band(lower=2200), 2500.0) is True

    def test_a_height_below_a_lower_bound_does_not(self) -> None:
        assert elevation_matches(_band(lower=2200), 1800.0) is False

    def test_a_height_above_an_upper_bound_does_not(self) -> None:
        assert elevation_matches(_band(upper=2200), 2500.0) is False

    def test_a_bound_is_inclusive(self) -> None:
        assert elevation_matches(_band(lower=2200), 2200.0) is True

    def test_a_treeline_band_cannot_be_decided(self) -> None:
        """We hold no treeline model, and None is not a no."""
        band = {
            "lower": None,
            "upper": None,
            "treeline": True,
            "treeline_side": "lower",
        }

        assert elevation_matches(band, 2500.0) is None

    def test_a_track_with_no_height_cannot_be_decided(self) -> None:
        assert elevation_matches(_band(lower=2200), None) is None


class TestOverlaps:
    """The join itself."""

    def test_an_aspect_the_problem_names_is_counted(self) -> None:
        segments = [SegmentFacts(aspect_deg=0.0, elevation_m=2500.0, length_m=100.0)]

        found = overlaps(segments, [_problem(aspects=["N"], elevation=_band(2200))])

        assert len(found) == 1
        assert found[0].length_m == 100.0
        assert found[0].aspects == {"N"}

    def test_an_aspect_the_problem_does_not_name_is_not(self) -> None:
        """The discrimination the whole feature exists to make.

        A south-facing stretch under a problem listed for the northern
        half is not in that problem's ground, and reporting it would be
        the false positive that teaches a reader to ignore the feature.
        """
        segments = [SegmentFacts(aspect_deg=180.0, elevation_m=2500.0, length_m=100.0)]

        assert overlaps(segments, [_problem(aspects=["N", "NE"])]) == []

    def test_an_empty_aspect_list_means_every_aspect(self) -> None:
        # CAAML's own convention, common on wet-snow problems. Reading it
        # as "no aspects" would silently drop a whole class of problem.
        segments = [SegmentFacts(aspect_deg=180.0, elevation_m=2500.0, length_m=100.0)]

        found = overlaps(segments, [_problem(aspects=[])])

        assert len(found) == 1

    def test_a_height_outside_the_band_is_not_counted(self) -> None:
        segments = [SegmentFacts(aspect_deg=0.0, elevation_m=1800.0, length_m=100.0)]

        assert (
            overlaps(segments, [_problem(aspects=["N"], elevation=_band(2200))]) == []
        )

    def test_only_the_matching_stretch_is_counted(self) -> None:
        """The figure is about this route, not about the problem."""
        segments = [
            SegmentFacts(aspect_deg=0.0, elevation_m=2500.0, length_m=100.0),
            SegmentFacts(aspect_deg=180.0, elevation_m=2500.0, length_m=400.0),
            SegmentFacts(aspect_deg=45.0, elevation_m=2500.0, length_m=200.0),
        ]

        found = overlaps(segments, [_problem(aspects=["N", "NE"])])

        assert found[0].length_m == 300.0
        assert found[0].aspects == {"N", "NE"}

    def test_it_reports_the_heights_the_route_crosses(self) -> None:
        segments = [
            SegmentFacts(aspect_deg=0.0, elevation_m=2200.0, length_m=100.0),
            SegmentFacts(aspect_deg=0.0, elevation_m=2800.0, length_m=100.0),
        ]

        found = overlaps(segments, [_problem(aspects=["N"])])

        assert found[0].lowest_m == 2200.0
        assert found[0].highest_m == 2800.0

    def test_a_treeline_band_is_reported_undecided_not_dropped(self) -> None:
        """A reader told nothing about a band assumes it did not apply."""
        band = {
            "lower": None,
            "upper": None,
            "treeline": True,
            "treeline_side": "lower",
        }
        segments = [SegmentFacts(aspect_deg=0.0, elevation_m=2500.0, length_m=100.0)]

        found = overlaps(segments, [_problem(aspects=["N"], elevation=band)])

        assert len(found) == 1
        assert found[0].elevation_undecided is True

    def test_a_segment_with_no_aspect_is_counted_against_nothing(self) -> None:
        """Ground nothing looked at is neither exposure nor safety.

        Attributing it to a problem would invent exposure; excusing it
        would invent safety. It is simply absent, and the surface that
        says so is the route's own "not surveyed" figure (SNOW-961).
        """
        segments = [SegmentFacts(aspect_deg=None, elevation_m=2500.0, length_m=100.0)]

        assert overlaps(segments, [_problem(aspects=[])]) == []

    def test_a_problem_the_route_never_enters_produces_no_entry(self) -> None:
        """The bulletin page lists every problem; this lists the crossed ones."""
        segments = [SegmentFacts(aspect_deg=0.0, elevation_m=2500.0, length_m=100.0)]

        found = overlaps(
            segments,
            [
                _problem(problem_type="wind_slab", aspects=["S"]),
                _problem(problem_type="persistent_weak_layers", aspects=["N"]),
            ],
        )

        assert [overlap.problem_type for overlap in found] == ["persistent_weak_layers"]
