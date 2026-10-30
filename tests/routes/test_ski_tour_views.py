"""
tests/routes/test_ski_tour_views.py — the SAC tour endpoints (SNOW-1278),
behind the ``route_drawing`` flag.

* ``routes:ski_tour_feature`` — GET, the tour as the route panel draws it,
  at once without the terrain and with it on ``?sample=1``;
* ``routes:ski_tour_terrain`` — GET, the tour's terrain rows for the
  point card (SNOW-1282);
* ``routes:ski_tour_save`` — POST, a copy onto the reader's routes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from waffle.testutils import override_flag

from apps.routes.models import Route, SkiTour
from apps.routes.services.track_render import TRACK_RENDER_VERSION
from tests.factories import RouteFactory, SkiTourFactory, UserFactory

pytestmark = pytest.mark.django_db

RECORD: dict[str, Any] = {"v": 1, "segments": []}


@pytest.fixture(autouse=True)
def drawing_on() -> Iterator[None]:
    """Turn the ``route_drawing`` flag on, with no summits cached."""
    cache.clear()
    with override_flag("route_drawing", active=True):
        yield


def _feature_url(tour: Any) -> str:
    """The tour's feature URL."""
    return reverse("routes:ski_tour_feature", args=[tour.source_uuid])


def _save_url(tour: Any) -> str:
    """The tour's save URL."""
    return reverse("routes:ski_tour_save", args=[tour.source_uuid])


class TestFeature:
    """``routes:ski_tour_feature``."""

    def test_an_unsampled_tour_answers_at_once_flagged_pending(
        self, client: Client
    ) -> None:
        """No grid walk; the panel is told to ask again."""
        tour = SkiTourFactory.create(ascent_time_label="03:30 h")
        with patch("apps.routes.services.ski_tours.build_slope_samples") as build:
            response = client.get(_feature_url(tour))
        build.assert_not_called()
        assert response.status_code == 200
        props = response.json()["feature"]["properties"]
        assert props["tour"] is True
        assert props["terrain_pending"] is True
        assert props["tour_uuid"] == str(tour.source_uuid)
        assert props["sac_time"] == "3 h 30"
        assert "uuid" not in props
        assert "token" not in props

    def test_a_tour_with_no_terrain_has_no_legs(self, client: Client) -> None:
        """No heights, no legs: one line, in no direction (SNOW-1310).

        A named way down is not pre-labelled one: its legs come with its
        terrain, as a route's do.
        """
        tour = SkiTourFactory.create(
            name="Descente à la cabane de Chanrion", ascent_m=420, descent_m=1660
        )
        props = client.get(_feature_url(tour)).json()["feature"]["properties"]
        assert props["terrain_pending"] is True
        assert "legs" not in props

    def test_the_line_follows_the_order_a_first_walk_settles(
        self, client: Client
    ) -> None:
        """The draft is built after the walk, in the order it was walked.

        A first walk that turns the line round must turn the draft round
        too: built before it, the draft kept the guessed order while the
        record held the other, and the terrain track fanned across the map
        until the tour was opened again.
        """
        tour = SkiTourFactory.create()
        walked = {**RECORD, "walked_reversed": True}

        def walk(row: Any, *, sample: bool) -> dict[str, Any]:
            row.slope_samples = walked
            return walked

        with (
            patch("apps.routes.views.tour_slope_samples", side_effect=walk),
            patch("apps.routes.views.draft_tour_route") as draft,
        ):
            draft.side_effect = lambda row: _assert_walked(row, walked)
            client.get(_feature_url(tour), {"sample": "1"})
        draft.assert_called_once()

    def test_a_sampled_tour_splits_into_legs_as_a_route_does(
        self, client: Client
    ) -> None:
        """With heights, the detected legs stand: up and down where the ground turns.

        SNOW-1307 (Hugo, 2026-10-30: "display up and down as a user route
        does"); the one leg is only the stand-in for a tour with no heights.
        """
        tour = SkiTourFactory.create()
        detected = [
            {"i": 1, "from": 0, "to": 0, "climbing": False},
            {"i": 2, "from": 1, "to": 1, "climbing": True},
        ]
        with patch(
            "apps.routes.services.track_render.wire_legs", return_value=detected
        ):
            props = client.get(_feature_url(tour)).json()["feature"]["properties"]
        assert props["legs"] == detected

    def test_a_tour_with_a_stored_render_is_read_not_built(
        self, client: Client
    ) -> None:
        """SNOW-1309: the panel reads the tour's render, as the map a route's."""
        tour = SkiTourFactory.create(slope_samples=RECORD)
        stored = {
            "v": TRACK_RENDER_VERSION,
            "geometry": [[10.0, 46.0, 1800.0], [10.0, 46.01, 2400.0]],
            "ascent_m": 600.0,
            "descent_m": 0.0,
            "legs": [{"i": 1, "from": 0, "to": 0, "climbing": True}],
            "rows": [],
        }
        SkiTour.objects.filter(pk=tour.pk).update(track_render=stored)
        with patch(
            "apps.routes.services.track_render.build_track_render",
            side_effect=AssertionError("built per request"),
        ):
            feature = client.get(_feature_url(tour)).json()["feature"]
        assert feature["geometry"]["coordinates"] == stored["geometry"]
        assert feature["properties"]["legs"] == stored["legs"]
        assert feature["properties"]["ascent_m"] == 600.0

    def test_sample_walks_the_grid_and_keeps_the_record(self, client: Client) -> None:
        """``?sample=1`` answers with the terrain and stores it."""
        tour = SkiTourFactory.create()
        with patch(
            "apps.routes.services.ski_tours.build_slope_samples", return_value=RECORD
        ):
            response = client.get(_feature_url(tour), {"sample": "1"})
        assert response.status_code == 200
        assert "terrain_pending" not in response.json()["feature"]["properties"]
        tour.refresh_from_db()
        assert tour.slope_samples == {**RECORD, "walked_reversed": False}

    def test_a_sampled_tour_is_not_pending(self, client: Client) -> None:
        """The held record answers the first request."""
        tour = SkiTourFactory.create(slope_samples=RECORD)
        props = client.get(_feature_url(tour)).json()["feature"]["properties"]
        assert "terrain_pending" not in props

    def test_an_unknown_tour_is_404(self, client: Client) -> None:
        """By uuid."""
        url = reverse(
            "routes:ski_tour_feature", args=["00000000-0000-0000-0000-000000000000"]
        )
        assert client.get(url).status_code == 404

    @override_flag("route_drawing", active=False)
    def test_the_flag_off_is_404(self, client: Client) -> None:
        """Invisible."""
        assert client.get(_feature_url(SkiTourFactory.create())).status_code == 404

    def test_the_rate_limit_answers_429(self, client: Client) -> None:
        """As the draw preview's."""
        tour = SkiTourFactory.create()
        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            assert client.get(_feature_url(tour)).status_code == 429


