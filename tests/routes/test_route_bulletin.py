"""
tests/routes/test_route_bulletin.py — this track, against that day.

Covers ``apps.routes.services.route_bulletin`` (SNOW-839): the half that
resolves regions and bulletins and hands the result to the pure join,
which ``tests/routes/test_bulletin_join.py`` establishes on its own.

What is asserted here is the wiring a unit test cannot see:

  - the aspect comes from the TERRAIN record and the height from the
    TRACK, which is SNOW-910's rule inverted and is the pair most likely
    to be "fixed" into agreement by a later reader;
  - a track crossing two regions reports under both bulletins, longest
    stretch first, with neither picked as the winner;
  - a region with no bulletin for the day is still reported, because a
    reader whose route leaves the forecast should be told so;
  - an unsampled track produces nothing at all — the join needs an
    aspect, and inventing one would invent exposure.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.routes.services.route_bulletin import _segment_facts, readings_for_track
from tests.factories import BulletinFactory, MicroRegionFactory

# A square region, and a second one beside it. Small and synthetic: the
# join does not care about real geography, and a fixture boundary makes
# the region a segment lands in arithmetic rather than a lookup.
_WEST = [[[7.0, 46.0], [7.1, 46.0], [7.1, 46.1], [7.0, 46.1], [7.0, 46.0]]]
_EAST = [[[7.1, 46.0], [7.2, 46.0], [7.2, 46.1], [7.1, 46.1], [7.1, 46.0]]]

DAY = datetime.date(2026, 3, 1)


def _boundary(rings: list[list[list[float]]]) -> dict[str, Any]:
    """Return a GeoJSON Polygon geometry for a region boundary."""
    return {"type": "Polygon", "coordinates": rings}


def _render_model(aspects: list[str], lower: int | None = None) -> dict[str, Any]:
    """Return a render model carrying one problem with the given geography."""
    return {
        "version": 1,
        "traits": [
            {
                "problems": [
                    {
                        "problem_type": "persistent_weak_layers",
                        "danger_rating_value": "considerable",
                        "aspects": aspects,
                        "elevation": (
                            {
                                "lower": lower,
                                "upper": None,
                                "treeline": False,
                                "treeline_side": None,
                            }
                            if lower is not None
                            else None
                        ),
                    }
                ]
            }
        ],
    }


def _track(
    coordinates: list[tuple[float, float]], elevation_m: float
) -> list[list[float | None]]:
    """Return a stored track at one height throughout."""
    return [[lon, lat, elevation_m] for lon, lat in coordinates]


def _record(
    coordinates: list[tuple[float, float]], aspects: list[float | None]
) -> dict[str, Any]:
    """Return a terrain record over ``coordinates`` with the given aspects."""
    return {
        "window_m": 10.0,
        "stride_m": 25.0,
        "grid": "snowdesk-terrain-5m-3035",
        "points": [[lon, lat] for lon, lat in coordinates],
        "segments": [
            {"angle_deg": 34.0, "aspect_deg": aspect}
            if aspect is not None
            else {"unknown": "outside_coverage"}
            for aspect in aspects
        ],
        "cruxes": [],
    }


@pytest.mark.django_db
class TestReadingsForTrack:
    """What each region's bulletin says about one track."""

    def _region_with_bulletin(
        self,
        region_id: str,
        rings: list[list[list[float]]],
        aspects: list[str],
        lower: int | None = None,
    ) -> Any:
        """Create a region whose bulletin carries one problem."""
        region = MicroRegionFactory.create(
            region_id=region_id, boundary=_boundary(rings)
        )
        bulletin = BulletinFactory.create(
            valid_from=datetime.datetime(2026, 3, 1, 6, tzinfo=datetime.UTC),
            valid_to=datetime.datetime(2026, 3, 1, 23, tzinfo=datetime.UTC),
            render_model=_render_model(aspects, lower),
            render_model_version=1,
        )
        bulletin.regions.add(region)
        return region

    def test_a_matching_aspect_and_height_is_reported(self) -> None:
        self._region_with_bulletin("CH-T01", _WEST, ["N", "NE"], lower=2200)
        coordinates = [(7.02, 46.02), (7.03, 46.03)]

        readings = readings_for_track(
            _track(coordinates, 2500.0), _record(coordinates, [0.0]), DAY
        )

        assert len(readings) == 1
        assert len(readings[0].problem_overlaps) == 1
        assert readings[0].problem_overlaps[0].aspects == {"N"}

    def test_the_height_comes_from_the_track_not_the_terrain(self) -> None:
        """SNOW-910's rule inverted, and the one most at risk of a "fix".

        The terrain record carries no height at all; the track does. A
        problem bounded at 2200 m must be matched from the GPX's own
        elevation, so a track at 1800 m is outside it however steep the
        ground beneath happens to be.
        """
        self._region_with_bulletin("CH-T02", _WEST, ["N"], lower=2200)
        coordinates = [(7.02, 46.02), (7.03, 46.03)]

        low = readings_for_track(
            _track(coordinates, 1800.0), _record(coordinates, [0.0]), DAY
        )
        high = readings_for_track(
            _track(coordinates, 2500.0), _record(coordinates, [0.0]), DAY
        )

        assert low[0].problem_overlaps == []
        assert len(high[0].problem_overlaps) == 1

    def test_the_height_is_interpolated_along_a_simplified_leg(self) -> None:
        """A stored track is SIMPLIFIED, so one leg can climb hundreds of
        metres. Snapping each sample to the nearer end's height would put
        a step halfway up a leg the route actually climbs steadily —
        which miscounts the length inside a bulletin's band by however
        much of the leg sits the wrong side of the boundary.

        Here the leg runs 2000 m to 3000 m and the problem starts at
        2500 m. Only the upper half of the leg is inside it, and the
        sample nearest the top must be matched while the one nearest the
        bottom must not.
        """
        self._region_with_bulletin("CH-T11", _WEST, ["N"], lower=2500)
        # Two stored points, far apart, 1000 m of climb between them.
        track: list[list[float | None]] = [
            [7.02, 46.01, 2000.0],
            [7.02, 46.09, 3000.0],
        ]
        # Four sampled segments spread along that one leg.
        boundaries = [
            (7.02, 46.01),
            (7.02, 46.03),
            (7.02, 46.05),
            (7.02, 46.07),
            (7.02, 46.09),
        ]
        record = _record(boundaries, [0.0, 0.0, 0.0, 0.0])

        readings = readings_for_track(track, record, DAY)

        assert len(readings) == 1
        overlap = readings[0].problem_overlaps[0]
        assert overlap.lowest_m is not None
        # Interpolated, the four samples sit at roughly 2125, 2375, 2625
        # and 2875 m, so the lowest one INSIDE the band is about 2625.
        # Snapped to the nearer vertex they would sit at 2000 or 3000
        # only, and the lowest matched height would be 3000 — so this
        # bound is what separates the two implementations, rather than
        # merely being true under both.
        assert 2500 <= overlap.lowest_m < 2900
        walked = sum(segment.length_m for segment in _segment_facts(track, record))
        assert overlap.length_m < walked

    def test_a_track_crossing_two_regions_reports_under_both(self) -> None:
        """On a border that is two providers, and neither is the winner."""
        self._region_with_bulletin("CH-T03", _WEST, ["N"])
        self._region_with_bulletin("CH-T04", _EAST, ["N"])
        # Three boundaries: two segments, one either side of 7.1.
        coordinates = [(7.05, 46.05), (7.09, 46.05), (7.15, 46.05)]

        readings = readings_for_track(
            _track(coordinates, 2500.0), _record(coordinates, [0.0, 0.0]), DAY
        )

        assert {reading.region.region_id for reading in readings} == {
            "CH-T03",
            "CH-T04",
        }

    def test_readings_are_longest_stretch_first(self) -> None:
        """The region a reader is mostly IN is the one to read first."""
        self._region_with_bulletin("CH-T05", _WEST, ["N"])
        self._region_with_bulletin("CH-T06", _EAST, ["N"])
        # Two segments whose middles are in the west, one whose middle is
        # in the east — and the two together are the longer stretch.
        coordinates = [(7.01, 46.05), (7.05, 46.05), (7.09, 46.05), (7.15, 46.05)]

        readings = readings_for_track(
            _track(coordinates, 2500.0), _record(coordinates, [0.0, 0.0, 0.0]), DAY
        )

        assert readings[0].region.region_id == "CH-T05"
        assert readings[0].length_m > readings[1].length_m

    def test_a_region_with_no_bulletin_is_still_reported(self) -> None:
        """Leaving the forecast is a fact the reader needs."""
        MicroRegionFactory.create(region_id="CH-T07", boundary=_boundary(_WEST))
        coordinates = [(7.02, 46.02), (7.03, 46.03)]

        readings = readings_for_track(
            _track(coordinates, 2500.0), _record(coordinates, [0.0]), DAY
        )

        assert len(readings) == 1
        assert readings[0].bulletin is None
        assert readings[0].problem_overlaps == []

    def test_an_unsampled_track_produces_nothing(self) -> None:
        """The join needs an aspect; inventing one would invent exposure."""
        self._region_with_bulletin("CH-T08", _WEST, ["N"])
        coordinates = [(7.02, 46.02), (7.03, 46.03)]

        assert readings_for_track(_track(coordinates, 2500.0), None, DAY) == []

    def test_a_malformed_record_produces_nothing(self) -> None:
        """Segments against the wrong ground is worse than no answer."""
        self._region_with_bulletin("CH-T09", _WEST, ["N"])
        coordinates = [(7.02, 46.02), (7.03, 46.03)]
        record = _record(coordinates, [0.0])
        record["points"] = [[7.02, 46.02]]

        assert readings_for_track(_track(coordinates, 2500.0), record, DAY) == []

    def test_a_segment_outside_every_region_is_left_out(self) -> None:
        """A bulletin is only about the ground its own region covers."""
        self._region_with_bulletin("CH-T10", _WEST, ["N"])
        # Both boundaries far outside the fixture regions.
        coordinates = [(9.0, 48.0), (9.01, 48.01)]

        assert (
            readings_for_track(
                _track(coordinates, 2500.0), _record(coordinates, [0.0]), DAY
            )
            == []
        )
