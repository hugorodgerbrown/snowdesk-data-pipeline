"""
tests/map/test_map_3d.py — the 3-D terrain map at /map/3d/ (SNOW-1438).

Pins the gate (a 404 for anyone outside the map_3d flag, and no waffle query
for an anonymous visitor), the URLs the script reads from #map-3d, the
Steep terrain switch following TERRAIN_CLASS_TILE_URL, and the page never
unfurling or being indexed.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from waffle.testutils import override_flag

from apps.map.map_3d import MAP_3D_DEM_TILE_URL
from apps.map.models import Map3dShare
from tests.factories import Map3dShareFactory, UserFactory

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


def test_the_page_carries_every_url_the_script_reads() -> None:
    """The style, DEM, tours, routes and resorts URLs ride on #map-3d."""
    with override_flag("map_3d", active=True):
        content = _signed_in().get(URL).content.decode()
    assert "ch.swisstopo.basemap-winter.vt/style.json" in content
    assert f'data-dem-tile-url="{MAP_3D_DEM_TILE_URL}"' in content
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
