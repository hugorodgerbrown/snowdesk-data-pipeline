"""
tests/routes/test_drawn.py — a route drawn on the map (SNOW-1255/1258).

Covers ``apps.routes.services.drawn`` (the validation of posted waypoints
and the unsaved draft), ``apps.routes.services.estimate`` (the planning
time) and ``routes.create_drawn_route`` (the save, under the per-user cap).
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from django.test import override_settings

from apps.routes.models import Route
from apps.routes.services.drawn import (
    MAX_DRAWN_DISTANCE_M,
    DrawnRouteError,
    clean_name,
    clean_waypoints,
    draft_route,
)
from apps.routes.services.estimate import estimate_duration_s
from apps.routes.services.gpx import MAX_POINTS
from apps.routes.services.routes import RouteLimitReached, create_drawn_route
from tests.factories import RouteFactory, UserFactory


class TestCleanWaypoints:
    """What the browser sends is checked before anything is built from it."""

    def test_returns_stored_points_with_no_height(self) -> None:
        """``[[lon, lat], …]`` becomes ``[[lon, lat, None], …]``."""
        points = clean_waypoints([[7.0, 46.0], [7.0, 46.001]])

        assert points == [[7.0, 46.0, None], [7.0, 46.001, None]]

    def test_rounds_to_six_places(self) -> None:
        """About 0.1 m, far finer than a finger on a map."""
        points = clean_waypoints([[7.12345678, 46.0], [7.0, 46.001]])

        assert points[0][0] == 7.123457

    def test_collapses_a_double_tap(self) -> None:
        """Two consecutive points under half a metre apart are one."""
        points = clean_waypoints([[7.0, 46.0], [7.0, 46.000001], [7.0, 46.001]])

        assert len(points) == 2

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "7,46",
            {"lon": 7},
            [[7.0, 46.0]],
            [[7.0, 46.0], [7.0, 46.0]],
            [[7.0, 46.0], [True, False]],
            [[7.0, 46.0], ["7", "46"]],
            [[7.0, 46.0], [7.0]],
            [[7.0, 46.0], [181.0, 46.0]],
            [[7.0, 46.0], [7.0, 91.0]],
            [[7.0, 46.0], [math.inf, 46.0]],
            [[7.0, 46.0], [math.nan, 46.0]],
        ],
    )
    def test_refuses_what_cannot_make_a_route(self, raw: Any) -> None:
        """Not a list, too few distinct points, or a bad ordinate."""
        with pytest.raises(DrawnRouteError):
            clean_waypoints(raw)

    def test_refuses_too_many_points(self) -> None:
        """The upload's own point cap bounds a draft too."""
        raw = [[7.0, 46.0 + index / 100_000] for index in range(MAX_POINTS + 1)]

        with pytest.raises(DrawnRouteError):
            clean_waypoints(raw)

    def test_refuses_a_line_longer_than_the_bound(self) -> None:
        """The bound is what caps one preview's terrain walk."""
        degrees = (MAX_DRAWN_DISTANCE_M + 10_000) / 111_195

        with pytest.raises(DrawnRouteError):
            clean_waypoints([[7.0, 46.0], [7.0, 46.0 + degrees]])


class TestCleanName:
    """The name is stripped and cut to the column."""

    def test_strips_and_truncates(self) -> None:
        """Whitespace goes, and the column's length is kept."""
        assert clean_name("  Mont Fort  ") == "Mont Fort"
        assert len(clean_name("x" * 500)) == Route._meta.get_field("name").max_length

    def test_anything_but_a_string_is_no_name(self) -> None:
        """A missing or odd value is an empty name, not an error."""
        assert clean_name(None) == ""
        assert clean_name(42) == ""


class TestDraftRoute:
    """An unsaved row carrying the figures an upload's row carries."""

    def test_derives_distance_bounds_and_count(self) -> None:
        """The three derived fields, and the drawn source."""
        draft = draft_route(clean_waypoints([[7.0, 46.0], [7.01, 46.001]]), "Test")

        assert draft._state.adding
        assert draft.source == Route.Source.DRAWN
        assert draft.point_count == 2
        assert draft.bounds == [7.0, 46.0, 7.01, 46.001]
        assert draft.distance_m == pytest.approx(778, rel=0.01)

    def test_leaves_every_recorded_field_null(self) -> None:
        """Nothing was recorded, so nothing claims to have been."""
        draft = draft_route(clean_waypoints([[7.0, 46.0], [7.0, 46.001]]))

        assert draft.ascent_m is None
        assert draft.descent_m is None
        assert draft.started_at is None
        assert draft.source_point_count is None


class TestEstimateDurationS:
    """DIN 33466 for the climb and the flat, a skied descent."""

    def test_the_larger_plus_half_the_smaller(self) -> None:
        """8 km and 900 m up: 2 h flat, 3 h vertical — 3 h + 1 h = 4 h."""
        assert estimate_duration_s(8_000.0, 900.0, 0.0) == 4 * 3600

    def test_a_descent_is_skied(self) -> None:
        """1,500 m down adds an hour to the vertical half, not three."""
        assert estimate_duration_s(0.0, 900.0, 1_500.0) == 4 * 3600

    def test_rounds_to_ten_minutes(self) -> None:
        """An estimate is not quoted to the minute."""
        estimate = estimate_duration_s(1_000.0, 100.0, 0.0)

        assert estimate is not None
        assert estimate % 600 == 0

    def test_never_below_ten_minutes(self) -> None:
        """A short line still takes some time."""
        assert estimate_duration_s(50.0, 1.0, 0.0) == 600

    def test_unknown_climb_is_no_estimate(self) -> None:
        """Outside the terrain model there is nothing to estimate from."""
        assert estimate_duration_s(5_000.0, None, None) is None

    def test_nothing_is_no_estimate(self) -> None:
        """A route of no length and no climb takes no time."""
        assert estimate_duration_s(0.0, 0.0, 0.0) is None


@pytest.mark.django_db
class TestCreateDrawnRoute:
    """The save, under the same cap as an upload."""

    def test_saves_a_drawn_route(self) -> None:
        """One row, the drawn source, the given name."""
        user = UserFactory.create()

        route = create_drawn_route(
            user, clean_waypoints([[7.0, 46.0], [7.0, 46.001]]), name="Mont Fort"
        )

        stored = Route.objects.get(pk=route.pk)
        assert stored.user == user
        assert stored.source == Route.Source.DRAWN
        assert stored.name == "Mont Fort"

    def test_an_unnamed_draft_gets_a_label(self) -> None:
        """A row with no name reads as a broken one."""
        route = create_drawn_route(
            UserFactory.create(), clean_waypoints([[7.0, 46.0], [7.0, 46.001]])
        )

        assert route.name == "Drawn route"

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_refuses_past_the_cap(self) -> None:
        """The cap counts drawn and uploaded routes together."""
        user = UserFactory.create()
        RouteFactory.create(user=user)

        with pytest.raises(RouteLimitReached):
            create_drawn_route(user, clean_waypoints([[7.0, 46.0], [7.0, 46.001]]))
        assert Route.objects.filter(user=user).count() == 1
