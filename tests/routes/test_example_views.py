"""
tests/routes/test_example_views.py

Covers the example route's views (SNOW-1230):
  - route_example: 404 with no example; a 302 to the map carrying the
    token, example=1 and the example's day; the token lands in the session
    on a real navigation only; never stored.
  - route_share_terrain: the terrain rows for a token this session holds;
    404 for a token it does not, or a dead share.
  - route_share_dismiss: drops a held token; 204 for any token; POST only.
  - routes_geojson: the example's feature carries example / example_date.
  - route_list: the example's pending row has the "Example route ·" prefix
    and no Save; the empty clause links the example when one is set.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.routes.services.shares import PENDING_SESSION_KEY
from apps.routes.services.terrain_detail import terrain_detail
from tests.factories import (
    DRIFTING_TRACK,
    RouteFactory,
    RouteShareFactory,
    UserFactory,
    drifting_track_record,
)

HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}
EXAMPLE_URL = "/routes/example/"
LIST_URL = "/routes/partials/list/?variant=map"


def _terrain_url(token: str) -> str:
    """Build a pending share's terrain URL."""
    return f"/routes/s/{token}/terrain.json"


def _dismiss_url(token: str) -> str:
    """Build a pending share's dismiss URL."""
    return f"/routes/s/{token}/dismiss/"


@pytest.mark.django_db
class TestRouteExample:
    """The one address for "the example route"."""

    def test_no_example_is_404(self, client: Client) -> None:
        """Nothing set, nothing to open."""
        assert client.get(EXAMPLE_URL).status_code == 404

    def test_it_redirects_to_the_map_on_the_examples_day(self, client: Client) -> None:
        """Token, the walkthrough's flag and the day, in one URL."""
        share = RouteShareFactory.create(example=True)

        response = client.get(EXAMPLE_URL)

        assert response.status_code == 302
        assert response["Location"] == (
            f"/map/?route_share={share.token}&example=1&d=2026-04-10"
        )
        assert response["Cache-Control"] == "no-store"

    def test_the_token_lands_in_the_session(self, client: Client) -> None:
        """So the panel, the profile and the terrain data read it."""
        share = RouteShareFactory.create(example=True)

        client.get(EXAMPLE_URL)

        assert share.token in client.session[PENDING_SESSION_KEY]

    def test_a_prefetch_writes_nothing(self, client: Client) -> None:
        """A real navigation only, as the share link."""
        RouteShareFactory.create(example=True)

        client.get(EXAMPLE_URL, HTTP_SEC_PURPOSE="prefetch")

        assert PENDING_SESSION_KEY not in client.session


@pytest.mark.django_db
class TestRouteShareTerrain:
    """Terrain data for any route this browser was sent."""

    def test_a_held_token_gets_the_rows(self, client: Client) -> None:
        """The rows are terrain_detail's own."""
        route = RouteFactory.create(
            points=DRIFTING_TRACK, slope_samples=drifting_track_record()
        )
        share = RouteShareFactory.create(route=route)
        client.get(f"/routes/s/{share.token}/")

        response = client.get(_terrain_url(share.token))

        assert response.status_code == 200
        assert response.json() == {
            "rows": terrain_detail(route.slope_samples, route.points) or []
        }

    def test_a_token_this_session_does_not_hold_is_404(self, client: Client) -> None:
        """Not a guessing oracle."""
        share = RouteShareFactory.create()

        assert client.get(_terrain_url(share.token)).status_code == 404

    def test_a_dead_share_is_404(self, client: Client) -> None:
        """Held, but expired since."""
        share = RouteShareFactory.create()
        client.get(f"/routes/s/{share.token}/")
        share.expires_at = timezone.now()
        share.save(update_fields=["expires_at"])

        assert client.get(_terrain_url(share.token)).status_code == 404


@pytest.mark.django_db
class TestRouteShareDismiss:
    """Closing the example drops it from the session."""

    def test_a_held_token_is_dropped(self, client: Client) -> None:
        """The example stops being drawn and listed."""
        share = RouteShareFactory.create(example=True)
        client.get(EXAMPLE_URL)

        response = client.post(_dismiss_url(share.token))

        assert response.status_code == 204
        assert share.token not in client.session.get(PENDING_SESSION_KEY, [])

    def test_any_token_answers_204(self, client: Client) -> None:
        """The same answer whether held or not."""
        response = client.post(_dismiss_url("not-a-token"))

        assert response.status_code == 204
        assert PENDING_SESSION_KEY not in client.session

    def test_get_is_not_allowed(self, client: Client) -> None:
        """POST only."""
        assert client.get(_dismiss_url("x")).status_code == 405


@pytest.mark.django_db
class TestExampleOnTheMapAndPanel:
    """How the example reads where pending shares are read."""

    def test_the_geojson_feature_names_the_example(self, client: Client) -> None:
        """The map marks the day and offers no Save."""
        RouteShareFactory.create(example=True)
        client.get(EXAMPLE_URL)

        features = client.get("/routes/routes.geojson").json()["features"]

        assert features[0]["properties"]["example"] is True
        assert features[0]["properties"]["example_date"] == "2026-04-10"

    def test_the_panel_row_is_labelled_and_has_no_save(self, client: Client) -> None:
        """Never claimable, so no control that would be refused."""
        client.force_login(UserFactory.create())
        RouteShareFactory.create(example=True)
        client.get(EXAMPLE_URL)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert "Example route ·" in content
        assert "data-row-claimed" not in content
        assert "Sign in to save this route" not in content

    def test_the_empty_clause_offers_the_example(self, client: Client) -> None:
        """A visitor with no GPX can see what one becomes."""
        client.force_login(UserFactory.create())
        RouteShareFactory.create(example=True)

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert 'data-testid="route-list-example"' in content
        assert 'href="/routes/example/"' in content

    def test_no_example_no_offer(self, client: Client) -> None:
        """The link is drawn only when it leads somewhere."""
        client.force_login(UserFactory.create())

        content = client.get(LIST_URL, **HTMX_HEADERS).content.decode()

        assert "route-list-example" not in content