def _terrain_url(tour: Any) -> str:
    """The tour's terrain rows URL."""
    return reverse("routes:ski_tour_terrain", args=[tour.source_uuid])


class TestTerrain:
    """``routes:ski_tour_terrain``."""

    def test_a_sampled_tour_answers_its_rows(self, client: Client) -> None:
        """The held record and the tour's own points make the rows."""
        tour = SkiTourFactory.create(slope_samples=RECORD)
        rows = [{"index": 0, "ground_deg": 31.0}]
        with patch(
            "apps.routes.services.track_render.terrain_detail", return_value=rows
        ) as detail:
            response = client.get(_terrain_url(tour))
        assert response.status_code == 200
        assert response.json() == {"rows": rows}
        samples, points = detail.call_args.args
        assert samples == RECORD
        assert points and points[0][2] is None

    def test_a_stored_render_answers_its_rows(self, client: Client) -> None:
        """SNOW-1309: the rows are read from the render, never computed."""
        tour = SkiTourFactory.create(slope_samples=RECORD)
        rows = [{"index": 0, "ground_deg": 31.0}]
        SkiTour.objects.filter(pk=tour.pk).update(
            track_render={"v": TRACK_RENDER_VERSION, "geometry": [], "rows": rows}
        )
        with patch(
            "apps.routes.services.track_render.terrain_detail",
            side_effect=AssertionError("computed per request"),
        ):
            response = client.get(_terrain_url(tour))
        assert response.json() == {"rows": rows}

    def test_an_unsampled_tour_answers_no_rows_and_walks_no_grid(
        self, client: Client
    ) -> None:
        """Sampling is the feature's ``?sample=1``, never this."""
        tour = SkiTourFactory.create()
        with patch("apps.routes.services.ski_tours.build_slope_samples") as build:
            response = client.get(_terrain_url(tour))
        build.assert_not_called()
        assert response.json() == {"rows": []}
        # Never held: the sampling the panel triggers next must be seen.
        assert "no-store" in response["Cache-Control"]

    def test_an_unknown_tour_is_404(self, client: Client) -> None:
        """By uuid."""
        url = reverse(
            "routes:ski_tour_terrain", args=["00000000-0000-0000-0000-000000000000"]
        )
        assert client.get(url).status_code == 404

    @override_flag("route_drawing", active=False)
    def test_the_flag_off_is_404(self, client: Client) -> None:
        """Invisible."""
        assert client.get(_terrain_url(SkiTourFactory.create())).status_code == 404


class TestSave:
    """``routes:ski_tour_save``."""

    def test_a_signed_in_reader_gets_a_copy(self, client: Client) -> None:
        """A SKI_TOUR route keeping the tour's uuid."""
        user = UserFactory.create()
        client.force_login(user)
        tour = SkiTourFactory.create(slope_samples=RECORD)
        response = client.post(_save_url(tour))
        assert response.status_code == 200
        route = Route.objects.get(user=user)
        assert response.json() == {"uuid": str(route.uuid), "bounds": route.bounds}
        assert route.ski_tour_uuid == tour.source_uuid

    def test_anonymous_is_403(self, client: Client) -> None:
        """Saving needs an account."""
        assert client.post(_save_url(SkiTourFactory.create())).status_code == 403
        assert not Route.objects.exists()

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_the_cap_is_409(self, client: Client) -> None:
        """The account is full."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)
        assert client.post(_save_url(SkiTourFactory.create())).status_code == 409

    def test_the_rate_limit_answers_429(self, client: Client) -> None:
        """As a drawn save's."""
        client.force_login(UserFactory.create())
        tour = SkiTourFactory.create()
        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            assert client.post(_save_url(tour)).status_code == 429

    @override_flag("route_drawing", active=False)
    def test_the_flag_off_is_404(self, client: Client) -> None:
        """Invisible."""
        client.force_login(UserFactory.create())
        assert client.post(_save_url(SkiTourFactory.create())).status_code == 404

    def test_get_is_405(self, client: Client) -> None:
        """POST only."""
        assert client.get(_save_url(SkiTourFactory.create())).status_code == 405


def _assert_walked(tour: Any, record: dict[str, Any]) -> Any:
    """Fail unless ``tour`` already holds ``record``; then draft it."""
    from apps.routes.services.ski_tours import draft_tour_route  # noqa: PLC0415 — the real one, past the patch

    assert tour.slope_samples is record
    return draft_tour_route(tour)
