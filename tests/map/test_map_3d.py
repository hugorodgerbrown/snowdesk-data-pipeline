"""
tests/map/test_map_3d.py — the 3-D terrain map at /map/3d/ (SNOW-1438).

Pins the gate (a 404 for anyone outside the map_3d flag, and no waffle query
for an anonymous visitor), the URLs the script reads from #map-3d, the
Steep terrain switch following TERRAIN_CLASS_TILE_URL, and the page never
unfurling or being indexed. Then the route share: ``?route_share=`` opening
the page on one route for anyone, only for a share by a 3-D reader, and
/map/'s 3D roundel offered to a friend who followed one.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from apps.map.map_3d import route_share_for_3d
from apps.map.models import Map3dShare
from apps.routes.models import RouteShare
from apps.routes.services.shares import PENDING_SESSION_KEY, SHARE_FOLLOW_GROUP
from tests.factories import (
    Map3dShareFactory,
    RouteFactory,
    RouteShareFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

URL = "/map/3d/"


def _signed_in() -> Client:
    """Return a client signed in as a fresh user.

    Returns:
        The client.

    """
    client = Client()
    client.force_login(UserFactory.create())
    return client


def test_the_route_is_map_3d() -> None:
    """The page is public:map_3d at /map/3d/."""
    assert reverse("public:map_3d") == URL


def test_an_anonymous_visitor_gets_a_404_without_asking_waffle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signed out is a 404 before waffle is asked anything."""
    asked: list[str] = []

    def _flag_is_active(request: object, name: str) -> bool:
        """Record the question and answer yes, so only the guard can 404."""
        asked.append(name)
        return True

    monkeypatch.setattr("apps.map.map_3d.waffle.flag_is_active", _flag_is_active)
    response = Client().get(URL)
    assert response.status_code == 404
    assert asked == []


def test_a_signed_in_user_outside_the_flag_gets_a_404() -> None:
    """The flag off is a 404, not a redirect or a teaser."""
    with override_flag("map_3d", active=False):
        response = _signed_in().get(URL)
    assert response.status_code == 404


def test_the_flag_renders_the_map() -> None:
    """Inside the flag the page renders #map-3d and its scripts."""
    with override_flag("map_3d", active=True):
        response = _signed_in().get(URL)
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="map-3d"' in content
    for script in (
        "js/maplibre-gl.min.js",
        "js/slope_classes_core.js",
        "js/terrain_filter_core.js",
        "js/map_3d_core.js",
        "js/map_3d.js",
    ):
        assert script in content
    assert content.index("js/terrain_filter_core.js") < content.index("js/map_3d.js")


def test_the_terrain_helper_loads_between_the_core_and_the_map() -> None:
    """SNOW-1449: map_3d.js installs its relief through map_3d_terrain.js.

    The helper reads map_3d_core.js at parse time and map_3d.js reads the
    helper at parse time, so the order is core, helper, map.
    """
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    core = content.index("js/map_3d_core.js")
    helper = content.index("js/map_3d_terrain.js")
    assert core < helper < content.index("js/map_3d.js")


def test_the_page_carries_every_url_the_script_reads() -> None:
    """The style, DEM, tours, routes and resorts URLs ride on #map-3d."""
    dem = "https://tiles.example.test/dem/2026-11/{z}/{x}/{y}.webp"
    with (
        override_flag("map_3d", active=True),
        override_settings(MAP_3D_DEM_TILE_URL=dem),
    ):
        content = _signed_in().get(URL).content.decode()
    assert "ch.swisstopo.basemap-winter.vt/style.json" in content
    assert f'data-dem-tile-url="{dem}"' in content
    assert f'data-ski-tours-url="{reverse("api:ski_tours_geojson")}"' in content
    assert f'data-routes-url="{reverse("routes:geojson")}"' in content
    assert f'data-resorts-url="{reverse("api:resorts_geojson")}"' in content


@override_settings(
    TERRAIN_CLASS_TILE_URL="https://tiles.example/terrain-class/v1/{z}/{x}/{y}.png"
)
def test_steep_terrain_switch_shows_with_the_class_tiles() -> None:
    """A configured class-tile template brings the switch, on by default."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert 'data-terrain-class-tile-url="https://tiles.example/' in content
    assert 'id="map-3d-steep"' in content
    assert "Steep terrain" in content
    assert "js/map_3d_steep_worker.js" in content
    assert (
        "/static/js/slope_classes_core.js /static/js/terrain_filter_core.js" in content
    )


@override_settings(TERRAIN_CLASS_TILE_URL="")
def test_no_class_tiles_means_no_steep_terrain_switch() -> None:
    """An unset template leaves Steep terrain out of the page."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert 'data-terrain-class-tile-url=""' in content
    assert 'id="map-3d-steep"' not in content


