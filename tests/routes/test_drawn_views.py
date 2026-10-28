"""
tests/routes/test_drawn_views.py — the drawn-route endpoints (SNOW-1255/
1257/1260), behind the ``route_drawing`` flag.

* ``routes:draw_preview`` — POST, no account needed, describes a draft;
* ``routes:create_drawn`` — POST, saves a draft for a signed-in user;
* ``routes:gpx`` — GET, the owner's download of any route.

The flag goes on the minting surfaces only (docs/feature-flags.md), so each
endpoint answers 404 with it off, and a saved drawn route reads on every
reading surface whatever the flag says.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from waffle.testutils import override_flag

from apps.routes.models import Route
from apps.routes.services.gpx import parse_gpx
from tests.factories import (
    DRIFTING_TRACK,
    RouteFactory,
    UserFactory,
    drifting_track_record,
)

PREVIEW_URL = reverse("routes:draw_preview")
CREATE_URL = reverse("routes:create_drawn")
GEOJSON_URL = reverse("routes:geojson")
WAYPOINTS = [[7.0, 46.0], [7.0, 46.001]]


@pytest.fixture
def drawing_on() -> Iterator[None]:
    """Turn the ``route_drawing`` flag on for every request in the test."""
    with override_flag("route_drawing", active=True):
        yield


def _post(client: Client, url: str, body: Any) -> Any:
    """POST a JSON body the way route_draw.js does.

    Args:
        client: The test client.
        url: The endpoint.
        body: The value to encode, or raw ``bytes`` sent as they are.

    Returns:
        The response.

    """
    data = body if isinstance(body, bytes) else json.dumps(body)
    return client.post(url, data=data, content_type="application/json")


@pytest.mark.django_db
class TestFlagGate:
    """Every drawing endpoint is invisible with the flag off."""

    @override_flag("route_drawing", active=False)
    def test_preview_404s(self, client: Client) -> None:
        """No preview."""
        assert _post(client, PREVIEW_URL, {"points": WAYPOINTS}).status_code == 404

    @override_flag("route_drawing", active=False)
    def test_create_404s(self, client: Client) -> None:
        """No save."""
        client.force_login(UserFactory.create())
        assert _post(client, CREATE_URL, {"points": WAYPOINTS}).status_code == 404
        assert not Route.objects.exists()

    @override_flag("route_drawing", active=False)
    def test_gpx_404s(self, client: Client) -> None:
        """No download."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)
        assert client.get(reverse("routes:gpx", args=[route.uuid])).status_code == 404


@pytest.mark.django_db
@pytest.mark.usefixtures("drawing_on")
class TestDrawPreview:
    """A draft described exactly as the routes layer will describe it."""

    def test_needs_no_account(self, client: Client) -> None:
        """Drawing is free."""
        response = _post(client, PREVIEW_URL, {"points": WAYPOINTS})

        assert response.status_code == 200
        feature = response.json()["feature"]
        assert feature["type"] == "Feature"
        assert feature["properties"]["draft"] is True
        assert feature["properties"]["source"] == "DRAWN"
        assert "uuid" not in feature["properties"]

    def test_writes_nothing(self, client: Client) -> None:
        """A preview is not a save."""
        _post(client, PREVIEW_URL, {"points": WAYPOINTS})

        assert not Route.objects.exists()

    def test_outside_the_terrain_model_claims_nothing_about_the_ground(
        self, client: Client
    ) -> None:
        """No slope, no climb, no estimate — the line alone."""
        with patch("apps.routes.views.build_slope_samples", return_value=None):
            props = _post(client, PREVIEW_URL, {"points": WAYPOINTS}).json()["feature"][
                "properties"
            ]

        assert "slope" not in props
        assert props["ascent_m"] is None
        assert props["estimate_s"] is None

    def test_on_modelled_ground_carries_the_climb_and_an_estimate(
        self, client: Client
    ) -> None:
        """The model's heights, the climb they give and the time it takes."""
        points = [point[:2] for point in DRIFTING_TRACK]
        with patch(
            "apps.routes.views.build_slope_samples",
            return_value=drifting_track_record(),
        ):
            feature = _post(client, PREVIEW_URL, {"points": points}).json()["feature"]

        props = feature["properties"]
        assert "slope" in props
        assert props["ascent_m"] == pytest.approx(40.0)
        assert props["estimate_s"] == 600
        assert props["duration_s"] is None
        # The profile is drawn from the model's heights on the geometry.
        assert feature["geometry"]["coordinates"][0][2] == pytest.approx(2000.0)

    @pytest.mark.parametrize(
        "body",
        [b"not json", b"[1, 2]", {"points": [[7.0, 46.0]]}, {"points": "x"}],
    )
    def test_refuses_a_body_that_is_not_a_route(
        self, client: Client, body: Any
    ) -> None:
        """400 with a fixed message, never the parser's own words."""
        response = _post(client, PREVIEW_URL, body)

        assert response.status_code == 400
        assert "error" in response.json()

    def test_is_post_only(self, client: Client) -> None:
        """A GET is refused."""
        assert client.get(PREVIEW_URL).status_code == 405

    def test_is_rate_limited(self, client: Client) -> None:
        """429 once the IP has spent its minute."""
        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            response = _post(client, PREVIEW_URL, {"points": WAYPOINTS})

        assert response.status_code == 429


