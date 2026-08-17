"""
tests/routes/test_services.py — Tests for apps.routes.services.

apps.routes.services.gpx.parse_gpx:
  single-track GPX — name, points in GeoJSON axis order, derived figures;
  multiple <trkseg> concatenated in document order;
  an <rte>-only file is accepted;
  a waypoint-only file is rejected (a <wpt> is not part of any path);
  a multi-track file imports the first and reports how many it saw;
  a file with no <ele> leaves ascent_m null, not zero;
  malformed XML is rejected;
  an XXE payload is rejected by defusedxml rather than by our validation;
  a non-<gpx> root, a too-short track and out-of-range coordinates are
    rejected;
  simplification honours MAX_POINTS, keeps both endpoints, returns a
    subsequence of the source, and carries each surviving point's
    elevation with it;
  distance and ascent are measured on the full-resolution track, so
    simplification does not shorten or flatten them;
  _stride_sample — the bound is guaranteed even when Douglas-Peucker
    cannot reach it.

apps.routes.services.routes:
  create_route stores the parsed result and truncates over-long names;
  create_route enforces settings.ROUTES_MAX_PER_USER and does not parse
    when already at the cap;
  create_route re-checks the cap inside the transaction (SNOW-465 pattern);
  delete_route is owner-scoped.

The distance figures are checked against a hand-worked fixture: every leg
in ``single_track.gpx`` is 0.01° of latitude along one meridian, which is
R·0.01·π/180 = 1111.95 m for R = 6 371 008.8 m.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from shapely.geometry import LineString

from apps.routes.models import Route
from apps.routes.services.gpx import (
    MAX_POINTS,
    GPXParseError,
    _stride_sample,
    _total_distance_m,
    parse_gpx,
)
from apps.routes.services.routes import RouteLimitReached, create_route, delete_route
from tests.factories import RouteFactory, UserFactory

FIXTURES = Path(__file__).parent / "fixtures"

# One 0.01° step along a meridian, in metres — see the module docstring.
LEG_M = 1111.9508023352598


def _fixture(name: str) -> bytes:
    """Return the raw bytes of a GPX fixture."""
    return (FIXTURES / name).read_bytes()


def _synthetic_gpx(point_count: int) -> bytes:
    """Build a GPX track of ``point_count`` wiggling, climbing points.

    The lateral sine wiggle matters: a dead-straight line collapses to two
    points at any tolerance, which would let the simplification tests pass
    without exercising the vertex walk that re-attaches elevations.

    Args:
        point_count: How many <trkpt> elements to emit.

    Returns:
        Raw GPX bytes.

    """
    points = "".join(
        f'<trkpt lat="{46.0 + index * 0.0001:.6f}" '
        f'lon="{7.0 + math.sin(index / 10) * 0.001:.6f}">'
        f"<ele>{1000.0 + index * 0.1:.1f}</ele></trkpt>"
        for index in range(point_count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>Long</name><trkseg>{points}</trkseg></trk></gpx>"
    ).encode()


# ---------------------------------------------------------------------------
# parse_gpx — accepted shapes
# ---------------------------------------------------------------------------


class TestParseGpxSingleTrack:
    """A plain one-track, one-segment file."""

    def test_name_comes_from_the_track_not_the_metadata(self) -> None:
        """<trk><name> wins over <metadata><name>."""
        assert parse_gpx(_fixture("single_track.gpx")).name == "Col de Balme"

    def test_points_are_lon_lat_ele_in_geojson_axis_order(self) -> None:
        """Stored points put longitude first, per RFC 7946."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.points[0] == [7.0, 46.0, 1000.0]
        assert parsed.points[-1] == [7.0, 46.03, 1250.0]

    def test_point_count_matches_the_stored_points(self) -> None:
        """point_count describes what is stored, not what was read."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.point_count == len(parsed.points) == 4

    def test_bounds_are_a_geojson_bbox(self) -> None:
        """bounds is [min_lon, min_lat, max_lon, max_lat]."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.bounds == [7.0, 46.0, 7.0, 46.03]

    def test_distance_is_the_summed_great_circle_length(self) -> None:
        """Three 0.01° meridian legs — hand-checked against LEG_M."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.distance_m == pytest.approx(3 * LEG_M)

    def test_ascent_counts_only_the_climbs(self) -> None:
        """1000→1100→1050→1250 is +100 and +200; the descent is not subtracted."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.ascent_m == pytest.approx(300.0)

    def test_track_count_is_one(self) -> None:
        """A single-track file reports one track, so nothing was dropped."""
        assert parse_gpx(_fixture("single_track.gpx")).track_count == 1

    def test_metadata_name_is_the_fallback_when_the_track_is_unnamed(self) -> None:
        """A track with no <name> of its own falls back to <metadata><name>."""
        raw = _fixture("single_track.gpx").replace(b"<name>Col de Balme</name>", b"", 1)
        assert parse_gpx(raw).name == "File level name"