def test_the_page_never_unfurls_and_is_not_indexed() -> None:
    """sharing=False drops the share card; noindex keeps it out of search."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert 'property="og:title"' not in content
    assert '<meta name="robots" content="noindex, nofollow">' in content


def test_tours_and_wildlife_switches_and_share_render() -> None:
    """SAC tours (on), Wildlife (off) and Share are always on the page."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert 'id="map-3d-tours"' in content
    assert 'id="map-3d-wildlife"' in content
    assert 'id="map-3d-share"' in content
    assert 'id="map-3d-strings-template"' in content
    assert "js/share.js" in content


def test_a_flagged_reader_gets_one_share_token() -> None:
    """Opening the page mints the reader's token once and puts it on #map-3d."""
    client = _signed_in()
    with override_flag("map_3d", active=True):
        first = client.get(URL).content.decode()
        client.get(URL)
    share = Map3dShare.objects.get()
    assert f'data-share-token="{share.token}"' in first


def test_a_share_link_opens_the_page_for_an_anonymous_visitor() -> None:
    """A live token bypasses the flag and sign-in, and shows no one's routes."""
    share = Map3dShareFactory.create()
    response = Client().get(URL, {"share": share.token})
    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-routes-url=""' in content
    assert f'data-share-token="{share.token}"' in content


def test_a_deleted_or_unknown_token_is_a_404() -> None:
    """Deleting the row revokes every link that carries it."""
    share = Map3dShareFactory.create()
    token = share.token
    share.delete()
    assert Client().get(URL, {"share": token}).status_code == 404
    assert Client().get(URL, {"share": "nope"}).status_code == 404


# ---------------------------------------------------------------------------
# A route share opens the page on that route
# ---------------------------------------------------------------------------


def _route_share_by_a_3d_reader(**kwargs: object) -> RouteShare:
    """Return a live route share whose sharer holds a Map3dShare.

    Args:
        **kwargs: Passed to the RouteShare factory.

    Returns:
        The share.

    """
    share = RouteShareFactory.create(**kwargs)
    Map3dShareFactory.create(created_by=share.created_by)
    return share


def test_a_route_share_opens_the_page_for_an_anonymous_visitor() -> None:
    """The route is drawn through routes.geojson; the sharer's token is not handed on."""
    share = _route_share_by_a_3d_reader()
    sharer_token = Map3dShare.objects.get(created_by=share.created_by).token
    client = Client()
    response = client.get(URL, {"route_share": share.token})
    content = response.content.decode()
    assert response.status_code == 200
    assert f'data-routes-url="{reverse("routes:geojson")}"' in content
    assert f'data-route-share-token="{share.token}"' in content
    assert 'data-share-token=""' in content
    assert sharer_token not in content
    # Only a reader inside the flag shares their own routes from here.
    assert 'data-route-share-url-template=""' in content
    assert client.session[PENDING_SESSION_KEY] == [share.token]


def test_a_route_share_visitor_sees_only_the_shared_route() -> None:
    """routes.geojson, as the page asks for it, holds the share and none of the sharer's routes."""
    share = _route_share_by_a_3d_reader()
    RouteFactory.create(user=share.created_by)
    client = Client()
    client.get(URL, {"route_share": share.token})
    features = client.get(reverse("routes:geojson")).json()["features"]
    assert [f["properties"].get("token") for f in features] == [share.token]
    assert all("uuid" not in f["properties"] for f in features)


def test_a_prefetch_of_a_route_share_writes_nothing_to_the_session() -> None:
    """As the share follow: only a real navigation seats the token."""
    share = _route_share_by_a_3d_reader()
    client = Client()
    response = client.get(
        URL, {"route_share": share.token}, HTTP_SEC_PURPOSE="prefetch"
    )
    assert response.status_code == 200
    assert PENDING_SESSION_KEY not in client.session


@pytest.mark.parametrize(
    "dead",
    ["no_3d_sharer", "expired", "example", "unknown"],
)
def test_a_route_share_that_may_not_open_3d_is_a_404(dead: str) -> None:
    """Sharer outside 3-D, expired, the example route, or no such token."""
    if dead == "no_3d_sharer":
        token = RouteShareFactory.create().token
    elif dead == "expired":
        token = _route_share_by_a_3d_reader(
            expires_at=timezone.now() - timedelta(days=1)
        ).token
    elif dead == "example":
        token = _route_share_by_a_3d_reader(is_example=True).token
    else:
        token = "nope"
    assert Client().get(URL, {"route_share": token}).status_code == 404


def test_deleting_the_sharers_3d_share_stops_their_route_links() -> None:
    """The Map3dShare row is the one switch for everything a reader shares."""
    share = _route_share_by_a_3d_reader()
    Map3dShare.objects.filter(created_by=share.created_by).delete()
    assert Client().get(URL, {"route_share": share.token}).status_code == 404


