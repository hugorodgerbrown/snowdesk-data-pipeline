"""
tests/routes/test_slope_segments.py — sampling the ground under a track.

Covers ``apps.routes.services.slope_segments``:

  - ``stride_distances``: the one rule of the walk, including the trailing
    stub being absorbed rather than appended, and a track shorter than one
    stride still holding exactly one segment;
  - ``stride_coordinates``: the walk placed on a synthetic meridian track
    with a hand-worked sample count;
  - ``build_slope_samples``: the record's shape, N + 1 coordinates bounding
    N segments, a mixed known/unknown track keeping the reason it was given,
    an all-unavailable run storing NOTHING rather than a record of
    nothing, and an outage ENDING the walk rather than being waited out;
  - ``_worker_sample_route_slopes``: the write, the one-column save, and a
    route deleted between the enqueue and the run being an expected race;
  - ``create_route``: the enqueue happens exactly once, and OUTSIDE the
    transaction;
  - ``save_trip_route``: the same two placement rules, plus the end-to-end
    path — a route saved off a trip ends up with a record of its own,
    because a trip's snapshot deliberately carries none to inherit.

``sample_slope`` is patched here rather than the transport: this module's
job is the walk and the record, and ``tests/locations/services/test_terrain.py``
is where the sampler's own answers are established. ``load_grid`` is
patched with the committed fixture definition, so the ``grid`` and
``window_m`` the record carries are the real ones.

The autouse ``_no_live_terrain_origin`` fixture in ``tests/conftest.py`` is
what keeps the unpatched tests here — and every other ``create_route``
test in the suite — off the real tile origin.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection

from apps.locations.services.terrain import TerrainSlope, TerrainUnknown
from apps.locations.services.terrain_grid import TerrainGrid, grid_from_payload
from apps.routes.models import Route
from apps.routes.services.routes import create_route
from apps.routes.services.slope_segments import (
    _UNAVAILABLE_RUN_LIMIT,
    SAMPLE_STRIDE_M,
    _worker_sample_route_slopes,
    build_slope_samples,
    stride_coordinates,
    stride_distances,
)
from tests.factories import RouteFactory, TripFactory, UserFactory

_GRID_FIXTURE = (
    Path(__file__).parent.parent / "locations" / "fixtures" / "terrain" / "grid.json"
)

# One 0.01° step along a meridian, in metres. The same constant
# tests/routes/test_services.py works its distances against, and it comes
# from apps.core.geo.haversine_m over (46.00, 7.0) → (46.01, 7.0).
LEG_M = 1111.9508023352598

# A straight meridian track: two stored points, 0.03° apart, no elevation.
# Its length is exactly three legs, which is what makes the sample count
# below arithmetic rather than a recorded observation.
MERIDIAN_TRACK: list[list[float | None]] = [[7.0, 46.0, None], [7.0, 46.03, None]]
MERIDIAN_LENGTH_M = 3 * LEG_M


@pytest.fixture(autouse=True)
def _silent_crux_probes() -> Any:
    """Answer every SNOW-911 uphill probe with "we did not see".

    ``apps.routes.services.cruxes`` imports ``sample_slope`` itself, so
    patching this module's copy does not reach it — an unpatched probe
    would walk out to the real tile origin from a unit test. Silenced
    rather than answered here because the probe geometry and what it
    concludes are ``tests/routes/test_cruxes.py``'s subject; with the
    probes saying nothing, a segment's OWN angle is the only thing that
    can flag it, which is what the assertions below rely on.
    """
    with patch(
        "apps.routes.services.cruxes.sample_slope",
        return_value=_unknown(TerrainUnknown.OUTSIDE_COVERAGE),
    ):
        yield


def _grid() -> TerrainGrid:
    """Return the committed terrain grid definition.

    Returns:
        The fixture grid, so the record under test carries the real
        ``grid`` identifier and the real default analysis window.

    """
    return grid_from_payload(json.loads(_GRID_FIXTURE.read_text()))


def _known(angle_deg: float, aspect_deg: float = 180.0) -> TerrainSlope:
    """Return a TerrainSlope carrying an angle.

    Args:
        angle_deg: The steepness the sampler should report.
        aspect_deg: The bearing it faces. Defaults to due south.

    Returns:
        A known slope sample.

    """
    return TerrainSlope(
        angle_deg=angle_deg,
        aspect_deg=aspect_deg,
        window_m=10.0,
        unknown=None,
        source=None,
    )


def _unknown(reason: TerrainUnknown) -> TerrainSlope:
    """Return a TerrainSlope carrying a reason instead of an angle.

    Args:
        reason: Why the sampler had no figure.

    Returns:
        An unknown slope sample.

    """
    return TerrainSlope(
        angle_deg=None,
        aspect_deg=None,
        window_m=10.0,
        unknown=reason,
        source=None,
    )


class TestStrideDistances:
    """The stride walk's one rule, with no track involved."""

    def test_a_whole_number_of_strides_lands_on_the_end(self) -> None:
        """100 m at a 25 m stride is four segments, ending exactly at 100."""
        assert stride_distances(100.0, 25.0) == [0.0, 25.0, 50.0, 75.0, 100.0]

    def test_a_short_remainder_is_absorbed_into_the_last_segment(self) -> None:
        """110 m gives four segments, the last 35 m long — not a 10 m stub."""
        assert stride_distances(110.0, 25.0) == [0.0, 25.0, 50.0, 75.0, 110.0]

    def test_a_long_remainder_becomes_its_own_segment(self) -> None:
        """120 m leaves 20 m, which is over half a stride and stands alone."""
        assert stride_distances(120.0, 25.0) == [0.0, 25.0, 50.0, 75.0, 100.0, 120.0]

    def test_a_track_shorter_than_one_stride_still_holds_one_segment(self) -> None:
        """10 m is under half a stride but is the whole track, so it stays."""
        assert stride_distances(10.0, 25.0) == [0.0, 10.0]

    def test_a_track_with_no_length_holds_no_segment(self) -> None:
        """A single boundary bounds nothing — the caller discards it."""
        assert stride_distances(0.0, 25.0) == [0.0]

    def test_every_segment_is_between_half_and_one_and_a_half_strides(self) -> None:
        """The property the absorption rule exists to guarantee."""
        for total in (26.0, 37.0, 49.9, 50.1, 999.0, 1234.5):
            boundaries = stride_distances(total, 25.0)
            lengths = [
                boundaries[index + 1] - boundaries[index]
                for index in range(len(boundaries) - 1)
            ]
            assert lengths
            assert min(lengths) >= 12.5
            assert max(lengths) <= 37.5