class TestParseGpxMultipleSegments:
    """Multiple <trkseg> are one polyline, concatenated in document order."""

    def test_segments_are_concatenated(self) -> None:
        """Two two-point segments become one four-point track."""
        parsed = parse_gpx(_fixture("multi_segment.gpx"))
        assert parsed.point_count == 4

    def test_segments_are_joined_in_document_order(self) -> None:
        """The second segment's points follow the first's, unreordered."""
        parsed = parse_gpx(_fixture("multi_segment.gpx"))
        latitudes = [point[1] for point in parsed.points]
        assert latitudes == [46.0, 46.01, 46.02, 46.03]

    def test_the_segment_join_counts_towards_distance(self) -> None:
        """The gap between segments is a leg, not a discontinuity."""
        parsed = parse_gpx(_fixture("multi_segment.gpx"))
        assert parsed.distance_m == pytest.approx(3 * LEG_M)


class TestParseGpxRouteElement:
    """An <rte>-only file is a route too."""

    def test_rte_only_file_is_accepted(self) -> None:
        """<rtept> points are read when there is no <trk>."""
        parsed = parse_gpx(_fixture("route_only.gpx"))
        assert parsed.point_count == 3
        assert parsed.name == "Planned line"

    def test_rte_derived_figures_are_computed(self) -> None:
        """A route gets the same derived figures a track does."""
        parsed = parse_gpx(_fixture("route_only.gpx"))
        assert parsed.distance_m == pytest.approx(2 * LEG_M)
        assert parsed.ascent_m == pytest.approx(200.0)

    def test_trk_wins_when_a_file_holds_both(self) -> None:
        """A file with both imports the track, not the route."""
        raw = _fixture("single_track.gpx").replace(
            b"</gpx>",
            b'<rte><name>Ignored</name><rtept lat="1.0" lon="1.0"></rtept>'
            b'<rtept lat="2.0" lon="2.0"></rtept></rte></gpx>',
        )
        assert parse_gpx(raw).name == "Col de Balme"


class TestParseGpxMultipleTracks:
    """A multi-track file imports the first and says how many it saw."""

    def test_first_track_is_imported(self) -> None:
        """Points come from the first <trk> only."""
        parsed = parse_gpx(_fixture("multi_track.gpx"))
        assert parsed.name == "First track"
        assert parsed.bounds == [7.0, 46.0, 7.0, 46.01]

    def test_track_count_reports_the_dropped_tracks(self) -> None:
        """track_count lets the caller say what it took."""
        assert parse_gpx(_fixture("multi_track.gpx")).track_count == 2


class TestParseGpxMissingElevation:
    """A file with no <ele> yields a null ascent, not zero."""

    def test_ascent_is_none(self) -> None:
        """A file that never recorded elevation says nothing, not "flat"."""
        assert parse_gpx(_fixture("no_elevation.gpx")).ascent_m is None

    def test_points_carry_null_elevations(self) -> None:
        """Each stored point still has three slots, with ele null."""
        parsed = parse_gpx(_fixture("no_elevation.gpx"))
        assert all(point[2] is None for point in parsed.points)

    def test_distance_is_still_measured(self) -> None:
        """Missing elevation does not stop the horizontal maths."""
        parsed = parse_gpx(_fixture("no_elevation.gpx"))
        assert parsed.distance_m == pytest.approx(2 * LEG_M)

    def test_a_partial_elevation_series_skips_the_gap(self) -> None:
        """A gap in the series is skipped, not counted as a climb from zero."""
        raw = _fixture("single_track.gpx").replace(b"<ele>1100.0</ele>", b"", 1)
        parsed = parse_gpx(raw)
        # 1000→(gap)→1050→1250: only the +200 leg has both ends.
        assert parsed.ascent_m == pytest.approx(200.0)

    def test_an_unparseable_elevation_is_a_gap_not_a_failure(self) -> None:
        """A malformed <ele> drops that point's elevation, keeping its position."""
        raw = _fixture("single_track.gpx").replace(
            b"<ele>1100.0</ele>", b"<ele>not-a-number</ele>", 1
        )
        parsed = parse_gpx(raw)
        assert parsed.point_count == 4
        assert parsed.points[1][2] is None