def test_a_flagged_reader_can_share_their_routes() -> None:
    """The share-create template rides on #map-3d, with its uuid placeholder."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    template = reverse("routes:share_create", args=[uuid.UUID(int=0)]).replace(
        str(uuid.UUID(int=0)), "__UUID__"
    )
    assert f'data-route-share-url-template="{template}"' in content
    assert 'data-csrf-token=""' not in content
    assert "Share this route in 3D" in content


def test_route_share_for_3d_ignores_a_blank_token() -> None:
    """No query for a page opened without ?route_share=."""
    assert route_share_for_3d("") is None


# ---------------------------------------------------------------------------
# /map/'s 3D roundel for a friend who followed a route share
# ---------------------------------------------------------------------------


def _map_page(client: Client) -> str:
    """Return /map/'s HTML.

    Args:
        client: The client to ask with.

    Returns:
        The page.

    """
    return client.get(reverse("public:map")).content.decode()


def test_a_friend_sent_a_3d_readers_route_gets_the_roundel() -> None:
    """Outside the flag, the roundel carries the followed share."""
    share = _route_share_by_a_3d_reader()
    client = Client()
    client.get(reverse("routes:share_redirect", args=[share.token]))
    content = _map_page(client)
    assert 'id="map-3d-link"' in content
    assert f'href="/map/3d/?route_share={share.token}"' in content
    assert f'data-route-share="{share.token}"' in content
    assert "js/map_3d_link.js" in content
    # SNOW-1449: the 3-D view on /map/ is a flagged reader's, never theirs.
    assert 'id="map-view-toggle"' not in content
    assert "js/map_view_3d.js" not in content


def test_the_newest_eligible_share_is_the_one_carried() -> None:
    """Followed in order: a 3-D one, a plain one, a second 3-D one."""
    first = _route_share_by_a_3d_reader()
    plain = RouteShareFactory.create()
    newest = _route_share_by_a_3d_reader()
    client = Client()
    for share in (first, newest, plain):
        client.get(reverse("routes:share_redirect", args=[share.token]))
    assert f'data-route-share="{newest.token}"' in _map_page(client)


def test_a_share_by_someone_outside_3d_brings_no_roundel() -> None:
    """A plain route share leaves /map/ as it was."""
    share = RouteShareFactory.create()
    client = Client()
    client.get(reverse("routes:share_redirect", args=[share.token]))
    assert 'id="map-3d-link"' not in _map_page(client)


def test_a_flagged_readers_roundel_carries_no_route_share() -> None:
    """Their 3-D map draws their followed shares anyway."""
    share = _route_share_by_a_3d_reader()
    client = _signed_in()
    client.get(reverse("routes:share_redirect", args=[share.token]))
    with override_flag("map_3d", active=True):
        content = _map_page(client)
    assert 'href="/map/3d/"' in content
    assert 'data-route-share=""' in content


def test_seating_a_route_share_spends_the_follow_budget() -> None:
    """Past the share follows' budget, the page answers 429 and seats nothing."""
    share = _route_share_by_a_3d_reader()
    client = Client()
    with patch("apps.map.map_3d.is_ratelimited", return_value=True) as limited:
        response = client.get(URL, {"route_share": share.token})
    assert response.status_code == 429
    assert PENDING_SESSION_KEY not in client.session
    assert limited.call_args.kwargs["group"] == SHARE_FOLLOW_GROUP
    assert limited.call_args.kwargs["increment"] is True


def test_the_page_and_the_link_share_one_bucket() -> None:
    """The bucket is the share follows' own: token then client IP."""
    share = _route_share_by_a_3d_reader()
    with patch("apps.map.map_3d.is_ratelimited", return_value=False) as limited:
        Client().get(URL, {"route_share": share.token}, REMOTE_ADDR="203.0.113.9")
    request = limited.call_args.args[0]
    key = limited.call_args.kwargs["key"]
    assert key(SHARE_FOLLOW_GROUP, request) == f"{share.token}|203.0.113.9"


def test_the_probe_redirects_a_flagged_reader_to_the_map() -> None:
    """SNOW-1456: /map/3d/?probe=1 goes to /map/?probe=1&view=3d.

    The probe moved onto /map/ as the 3D test; the URL Hugo kept goes on
    working until SNOW-1450 retires the page.
    """
    with override_flag("map_3d", active=True):
        response = _signed_in().get(URL, {"probe": "1"})
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('public:map')}?probe=1&view=3d"


def test_the_probe_url_keeps_the_flag_gate() -> None:
    """Outside the flag ?probe=1 is the same 404 as the page."""
    with override_flag("map_3d", active=False):
        response = _signed_in().get(URL, {"probe": "1"})
    assert response.status_code == 404


def test_the_page_no_longer_carries_the_probe_strings() -> None:
    """The probe's words left with it."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert "probe_" not in content