class TestStrideCoordinates:
    """The walk placed on a track."""

    def test_the_sample_count_is_hand_worked(self) -> None:
        """3 × LEG_M is 3335.85 m: 133 whole strides, the 10.85 m remainder absorbed."""
        assert MERIDIAN_LENGTH_M == pytest.approx(3335.8524070057794)
        coordinates = stride_coordinates(MERIDIAN_TRACK, SAMPLE_STRIDE_M)
        # 134 boundaries bound 133 segments: int(3335.85 / 25) == 133, and
        # the 10.85 m left over is under half a stride so it lengthens the
        # last segment rather than adding a 135th boundary.
        assert len(coordinates) == 134

    def test_the_walk_spans_the_whole_track(self) -> None:
        """The first coordinate is the start and the last is the end."""
        coordinates = stride_coordinates(MERIDIAN_TRACK, SAMPLE_STRIDE_M)
        assert coordinates[0] == pytest.approx((7.0, 46.0))
        assert coordinates[-1] == pytest.approx((7.0, 46.03))

    def test_the_coordinates_are_evenly_spaced_along_the_track(self) -> None:
        """25 m along a 3335.85 m meridian is 0.03° × 25 / 3335.85 of latitude."""
        coordinates = stride_coordinates(MERIDIAN_TRACK, SAMPLE_STRIDE_M)
        step = 0.03 * SAMPLE_STRIDE_M / MERIDIAN_LENGTH_M
        assert coordinates[1] == pytest.approx((7.0, 46.0 + step))
        assert coordinates[2] == pytest.approx((7.0, 46.0 + 2 * step))

    def test_a_sample_point_need_not_be_a_stored_point(self) -> None:
        """The walk interpolates: 134 samples off a two-point track."""
        coordinates = stride_coordinates(MERIDIAN_TRACK, SAMPLE_STRIDE_M)
        assert len(coordinates) > len(MERIDIAN_TRACK)

    def test_a_bend_is_followed_rather_than_cut(self) -> None:
        """A sample on the second leg of a right-angled track sits on it."""
        # East then north, one leg each. Half way along is the corner, so
        # a coordinate past the halfway mark must have moved in latitude
        # and stopped moving in longitude.
        track: list[list[float | None]] = [
            [7.0, 46.0, None],
            [7.01, 46.0, None],
            [7.01, 46.01, None],
        ]
        coordinates = stride_coordinates(track, 200.0)
        northbound = [pair for pair in coordinates if pair[1] > 46.0]
        assert northbound
        assert all(pair[0] == pytest.approx(7.01) for pair in northbound)

    def test_a_track_of_one_point_yields_nothing_to_walk(self) -> None:
        """Fewer than two stored points is not a track."""
        assert stride_coordinates([[7.0, 46.0, None]], SAMPLE_STRIDE_M) == []


