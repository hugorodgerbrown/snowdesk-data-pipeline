"""
tests/routes/test_ski_tour_views.py — the SAC tour endpoints (SNOW-1278),
behind the ``route_drawing`` flag.

* ``routes:ski_tour_feature`` — GET, the tour as the route panel draws it,
  at once without the terrain and with it on ``?sample=1``;
* ``routes:ski_tour_terrain`` — GET, the tour's terrain rows for the
  point card (SNOW-1282);
* ``routes:ski_tour_save`` — POST, a copy onto the reader's routes;
* ``routes:ski_tour_list`` — the HTMX list of a summit's tours or a tap's.
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

from apps.routes.models import Route
from tests.factories import RouteFactory, SkiTourFactory, UserFactory

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}
LIST_URL = reverse("routes:ski_tour_list")
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
        assert tour.slope_samples == RECORD

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
        with patch("apps.routes.views.terrain_detail", return_value=rows) as detail:
            response = client.get(_terrain_url(tour))
        assert response.status_code == 200
        assert response.json() == {"rows": rows}
        samples, points = detail.call_args.args
        assert samples == RECORD
        assert points and points[0][2] is None

    def test_an_unsampled_tour_answers_no_rows_and_walks_no_grid(
        self, client: Client
    ) -> None:
        """Sampling is the feature's ``?sample=1``, never this."""
        tour = SkiTourFactory.create()
        with patch("apps.routes.services.ski_tours.build_slope_samples") as build:
            response = client.get(_terrain_url(tour))
        build.assert_not_called()
        assert response.json() == {"rows": []}

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


class TestList:
    """``routes:ski_tour_list``."""

    def test_a_summit_lists_its_ways_up_and_down(self, client: Client) -> None:
        """Counted, grouped, named by SAC's names."""
        SkiTourFactory.create(name="Von der Hütte", ascent_time_label="03:30 h")
        SkiTourFactory.create(name="Abfahrt nach Lavin", ascent_m=None, descent_m=1800)
        response = client.get(LIST_URL, {"summit": "piz-buin-3312"}, headers=HTMX)
        assert response.status_code == 200
        body = response.content.decode()
        assert "Piz Buin · 3312 m" in body
        assert "2 SAC routes" in body
        assert "1 up, 1 down" in body
        assert "Von der Hütte" in body
        assert "3 h 30" in body
        assert "↓ 1800 m" in body
        assert body.count("data-ski-tour=") == 2

    def test_a_tap_lists_its_tours_by_summit(self, client: Client) -> None:
        """List them as "2 routes pass here", each row titled by its summit."""
        one = SkiTourFactory.create()
        two = SkiTourFactory.create(target_name="Signalhorn", target_altitude_m=3207)
        SkiTourFactory.create()
        response = client.get(
            LIST_URL, {"tours": f"{one.source_uuid},{two.source_uuid}"}, headers=HTMX
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "2 routes pass here" in body
        assert "Signalhorn · 3207 m" in body
        assert body.count("data-ski-tour=") == 2

    @pytest.mark.parametrize("query", [{}, {"tours": "not-a-uuid"}])
    def test_a_bad_query_is_400(self, client: Client, query: dict[str, str]) -> None:
        """Neither parameter, or a malformed uuid."""
        assert client.get(LIST_URL, query, headers=HTMX).status_code == 400

    def test_an_unknown_summit_is_404(self, client: Client) -> None:
        """No tours go there."""
        assert (
            client.get(LIST_URL, {"summit": "nowhere-0"}, headers=HTMX).status_code
            == 404
        )

    def test_a_plain_request_is_400(self, client: Client) -> None:
        """``require_htmx``."""
        assert client.get(LIST_URL, {"summit": "piz-buin-3312"}).status_code == 400

    @override_flag("route_drawing", active=False)
    def test_the_flag_off_is_404(self, client: Client) -> None:
        """Invisible."""
        SkiTourFactory.create()
        response = client.get(LIST_URL, {"summit": "piz-buin-3312"}, headers=HTMX)
        assert response.status_code == 404