@pytest.mark.django_db
@pytest.mark.usefixtures("drawing_on")
class TestCreateDrawn:
    """Saving a draft needs an account, and lands an ordinary Route."""

    def test_anonymous_is_refused(self, client: Client) -> None:
        """403, nothing stored."""
        response = _post(client, CREATE_URL, {"points": WAYPOINTS})

        assert response.status_code == 403
        assert not Route.objects.exists()

    def test_saves_and_answers_with_the_route(self, client: Client) -> None:
        """The uuid and the bounds the map frames."""
        user = UserFactory.create()
        client.force_login(user)

        response = _post(client, CREATE_URL, {"points": WAYPOINTS, "name": "Test"})

        assert response.status_code == 200
        body = response.json()
        route = Route.objects.get(user=user)
        assert body["uuid"] == str(route.uuid)
        assert body["bounds"] == route.bounds
        assert route.source == Route.Source.DRAWN
        assert route.name == "Test"

    def test_a_saved_drawn_route_is_on_the_routes_layer(self, client: Client) -> None:
        """The reading surface needs no flag — only minting does."""
        user = UserFactory.create()
        client.force_login(user)
        _post(client, CREATE_URL, {"points": WAYPOINTS})

        with override_flag("route_drawing", active=False):
            features = client.get(GEOJSON_URL).json()["features"]

        assert [feature["properties"]["source"] for feature in features] == ["DRAWN"]
        assert "estimate_s" in features[0]["properties"]

    def test_refuses_bad_waypoints(self, client: Client) -> None:
        """400, nothing stored."""
        client.force_login(UserFactory.create())

        response = _post(client, CREATE_URL, {"points": [[7.0, 46.0]]})

        assert response.status_code == 400
        assert not Route.objects.exists()

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_refuses_past_the_cap(self, client: Client) -> None:
        """409, the same answer an upload gets."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)

        response = _post(client, CREATE_URL, {"points": WAYPOINTS})

        assert response.status_code == 409
        assert Route.objects.filter(user=user).count() == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("drawing_on")
class TestRouteGpx:
    """The owner's download of a route as GPX."""

    def test_downloads_the_owners_route(self, client: Client) -> None:
        """An attachment the project's own parser reads back."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user, name="Col de Balme")

        response = client.get(reverse("routes:gpx", args=[route.uuid]))

        assert response.status_code == 200
        assert response["Content-Type"] == "application/gpx+xml"
        assert "attachment" in response["Content-Disposition"]
        assert len(parse_gpx(response.content).points) == route.point_count

    def test_anonymous_is_refused(self, client: Client) -> None:
        """403."""
        route = RouteFactory.create()

        assert client.get(reverse("routes:gpx", args=[route.uuid])).status_code == 403

    def test_another_users_route_is_not_found(self, client: Client) -> None:
        """404, never 403 — a uuid is not confirmed to exist."""
        client.force_login(UserFactory.create())
        route = RouteFactory.create()

        assert client.get(reverse("routes:gpx", args=[route.uuid])).status_code == 404


@pytest.mark.django_db
class TestRowMenuDownload:
    """The row's Download GPX item follows the flag."""

    def _rows(self, client: Client) -> str:
        """Render the map panel's rows for the logged-in user."""
        return client.get(
            reverse("routes:list") + "?variant=map", HTTP_HX_REQUEST="true"
        ).content.decode()

    def test_shown_with_the_flag_on(self, client: Client) -> None:
        """The item links to the download."""
        user = UserFactory.create()
        client.force_login(user)
        route = RouteFactory.create(user=user)

        with override_flag("route_drawing", active=True):
            body = self._rows(client)

        assert reverse("routes:gpx", args=[route.uuid]) in body

    def test_absent_with_the_flag_off(self, client: Client) -> None:
        """No item, no link."""
        user = UserFactory.create()
        client.force_login(user)
        RouteFactory.create(user=user)

        with override_flag("route_drawing", active=False):
            body = self._rows(client)

        assert "data-route-gpx" not in body