@pytest.mark.django_db
class TestBuildSlopeSamples:
    """The record written to Route.slope_samples."""

    def test_n_plus_one_coordinates_bound_n_segments(self) -> None:
        """The shared-endpoint invariant the record's compactness rests on."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.25),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert len(record["points"]) == len(record["segments"]) + 1

    def test_the_record_carries_its_provenance(self) -> None:
        """The grid it was sampled from, the window, and the stride."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        grid = _grid()
        with (
            patch("apps.routes.services.slope_segments.load_grid", return_value=grid),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.25),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert record["grid"] == grid.grid
        assert record["window_m"] == grid.default_analysis_window_m
        assert record["stride_m"] == SAMPLE_STRIDE_M

    def test_the_record_carries_its_cruxes(self) -> None:
        """SNOW-911: grouped markers, not one per flagged segment.

        Every segment is 41°, which is over the crux threshold, so the
        whole track is one continuous passage — and a continuous passage
        is ONE marker however many strides it spans.
        """
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(41.0),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert all(segment["crux"] for segment in record["segments"])
        assert len(record["cruxes"]) == 1
        # The marker is a coordinate on the walk, not an invented point.
        assert record["cruxes"][0] in record["points"]

    def test_a_gentle_track_carries_an_empty_crux_list(self) -> None:
        """Empty, not absent: "nothing was flagged" is an answer, and the
        backfill command reads the KEY's presence as "this row is current".
        """
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(12.0),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert record["cruxes"] == []
        assert not any("crux" in segment for segment in record["segments"])

    def test_the_record_carries_its_summary(self) -> None:
        """SNOW-961: written here, where the exact segment lengths exist.

        Every segment is the same known angle, so the summary's own
        arithmetic is checkable by hand: all of the walk is surveyed, all
        of it is steep at 34.25°, and it all falls in one band.
        """
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.25),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        summary = record["summary"]
        assert summary["surveyed_m"] == summary["sampled_m"] > 0
        assert summary["steep_m"] == summary["sampled_m"]
        # The STORED angle, not the sampled one: the summary reads the
        # record's own rounded figures, so the popup can never quote a
        # steepest angle the coloured segment beside it disagrees with.
        assert summary["steepest_deg"] == 34.2
        assert list(summary["bands"]) == ["slope-30"]
        # The walk's own length, and every metre of it accounted for in
        # exactly one band — the two figures a reader compares.
        assert summary["bands"]["slope-30"] == summary["sampled_m"]

    def test_a_known_segment_carries_an_angle_and_an_aspect(self) -> None:
        """Rounded to a tenth of a degree — see the module's precision note."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.248, 105.34),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert record["segments"][0] == {"angle_deg": 34.2, "aspect_deg": 105.3}

    def test_level_ground_is_a_known_angle_facing_nowhere(self) -> None:
        """0.0 degrees with a null aspect is an ANSWER, not an unknown."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        flat = TerrainSlope(
            angle_deg=0.0, aspect_deg=None, window_m=10.0, unknown=None, source=None
        )
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope", return_value=flat
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert record["segments"][0] == {"angle_deg": 0.0, "aspect_deg": None}
        assert "unknown" not in record["segments"][0]

    def test_an_unknown_segment_carries_its_reason_and_no_angle(self) -> None:
        """The reason is preserved verbatim, never reduced to a null angle."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.OUTSIDE_COVERAGE),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert record["segments"][0] == {"unknown": "outside_coverage"}

    def test_a_mixed_track_keeps_each_segment_s_own_answer(self) -> None:
        """A track crossing the coverage edge is part coloured, part unknown."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        answers = [
            _known(41.0),
            _unknown(TerrainUnknown.NO_DATA),
            _known(28.5),
        ]
        calls = {"count": 0}

        def _next_answer(latitude: float, longitude: float) -> TerrainSlope:
            """Answer in a fixed cycle so each segment gets a known result."""
            answer = answers[calls["count"] % len(answers)]
            calls["count"] += 1
            return answer

        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                side_effect=_next_answer,
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        # 41° is at or above the crux threshold, so the segment carries
        # the flag as well — see apps/routes/services/cruxes.py.
        assert record["segments"][0] == {
            "angle_deg": 41.0,
            "aspect_deg": 180.0,
            "crux": True,
        }
        assert record["segments"][1] == {"unknown": "no_data"}
        assert record["segments"][2] == {"angle_deg": 28.5, "aspect_deg": 180.0}

    def test_an_all_unavailable_run_stores_nothing(self) -> None:
        """Our outage is not a fact about the ground, and must stay retryable."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.UNAVAILABLE),
            ),
        ):
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None

    def test_an_outage_stops_the_walk_rather_than_finishing_it(self) -> None:
        """The CALL COUNT is the assertion: an outage is not waited out.

        A failure is deliberately never memoised, so without the
        short-circuit every one of this track's 133 midpoints would
        re-attempt the same dead tiles at the transport's full timeout to
        reach the same answer.
        """
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.UNAVAILABLE),
            ) as sampler,
        ):
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None

        assert sampler.call_count == _UNAVAILABLE_RUN_LIMIT

    def test_a_short_route_can_be_all_unavailable_below_the_limit(self) -> None:
        """The all-unavailable check still earns its place.

        This track holds ONE segment, so the consecutive-run limit is never
        reached and nothing but that check keeps the empty record out of
        the row.
        """
        route = RouteFactory.create(points=[[7.0, 46.0, None], [7.0, 46.0002, None]])
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.UNAVAILABLE),
            ) as sampler,
        ):
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None

        assert sampler.call_count == 1

    def test_an_isolated_unavailable_does_not_abandon_a_good_run(self) -> None:
        """CONSECUTIVE, not cumulative — one dead tile is a blip.

        Aborting here would throw away 132 good samples, and leave the row
        null for a route the origin was answering perfectly well about.
        """
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        calls = {"count": 0}

        def _one_bad_tile(latitude: float, longitude: float) -> TerrainSlope:
            """Fail on the sixth sample only, answer every other one."""
            calls["count"] += 1
            if calls["count"] == 6:
                return _unknown(TerrainUnknown.UNAVAILABLE)
            return _known(30.0)

        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                side_effect=_one_bad_tile,
            ) as sampler,
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert sampler.call_count == len(record["segments"])
        assert record["segments"][5] == {"unknown": "unavailable"}
        assert record["segments"][6] == {"angle_deg": 30.0, "aspect_deg": 180.0}

    def test_an_outage_beginning_mid_route_is_caught_too(self) -> None:
        """The origin goes down part-way, so the run that matters starts there."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        good_samples = 10
        calls = {"count": 0}

        def _origin_dies(latitude: float, longitude: float) -> TerrainSlope:
            """Answer ten samples, then fail for the rest of the track."""
            calls["count"] += 1
            if calls["count"] <= good_samples:
                return _known(30.0)
            return _unknown(TerrainUnknown.UNAVAILABLE)

        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                side_effect=_origin_dies,
            ) as sampler,
        ):
            # Nothing is stored: a part-sampled track would be a record
            # whose second half says the ground is unknown, which is a
            # claim about our origin rather than about the ground.
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None

        assert sampler.call_count == good_samples + _UNAVAILABLE_RUN_LIMIT

    def test_an_all_outside_coverage_run_is_stored(self) -> None:
        """Permanently uncovered ground IS a fact, and asking again won't help."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.OUTSIDE_COVERAGE),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert all(
            segment == {"unknown": "outside_coverage"} for segment in record["segments"]
        )

    def test_an_unreachable_grid_samples_nothing_at_all(self) -> None:
        """No definition means no sample can succeed, so no request is made."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch("apps.routes.services.slope_segments.load_grid", return_value=None),
            patch("apps.routes.services.slope_segments.sample_slope") as sampler,
        ):
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None
        sampler.assert_not_called()

    def test_a_track_with_no_length_is_not_sampled(self) -> None:
        """Two identical points describe a place, not a route across ground."""
        route = RouteFactory.create(
            points=[[7.0, 46.0, 1500.0], [7.0, 46.0, 1500.0]],
        )
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch("apps.routes.services.slope_segments.sample_slope") as sampler,
        ):
            assert build_slope_samples(route.points, f"route pk={route.pk}") is None
        sampler.assert_not_called()

    def test_the_terrain_is_sampled_and_never_the_track(self) -> None:
        """The whole point: the track's own elevation reaches no sampler.

        The track climbs 500 m over 3.3 km — a gentle 8.6° along its own
        length — while the ground under it is sampled at 41°. The record
        must say 41.
        """
        route = RouteFactory.create(
            points=[[7.0, 46.0, 1500.0], [7.0, 46.03, 2000.0]],
        )
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(41.0),
            ),
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert {segment["angle_deg"] for segment in record["segments"]} == {41.0}

    def test_each_segment_is_sampled_at_its_own_midpoint(self) -> None:
        """One call per segment, and never at a boundary coordinate."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.0),
            ) as sampler,
        ):
            record = build_slope_samples(route.points, f"route pk={route.pk}")

        assert record is not None
        assert sampler.call_count == len(record["segments"])
        # (latitude, longitude) at the call — the house argument order.
        first_latitude = sampler.call_args_list[0].args[0]
        boundary_latitudes = {point[1] for point in record["points"]}
        assert first_latitude not in boundary_latitudes


@pytest.mark.django_db
class TestSampleRouteSlopesWorker:
    """The background worker that stores the record."""

    def test_it_writes_the_record_to_the_row(self) -> None:
        """The happy path, read back from the database."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.0),
            ),
        ):
            _worker_sample_route_slopes.call(route.pk)

        route.refresh_from_db()
        assert route.slope_samples is not None
        assert route.slope_samples["segments"][0]["angle_deg"] == 34.0

    def test_it_writes_only_the_one_column(self) -> None:
        """A rename made while the sampling ran must survive it."""
        route = RouteFactory.create(points=MERIDIAN_TRACK, name="Before")
        Route.objects.filter(pk=route.pk).update(name="Renamed mid-flight")
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.0),
            ),
        ):
            # ``route`` still holds the stale name in memory; a full save
            # would write it back over the rename.
            _worker_sample_route_slopes.call(route.pk)

        route.refresh_from_db()
        assert route.name == "Renamed mid-flight"
        assert route.slope_samples is not None

    def test_a_run_that_learned_nothing_leaves_the_field_null(self) -> None:
        """Null keeps meaning NEVER SAMPLED, so a backfill can still find it."""
        route = RouteFactory.create(points=MERIDIAN_TRACK)
        with (
            patch(
                "apps.routes.services.slope_segments.load_grid", return_value=_grid()
            ),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_unknown(TerrainUnknown.UNAVAILABLE),
            ),
        ):
            _worker_sample_route_slopes.call(route.pk)

        route.refresh_from_db()
        assert route.slope_samples is None

    def test_a_deleted_route_is_an_expected_race(self) -> None:
        """Deleted between the enqueue and the run: log and return, never raise."""
        with patch("apps.routes.services.slope_segments.sample_slope") as sampler:
            _worker_sample_route_slopes.call(999_999)
        sampler.assert_not_called()