# ---------------------------------------------------------------------------
# parse_gpx — rejected shapes
# ---------------------------------------------------------------------------


class TestParseGpxRejections:
    """Everything parse_gpx refuses, and why."""

    def test_waypoint_only_file_is_rejected(self) -> None:
        """A <wpt> is a standalone marker, not part of a path."""
        with pytest.raises(GPXParseError, match="No <trk> or <rte>"):
            parse_gpx(_fixture("waypoints_only.gpx"))

    def test_malformed_xml_is_rejected(self) -> None:
        """A mismatched tag is a parse failure, not a 500."""
        with pytest.raises(GPXParseError, match="well-formed"):
            parse_gpx(_fixture("malformed.gpx"))

    def test_xxe_payload_is_rejected_by_defusedxml(self) -> None:
        """The external-entity declaration never reaches expansion.

        This is the whole reason parsing goes through defusedxml rather
        than the stdlib parser: the rejection happens in the parser, not in
        validation we would have to remember to write.
        """
        with pytest.raises(GPXParseError, match="Rejected XML document"):
            parse_gpx(_fixture("xxe.gpx"))

    def test_xxe_payload_does_not_leak_the_targeted_file(self) -> None:
        """Nothing from /etc/passwd reaches the parsed output — it never parses."""
        with pytest.raises(GPXParseError) as excinfo:
            parse_gpx(_fixture("xxe.gpx"))
        assert "root:" not in str(excinfo.value)

    def test_non_gpx_root_is_rejected(self) -> None:
        """Well-formed XML that is not GPX is still not importable."""
        with pytest.raises(GPXParseError, match="not <gpx>"):
            parse_gpx(b"<?xml version='1.0'?><kml><Placemark/></kml>")

    def test_empty_body_is_rejected(self) -> None:
        """An empty upload is a parse failure, not an empty route."""
        with pytest.raises(GPXParseError):
            parse_gpx(b"")

    def test_single_point_track_is_rejected(self) -> None:
        """A polyline needs two ends."""
        raw = (
            '<?xml version="1.0"?>'
            '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            '<trkpt lat="46.0" lon="7.0"/>'
            "</trkseg></trk></gpx>"
        ).encode()
        with pytest.raises(GPXParseError, match="at least 2 points"):
            parse_gpx(raw)

    def test_point_missing_lat_lon_is_rejected(self) -> None:
        """A <trkpt> with no coordinates is a broken file."""
        raw = (
            '<?xml version="1.0"?>'
            '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            '<trkpt lat="46.0"/><trkpt lat="46.1" lon="7.0"/>'
            "</trkseg></trk></gpx>"
        ).encode()
        with pytest.raises(GPXParseError, match="missing its lat/lon"):
            parse_gpx(raw)

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [("95.0", "7.0"), ("46.0", "200.0"), ("nan", "7.0"), ("46.0", "inf")],
    )
    def test_out_of_range_or_non_finite_coordinates_are_rejected(
        self, lat: str, lon: str
    ) -> None:
        """SNOW-464: NaN/±Inf and out-of-WGS-84 values never reach a write."""
        raw = (
            '<?xml version="1.0"?>'
            '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            f'<trkpt lat="{lat}" lon="{lon}"/><trkpt lat="46.1" lon="7.0"/>'
            "</trkseg></trk></gpx>"
        ).encode()
        with pytest.raises(GPXParseError, match="out of range"):
            parse_gpx(raw)

    def test_unparseable_coordinate_is_rejected(self) -> None:
        """A non-numeric lat is a parse failure, not a crash."""
        raw = (
            '<?xml version="1.0"?>'
            '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            '<trkpt lat="north" lon="7.0"/><trkpt lat="46.1" lon="7.0"/>'
            "</trkseg></trk></gpx>"
        ).encode()
        with pytest.raises(GPXParseError, match="Unparseable"):
            parse_gpx(raw)


# ---------------------------------------------------------------------------
# parse_gpx — simplification
# ---------------------------------------------------------------------------


