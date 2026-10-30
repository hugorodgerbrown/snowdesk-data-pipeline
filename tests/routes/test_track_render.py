"""
tests/routes/test_track_render.py — the stored track render (SNOW-1309).

apps/routes/services/track_render.py: the builder answers exactly what the
routes feed and the trip page built per request before it; the one writer
stores the record and its render together; every creation path stamps the
no-terrain render; and the readers read the stored record, building one
in memory only when it is missing or stale, and never writing it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client

from apps.routes.models import Route
from apps.routes.services.canonical import CANONICAL_DIR
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.routes import create_drawn_route, create_route
from apps.routes.services.shares import write_route_copy
from apps.routes.services.track_render import (
    cleared_terrain,
    current_render,
    stamped_fields,
    store_terrain,
)
from apps.trips.models import Trip
from apps.trips.services.trips import create_trip
from apps.trips.views import _trip_map_payload
from tests.factories import RouteFactory, TripFactory, UserFactory
from track.leg_wire import wire_legs
from track.render import (
    TRACK_RENDER_VERSION,
    build_track_render,
    is_current,
    leg_lines,
    render_fingerprint,
)
from track.slope_summary import summarise_record
from track.slope_wire import compact_slope
from track.terrain_detail import terrain_detail
from track.terrain_heights import climb_figures, terrain_points

pytestmark = pytest.mark.django_db

_RECORDS = Path(__file__).parent / "fixtures" / "slope_records"
_STEMS = sorted(path.stem for path in _RECORDS.glob("*.json"))
_BUILD = "apps.routes.services.track_render.build_track_render"


def _canonical(stem: str) -> tuple[Any, dict[str, Any]]:
    """Return a canonical track parsed, and its recorded slope record."""
    parsed = parse_gpx((CANONICAL_DIR / f"{stem}.gpx").read_bytes())
    return parsed, json.loads((_RECORDS / f"{stem}.json").read_text())


def _stored(row: Route | Trip) -> dict[str, Any]:
    """Return a row's stored render, which every write path sets."""
    assert row.track_render is not None
    return dict(row.track_render)


def _per_request(
    points: list[list[float | None]],
    samples: dict[str, Any] | None,
    ascent_m: float | None,
    descent_m: float | None,
) -> dict[str, Any]:
    """What ``_route_feature`` and the trip page computed per request, pre-1309."""
    geometry = terrain_points(points, samples)
    slope = compact_slope(samples, coordinates=geometry)
    legs = wire_legs(geometry, samples if slope is not None else None)
    terrain = summarise_record(samples)
    climb = climb_figures(points, samples, ascent_m, descent_m)
    return {
        "geometry": geometry,
        "ascent_m": climb.ascent_m,
        "descent_m": climb.descent_m,
        **({"slope": slope} if slope is not None else {}),
        **({"legs": legs} if legs else {}),
        **({"terrain": terrain} if terrain is not None else {}),
        "rows": terrain_detail(samples, points) or [],
    }


class TestBuilderParity:
    """The record is exactly what the surfaces built per request before."""

    @pytest.mark.parametrize("stem", _STEMS)
    @pytest.mark.parametrize("sampled", [True, False])
    def test_a_canonical_track(self, stem: str, sampled: bool) -> None:
        parsed, record = _canonical(stem)
        samples = record if sampled else None
        built = build_track_render(
            parsed.points,
            samples,
            ascent_m=parsed.ascent_m,
            descent_m=parsed.descent_m,
        )
        assert built == {
            "v": TRACK_RENDER_VERSION,
            "fp": render_fingerprint(
                parsed.points,
                samples,
                ascent_m=parsed.ascent_m,
                descent_m=parsed.descent_m,
            ),
            **_per_request(parsed.points, samples, parsed.ascent_m, parsed.descent_m),
        }

    def test_an_unsampled_track_keeps_its_stored_figures(self) -> None:
        """No model heights: the parser's full-resolution figures stand."""
        parsed, _ = _canonical(_STEMS[0])
        built = build_track_render(parsed.points, None, ascent_m=123.0, descent_m=None)
        assert (built["ascent_m"], built["descent_m"]) == (123.0, None)
        assert "slope" not in built and "terrain" not in built
        assert built["rows"] == []