@pytest.mark.django_db
class TestMapPageDrawSurface:
    """The map page carries the draw surface only with the flag on."""

    def test_present_with_the_flag_on(self, client: Client) -> None:
        """The CTA, the panel and its script, for a signed-in user."""
        client.force_login(UserFactory.create())
        with override_flag("route_drawing", active=True):
            body = client.get(reverse("public:map")).content.decode()

        assert "data-route-draw-start" in body
        assert 'id="route-draw-sheet"' in body
        assert "js/route_draw.js" in body
        assert "Upload a GPX" in body
        # Draw mode follows the ski-touring network (SNOW-1277).
        assert "js/ski_snap_core.js" in body
        assert f'data-network-url="{reverse("api:ski_network_geojson")}"' in body
        assert "data-route-draw-follow" in body
        # swisstopo's ski-touring routes ride on the same flag.
        assert 'data-overlay-key="ski_routes"' in body
        assert 'data-ski-routes-eligible="true"' in body
        # SNOW-1278: three ways to add a route, replacing the add button,
        # and "Find a route"'s surface.
        assert "data-ski-tour-find" in body
        assert "Find a route" in body
        assert "Create a route" in body
        assert 'id="ski-tours-sheet"' in body
        assert "js/ski_tours.js" in body
        assert f'data-geojson-url="{reverse("api:ski_tours_geojson")}"' in body
        # SNOW-1282: the route panel reads a tour's terrain rows too.
        nil = "00000000-0000-0000-0000-000000000000"
        tour_terrain = reverse("routes:ski_tour_terrain", args=[nil])
        assert f'data-route-tour-terrain-url-template="{tour_terrain}"' in body
        assert "Add a route" not in body

    def test_absent_for_an_anonymous_visitor(self, client: Client) -> None:
        """No waffle read and no surface for a visitor with no session."""
        with override_flag("route_drawing", active=True):
            body = client.get(reverse("public:map")).content.decode()

        assert "data-route-draw-start" not in body
        assert 'data-overlay-key="ski_routes"' not in body
        assert 'data-ski-routes-eligible="false"' in body

    def test_absent_with_the_flag_off(self, client: Client) -> None:
        """The panel reads as it did before."""
        client.force_login(UserFactory.create())
        with override_flag("route_drawing", active=False):
            body = client.get(reverse("public:map")).content.decode()

        assert "data-route-draw-start" not in body
        assert 'id="route-draw-sheet"' not in body
        assert "data-ski-tour-find" not in body
        assert 'id="ski-tours-sheet"' not in body
        assert "Add a route" in body
        assert 'data-overlay-key="ski_routes"' not in body