class TestParseGpxSimplification:
    """A long track is thinned to a bounded point count."""

    def test_a_short_track_is_left_alone(self) -> None:
        """Below the bound, nothing is dropped."""
        parsed = parse_gpx(_fixture("single_track.gpx"))
        assert parsed.point_count == parsed.source_point_count == 4

    def test_a_long_track_is_reduced_to_the_bound(self) -> None:
        """A 5,000-point track does not become a 5,000-point JSON column."""
        parsed = parse_gpx(_synthetic_gpx(5_000))
        assert parsed.source_point_count == 5_000
        assert parsed.point_count <= MAX_POINTS

    def test_endpoints_survive_simplification(self) -> None:
        """The stored line still starts and finishes where the track did."""
        parsed = parse_gpx(_synthetic_gpx(5_000))
        assert parsed.points[0][1] == pytest.approx(46.0)
        assert parsed.points[-1][1] == pytest.approx(46.0 + 4_999 * 0.0001)

    def test_stored_points_are_a_subsequence_of_the_source(self) -> None:
        """Simplification drops vertices; it never invents or moves one."""
        parsed = parse_gpx(_synthetic_gpx(5_000))
        source = {
            (round(46.0 + index * 0.0001, 6), round(1000.0 + index * 0.1, 1))
            for index in range(5_000)
        }
        for _lon, lat, ele in parsed.points:
            assert (lat, ele) in source

    def test_each_surviving_point_keeps_its_own_elevation(self) -> None:
        """The vertex walk re-attaches the right ele, not a shifted one.

        The synthetic track's elevation is a fixed function of its latitude
        (both are linear in the point index), so a mis-aligned walk would
        show up immediately as a broken pairing.
        """
        parsed = parse_gpx(_synthetic_gpx(5_000))
        for _lon, lat, ele in parsed.points:
            assert lat is not None
            index = round((lat - 46.0) / 0.0001)
            assert ele == pytest.approx(1000.0 + index * 0.1)

    def test_distance_is_measured_before_simplification(self) -> None:
        """A coarser stored geometry must not shorten the reported distance.

        Dropping vertices can only shorten a polyline (triangle
        inequality), so re-measuring the *stored* points with the same
        haversine must come out under the reported figure — which is what
        proves the reported figure was taken before the thinning.
        """
        parsed = parse_gpx(_synthetic_gpx(5_000))
        stored_length = _total_distance_m(
            [
                (float(lon), float(lat), ele)  # type: ignore[arg-type]
                for lon, lat, ele in parsed.points
            ]
        )
        assert stored_length < parsed.distance_m

    def test_ascent_is_measured_before_simplification(self) -> None:
        """The synthetic track climbs monotonically: 4,999 steps of 0.1 m."""
        parsed = parse_gpx(_synthetic_gpx(5_000))
        assert parsed.ascent_m == pytest.approx(499.9)

    def test_stride_sampling_guarantees_the_bound(self) -> None:
        """The backstop behind Douglas-Peucker cannot fail to meet the limit."""
        points: list[tuple[float, float, float | None]] = [
            (7.0, 46.0 + index * 0.0001, None) for index in range(1_000)
        ]
        sampled = _stride_sample(points, 100)
        assert len(sampled) <= 100
        assert sampled[0] == points[0]
        assert sampled[-1] == points[-1]

    def test_simplification_falls_back_when_douglas_peucker_cannot_converge(
        self,
    ) -> None:
        """If tolerance doubling runs out, the bound is still honoured."""
        # Pin the iteration budget to zero doublings so the for/else fallback
        # is the only path that can produce a result.
        with patch("apps.routes.services.gpx._MAX_SIMPLIFY_ITERATIONS", 0):
            parsed = parse_gpx(_synthetic_gpx(5_000))
        assert parsed.point_count <= MAX_POINTS

    def test_a_vertex_absent_from_the_source_degrades_rather_than_raising(
        self,
    ) -> None:
        """The guard on the subsequence assumption returns a route, not a 500.

        Douglas-Peucker returns input vertices verbatim, so this cannot
        happen today — but an upload is user-facing, and an IndexError from
        a surprise in GEOS would be a 500 rather than a coarser line.
        """
        shifted = LineString([(7.5, 46.5), (7.6, 46.6)])
        with patch("shapely.geometry.LineString.simplify", return_value=shifted):
            parsed = parse_gpx(_synthetic_gpx(5_000))

        assert parsed.point_count <= MAX_POINTS
        assert parsed.points[0] == [7.0, 46.0, 1000.0]