@pytest.mark.django_db
class TestCreateRouteEnqueuesSampling:
    """Where create_route's enqueue sits, which is the subtle part."""

    def _gpx(self) -> bytes:
        """Return a minimal two-point GPX track.

        Returns:
            GPX bytes ``parse_gpx`` accepts.

        """
        return (
            b'<?xml version="1.0"?>'
            b'<gpx version="1.1" creator="t"><trk><trkseg>'
            b'<trkpt lat="46.0" lon="7.0"><ele>1500</ele></trkpt>'
            b'<trkpt lat="46.03" lon="7.0"><ele>1600</ele></trkpt>'
            b"</trkseg></trk></gpx>"
        )

    def test_it_enqueues_exactly_once(self) -> None:
        """One upload, one sampling run."""
        user = UserFactory.create()
        with patch(
            "apps.routes.services.routes.enqueue_route_slope_sampling"
        ) as enqueue:
            route = create_route(user, self._gpx(), "track.gpx")
        enqueue.assert_called_once_with(route)

    def test_it_enqueues_outside_the_transaction(self) -> None:
        """The placement that only production would punish getting wrong.

        Under ``ImmediateBackend`` — dev, test AND staging — ``.enqueue()``
        runs the worker inline, so an enqueue inside ``create_route``'s
        ``atomic()`` block would hold the ``select_for_update`` lock on the
        user row through a walk of the tile origin. Every backend looks the
        same on a green run, so the placement is asserted directly.

        ``connection.savepoint_ids`` is the probe: pytest-django wraps each
        test in its own atomic block, so ``in_atomic_block`` is true either
        way — but ``create_route``'s nested ``atomic()`` opens a SAVEPOINT
        inside it, and that savepoint is released when the block exits.
        """
        user = UserFactory.create()
        depths: list[int] = []

        def _record(route: Route) -> None:
            """Record how deep the transaction stack is at the enqueue."""
            depths.append(len(connection.savepoint_ids))

        with patch(
            "apps.routes.services.routes.enqueue_route_slope_sampling",
            side_effect=_record,
        ):
            create_route(user, self._gpx(), "track.gpx")

        assert depths == [0]

    def test_the_savepoint_probe_detects_an_enqueue_inside_the_block(self) -> None:
        """The control for the test above — a probe that never fails is no test.

        Asserts the same measurement taken from INSIDE an ``atomic()``
        block reads differently, so ``depths == [0]`` above is evidence and
        not a constant.
        """
        from django.db import transaction

        with transaction.atomic():
            assert len(connection.savepoint_ids) == 1

    def test_a_failed_upload_enqueues_nothing(self) -> None:
        """Nothing was stored, so there is nothing to sample."""
        from apps.routes.services.gpx import GPXParseError

        user = UserFactory.create()
        with patch(
            "apps.routes.services.routes.enqueue_route_slope_sampling"
        ) as enqueue:
            with pytest.raises(GPXParseError):
                create_route(user, b"not a gpx file", "junk.gpx")
        enqueue.assert_not_called()