class TestTheWriter:
    """``store_terrain``: the record and its render, always together."""

    def test_storing_a_record_stores_its_render(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        route = RouteFactory.create(points=parsed.points)
        before = route.updated_at
        store_terrain(route, record)
        route.refresh_from_db()
        assert route.slope_samples == record
        assert route.track_render == build_track_render(
            parsed.points, record, ascent_m=route.ascent_m, descent_m=route.descent_m
        )
        assert route.updated_at > before

    def test_clearing_writes_the_no_terrain_render(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        route = RouteFactory.create(points=parsed.points, slope_samples=record)
        store_terrain(route, None)
        route.refresh_from_db()
        assert route.slope_samples is None
        assert "slope" not in _stored(route)
        assert _stored(route)["rows"] == []

    def test_a_trip_is_written_the_same_way(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        trip = TripFactory.create(points=parsed.points)
        store_terrain(trip, record)
        trip.refresh_from_db()
        assert _stored(trip)["slope"] == compact_slope(
            record, coordinates=_stored(trip)["geometry"]
        )

    def test_storing_the_same_record_twice_writes_once(
        self, django_assert_num_queries: Any
    ) -> None:
        """SNOW-1316: the second call is a no-op — no UPDATE at all."""
        parsed, record = _canonical(_STEMS[0])
        route = RouteFactory.create(points=parsed.points)
        store_terrain(route, record)
        route.refresh_from_db()
        stamp = route.updated_at
        with django_assert_num_queries(0):
            store_terrain(route, record)
        route.refresh_from_db()
        assert route.updated_at == stamp

    def test_a_sync_clears_both_fields(self) -> None:
        assert cleared_terrain() == {"slope_samples": None, "track_render": None}


class TestCreation:
    """Every creation path stores the no-terrain render with the row."""

    def test_an_upload(self) -> None:
        raw = (CANONICAL_DIR / f"{_STEMS[0]}.gpx").read_bytes()
        with patch("apps.routes.services.routes.enqueue_route_slope_sampling"):
            route = create_route(UserFactory.create(), raw)
        route.refresh_from_db()
        assert is_current(route.track_render)
        assert route.track_render == build_track_render(
            route.points, None, ascent_m=route.ascent_m, descent_m=route.descent_m
        )

    def test_a_drawn_route(self) -> None:
        points: list[list[float | None]] = [[7.4, 46.1, None], [7.41, 46.11, None]]
        with patch("apps.routes.services.routes.enqueue_route_slope_sampling"):
            route = create_drawn_route(UserFactory.create(), points)
        route.refresh_from_db()
        assert _stored(route)["geometry"] == terrain_points(points, None)

    def test_a_copy_builds_its_own_never_the_sources(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        fields = {
            "name": "Copy",
            "points": parsed.points,
            "distance_m": parsed.distance_m,
            "ascent_m": parsed.ascent_m,
            "descent_m": parsed.descent_m,
            "point_count": parsed.point_count,
            "bounds": parsed.bounds,
            "slope_samples": record,
        }
        route = write_route_copy(UserFactory.create(), fields)
        assert route.track_render == stamped_fields(fields)["track_render"]
        assert "slope" in _stored(route)

    def test_a_trip_snapshots_the_routes_render(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user, points=parsed.points, slope_samples=record
        )
        with patch("apps.trips.services.trips.enqueue_trip_slope_sampling"):
            trip = create_trip(
                user,
                route_uuid=route.uuid,
                date=route.started_at.date(),  # type: ignore[union-attr]
                start_time=route.started_at.time(),  # type: ignore[union-attr]
            )
        trip = Trip.objects.get(pk=trip.pk)
        assert trip.track_render == route.track_render


class TestReaders:
    """Readers read the stored record; a missing one is built, never written."""

    def test_a_current_record_is_read(self) -> None:
        route = RouteFactory.create()
        with patch(_BUILD, side_effect=AssertionError("built")):
            assert current_render(route) == route.track_render

    def test_a_missing_record_is_built_in_memory_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        route = RouteFactory.create(track_render=None)
        with caplog.at_level(logging.WARNING, "apps.routes.services.track_render"):
            render = current_render(route)
        assert is_current(render)
        assert "missing or stale" in caplog.text
        route.refresh_from_db()
        assert route.track_render is None

    def test_a_stale_record_is_not_trusted(self) -> None:
        route = RouteFactory.create(track_render={"v": 0, "geometry": []})
        assert current_render(route)["v"] == TRACK_RENDER_VERSION

    def test_an_unsaved_draft_is_built_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        draft = Route(points=[[7.4, 46.1, None], [7.41, 46.11, None]])
        with caplog.at_level(logging.WARNING, "apps.routes.services.track_render"):
            current_render(draft)
        assert caplog.text == ""

    def test_the_route_feed_reads_the_stored_record(self, client: Client) -> None:
        parsed, record = _canonical(_STEMS[0])
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user, points=parsed.points, slope_samples=record
        )
        client.force_login(user)
        with patch(_BUILD, side_effect=AssertionError("built per request")):
            feature = client.get("/routes/routes.geojson").json()["features"][0]
            rows = client.get(f"/routes/{route.uuid}/terrain.json").json()["rows"]
        assert feature["geometry"]["coordinates"] == _stored(route)["geometry"]
        assert feature["properties"]["legs"] == _stored(route)["legs"]
        assert feature["properties"]["slope"] == _stored(route)["slope"]
        assert rows == _stored(route)["rows"]

    def test_the_trip_page_reads_the_stored_record(self) -> None:
        parsed, record = _canonical(_STEMS[0])
        trip = TripFactory.create(points=parsed.points, slope_samples=record)
        with patch(_BUILD, side_effect=AssertionError("built per request")):
            route = _trip_map_payload(trip)["route"]
        assert route["geometry"]["coordinates"] == _stored(trip)["geometry"]
        assert route["properties"]["legs"] == _stored(trip)["legs"]
        assert route["properties"]["terrain"] == _stored(trip)["terrain"]

    def test_climb_reads_the_stored_record(self) -> None:
        route = RouteFactory.create(
            track_render={"v": TRACK_RENDER_VERSION, "ascent_m": 7.0, "descent_m": 8.0}
        )
        assert tuple(route.climb) == (7.0, 8.0)
        trip = TripFactory.create(
            track_render={"v": TRACK_RENDER_VERSION, "ascent_m": 5.0, "descent_m": 6.0}
        )
        assert tuple(trip.climb) == (5.0, 6.0)


class TestLegLines:
    """The tours layer's slice of a stored record."""

    def test_a_stale_or_missing_record_has_no_lines(self) -> None:
        assert leg_lines(None) == []
        assert leg_lines({"v": 0, "legs": [{"climbing": True}]}) == []