# ---------------------------------------------------------------------------
# create_route / delete_route
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateRoute:
    """create_route parses, stores, and enforces the per-user cap."""

    def test_stores_the_parsed_track(self) -> None:
        """The derived figures land on the row."""
        user = UserFactory.create()
        route = create_route(
            user, _fixture("single_track.gpx"), source_filename="ride.gpx"
        )

        assert route.user == user
        assert route.name == "Col de Balme"
        assert route.source_filename == "ride.gpx"
        assert route.point_count == 4
        assert route.points[0] == [7.0, 46.0, 1000.0]
        assert route.bounds == [7.0, 46.0, 7.0, 46.03]
        assert route.distance_m == pytest.approx(3 * LEG_M)
        assert route.ascent_m == pytest.approx(300.0)

    def test_stores_null_ascent_for_an_elevation_free_file(self) -> None:
        """The null survives the write."""
        user = UserFactory.create()
        route = create_route(user, _fixture("no_elevation.gpx"))
        route.refresh_from_db()
        assert route.ascent_m is None

    def test_over_long_gpx_name_is_truncated_not_a_db_error(self) -> None:
        """A GPX may name itself anything; the column may not."""
        user = UserFactory.create()
        raw = _fixture("single_track.gpx").replace(
            b"<name>Col de Balme</name>",
            b"<name>" + b"x" * 500 + b"</name>",
            1,
        )
        route = create_route(user, raw)
        assert len(route.name) == 100

    def test_over_long_filename_is_truncated(self) -> None:
        """Same guard on the filename label."""
        user = UserFactory.create()
        route = create_route(
            user, _fixture("single_track.gpx"), source_filename="y" * 500 + ".gpx"
        )
        assert len(route.source_filename) == 255

    def test_parse_failure_propagates_and_writes_nothing(self) -> None:
        """A bad file leaves no row behind."""
        user = UserFactory.create()
        with pytest.raises(GPXParseError):
            create_route(user, _fixture("malformed.gpx"))
        assert not Route.objects.for_user(user).exists()

    def test_cap_is_enforced(self, settings: Any) -> None:
        """At the cap, create_route refuses."""
        settings.ROUTES_MAX_PER_USER = 1
        user = UserFactory.create()
        RouteFactory.create(user=user)

        with pytest.raises(RouteLimitReached):
            create_route(user, _fixture("single_track.gpx"))

    def test_at_the_cap_the_file_is_never_parsed(self, settings: Any) -> None:
        """The up-front check exists so a doomed upload costs no parsing."""
        settings.ROUTES_MAX_PER_USER = 1
        user = UserFactory.create()
        RouteFactory.create(user=user)

        with (
            patch("apps.routes.services.routes.parse_gpx") as mock_parse,
            pytest.raises(RouteLimitReached),
        ):
            create_route(user, _fixture("single_track.gpx"))
        mock_parse.assert_not_called()

    def test_cap_is_rechecked_inside_the_transaction(self, settings: Any) -> None:
        """SNOW-465 pattern: the locked re-check is what holds under a race.

        Simulated by letting the up-front check pass and having a competing
        row appear during the parse, which is exactly the window the
        select_for_update re-check exists to close.
        """
        settings.ROUTES_MAX_PER_USER = 1
        user = UserFactory.create()
        real_parse = parse_gpx

        def _parse_then_race(raw: bytes) -> Any:
            """Stand in for a concurrent request winning the slot."""
            RouteFactory.create(user=user)
            return real_parse(raw)

        with (
            patch(
                "apps.routes.services.routes.parse_gpx", side_effect=_parse_then_race
            ),
            pytest.raises(RouteLimitReached),
        ):
            create_route(user, _fixture("single_track.gpx"))

        assert Route.objects.for_user(user).count() == 1

    def test_the_cap_is_per_user(self, settings: Any) -> None:
        """One user at the cap does not block another."""
        settings.ROUTES_MAX_PER_USER = 1
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        RouteFactory.create(user=user_a)

        route = create_route(user_b, _fixture("single_track.gpx"))
        assert route.user == user_b


@pytest.mark.django_db
class TestDeleteRoute:
    """delete_route is owner-scoped."""

    def test_owner_can_delete(self) -> None:
        """The owner's route is removed."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        delete_route(user, route.uuid)

        assert not Route.objects.filter(pk=route.pk).exists()

    def test_non_owner_cannot_delete(self) -> None:
        """Another user's uuid raises DoesNotExist and leaves the row alone."""
        owner = UserFactory.create()
        other = UserFactory.create()
        route = RouteFactory.create(user=owner)

        with pytest.raises(Route.DoesNotExist):
            delete_route(other, route.uuid)

        assert Route.objects.filter(pk=route.pk).exists()