@pytest.mark.django_db
class TestClaimedCopiesCarryTheSamples:
    """A shared route's copy inherits the record rather than re-sampling."""

    def test_the_copy_carries_the_sharer_s_record(self) -> None:
        """Same geometry, same ground — and no second walk of the origin."""
        from apps.routes.services.shares import claim_route_share, create_route_share

        record: dict[str, Any] = {
            "window_m": 10.0,
            "stride_m": SAMPLE_STRIDE_M,
            "grid": "snowdesk-terrain-5m-3035",
            "points": [[7.0, 46.0], [7.0, 46.01]],
            "segments": [{"angle_deg": 34.0, "aspect_deg": 180.0}],
        }
        owner = UserFactory.create()
        route = RouteFactory.create(user=owner, slope_samples=record)
        share = create_route_share(owner, route.uuid)

        claimer = UserFactory.create()
        copy = claim_route_share(claimer, share.token)

        assert copy.slope_samples == record


@pytest.mark.django_db
class TestTripSavedRoutesAreSampled:
    """A trip-saved route is RE-SAMPLED, which is the opposite of a claim.

    ``claim_route_share`` has the source's record one field access away and
    copies it. ``save_trip_route`` has no route to read — a trip's geometry
    is a snapshot and ``Trip.route`` is provenance only — so the copy is
    written null and sampled (SNOW-910). Without the enqueue a trip-saved
    route drew flat for good while the identical line was coloured
    everywhere else.
    """

    def test_it_enqueues_exactly_once(self) -> None:
        """One save, one sampling run, against the COPY's row."""
        from apps.trips.services.routes import save_trip_route

        trip = TripFactory.create(points=MERIDIAN_TRACK, point_count=2)
        viewer = UserFactory.create()

        with patch(
            "apps.trips.services.routes.enqueue_route_slope_sampling"
        ) as enqueue:
            route = save_trip_route(viewer, trip)

        enqueue.assert_called_once_with(route)

    def test_it_enqueues_outside_the_transaction(self) -> None:
        """The same placement ``create_route`` is held to, same probe.

        ``write_route_copy`` opens a nested ``atomic()`` that takes
        ``select_for_update`` on the user row for the cap re-check. Under
        ``ImmediateBackend`` — dev, test AND staging — an enqueue inside it
        would hold that lock through a walk of the tile origin, and every
        backend would still look green.
        """
        from apps.trips.services.routes import save_trip_route

        trip = TripFactory.create(points=MERIDIAN_TRACK, point_count=2)
        viewer = UserFactory.create()
        depths: list[int] = []

        def _record(route: Route) -> None:
            """Record how deep the transaction stack is at the enqueue."""
            depths.append(len(connection.savepoint_ids))

        with patch(
            "apps.trips.services.routes.enqueue_route_slope_sampling",
            side_effect=_record,
        ):
            save_trip_route(viewer, trip)

        assert depths == [0]

    def test_the_saved_route_ends_up_with_a_record(self) -> None:
        """End to end, through the real enqueue and the real worker.

        The enqueue is deliberately NOT patched here: under the test
        backend it runs the worker inline, so this asserts the whole path
        a trip-saved route now takes rather than the call at the top of it.
        """
        from apps.trips.services.routes import save_trip_route

        trip = TripFactory.create(points=MERIDIAN_TRACK, point_count=2)
        viewer = UserFactory.create()
        grid = _grid()

        with (
            patch("apps.routes.services.slope_segments.load_grid", return_value=grid),
            patch(
                "apps.routes.services.slope_segments.sample_slope",
                return_value=_known(34.25),
            ),
        ):
            route = save_trip_route(viewer, trip)

        route.refresh_from_db()
        assert route.slope_samples is not None
        assert route.slope_samples["grid"] == grid.grid
        assert route.slope_samples["stride_m"] == SAMPLE_STRIDE_M
        assert (
            len(route.slope_samples["points"])
            == len(route.slope_samples["segments"]) + 1
        )
        assert all(
            segment["angle_deg"] == 34.2 for segment in route.slope_samples["segments"]
        )

    def test_a_sampled_trip_hands_its_record_over(self) -> None:
        """The snapshot's own record is inherited, not re-walked (SNOW-962).

        This reverses SNOW-910, and the reason is on the ticket: once
        ``Trip`` carries the record — which it must, for the trip page to
        colour its own line — this path is in ``claim_route_share``'s
        position, with the answer one field access away. Re-walking would
        ask the tile origin a question already answered.
        """
        from apps.trips.services.routes import save_trip_route

        record: dict[str, Any] = {
            "window_m": 10.0,
            "stride_m": SAMPLE_STRIDE_M,
            "grid": "snowdesk-terrain-5m-3035",
            "points": [[7.0, 46.0], [7.0, 46.01]],
            "segments": [{"angle_deg": 51.0, "aspect_deg": 180.0}],
        }
        organiser = UserFactory.create()
        source = RouteFactory.create(
            user=organiser, points=MERIDIAN_TRACK, slope_samples=record
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=source,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=record,
        )

        with patch(
            "apps.routes.services.slope_segments.sample_slope",
            side_effect=AssertionError("the origin must not be asked again"),
        ):
            route = save_trip_route(UserFactory.create(), trip)

        route.refresh_from_db()
        assert route.slope_samples is not None
        # The snapshot's own angle, carried across untouched.
        assert route.slope_samples["segments"][0]["angle_deg"] == 51.0

    def test_an_unreachable_origin_leaves_the_copy_unsampled(self) -> None:
        """Null is "never sampled", and a failed walk must leave it true.

        The route is still saved — a dead tile origin is no reason to
        refuse somebody their own copy of a track — and the backfill
        command's candidate set selects on exactly this null.
        """
        from apps.trips.services.routes import save_trip_route

        trip = TripFactory.create(points=MERIDIAN_TRACK, point_count=2)

        with patch("apps.routes.services.slope_segments.load_grid", return_value=None):
            route = save_trip_route(UserFactory.create(), trip)

        route.refresh_from_db()
        assert route.slope_samples is None
