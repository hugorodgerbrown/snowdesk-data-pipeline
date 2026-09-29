"""
tests/public/test_homepage.py — Tests for the marketing homepage at / and
the map's move to /map/.

Covers:
  - A bare / renders the homepage: 200, the homepage template, a link to
    /map/, and no map JavaScript.
  - / with map query parameters 301s to /map/ with the query string intact.
  - / with only attribution parameters (utm_*, ref) still renders the
    homepage.
  - /map/ renders the interactive map.
  - /observations/ 301s to /map/?panel=reports.
  - The manifest's start_url is /map/ while its id stays /.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestHomepage:
    """The bare root renders the static homepage."""

    def test_bare_root_renders_the_homepage(self, client: Client) -> None:
        """GET / is a 200 rendered from public/home.html."""
        response = client.get("/")

        assert response.status_code == 200
        assert "public/home.html" in [t.name for t in response.templates]
        assert "public/map.html" not in [t.name for t in response.templates]

    def test_home_reverses_to_the_root(self) -> None:
        """``public:home`` is / and ``public:map`` is /map/."""
        assert reverse("public:home") == "/"
        assert reverse("public:map") == "/map/"

    def test_links_into_the_map(self, client: Client) -> None:
        """The primary call to action points at /map/."""
        content = client.get("/").content.decode()

        assert 'id="home-open-map"' in content
        assert 'href="/map/"' in content

    def test_loads_no_map_javascript(self, client: Client) -> None:
        """The map image is a still; neither MapLibre nor map.js is loaded."""
        content = client.get("/").content.decode()

        assert "maplibre" not in content.lower()
        assert "js/map.js" not in content
        assert 'id="map"' not in content

    def test_links_to_help_compare_and_the_build_blog(self, client: Client) -> None:
        """The homepage links out to help, compare and the build blog."""
        content = client.get("/").content.decode()

        assert f'href="{reverse("public:help")}"' in content
        assert f'href="{reverse("public:compare")}"' in content
        assert 'href="https://build.snowdesk.info/"' in content

    @pytest.mark.parametrize(
        "query", ["utm_source=newsletter&utm_medium=email", "ref=producthunt"]
    )
    def test_attribution_parameters_still_render_the_homepage(
        self, client: Client, query: str
    ) -> None:
        """A campaign link to / lands on the homepage, not the map."""
        response = client.get(f"/?{query}")

        assert response.status_code == 200
        assert "public/home.html" in [t.name for t in response.templates]


class TestLegacyMapLinksOnRoot:
    """Map URLs minted while the map lived at / keep working."""

    @pytest.mark.parametrize(
        "query",
        [
            "d=2026-02-16",
            "panel=reports",
            "route_share=abc123",
            "d=2026-02-16&panel=favourites",
            "utm_source=x&d=2026-02-16",
        ],
    )
    def test_map_query_redirects_permanently_to_map(
        self, client: Client, query: str
    ) -> None:
        """/?<map state> 301s to /map/?<same query>."""
        response = client.get(f"/?{query}")

        assert response.status_code == 301
        assert response["Location"] == f"/map/?{query}"

    def test_fragment_links_are_forwarded_client_side(self, client: Client) -> None:
        """/#CH-4115 never reaches the server, so the page forwards it.

        The bulletin back-link was ``/#CH-4115`` before the map moved; the
        homepage carries a nonce'd head script that replaces the location
        with ``/map/`` plus the search and fragment when a fragment is set.
        """
        content = client.get("/").content.decode()

        assert "location.hash.length > 1" in content
        assert 'location.replace("/map/" + location.search + location.hash)' in content
        assert 'addEventListener("hashchange", toMap)' in content

    def test_redirect_preserves_the_query_string_verbatim(self, client: Client) -> None:
        """Encoded values survive the redirect unchanged."""
        response = client.get("/?d=x&loc=46.1%2C7.2")

        assert response["Location"] == "/map/?d=x&loc=46.1%2C7.2"


class TestMapPage:
    """The interactive map is served at /map/."""

    def test_map_renders_at_map_path(self, client: Client) -> None:
        """GET /map/ is a 200 rendered from public/map.html with #map."""
        response = client.get("/map/")

        assert response.status_code == 200
        assert "public/map.html" in [t.name for t in response.templates]
        assert b'id="map"' in response.content

    def test_map_with_query_renders_in_place(self, client: Client) -> None:
        """/map/?d= renders the map — it does not redirect."""
        response = client.get("/map/?d=2026-02-16")

        assert response.status_code == 200


class TestObservationsRedirect:
    """/observations/ (SNOW-804) lands on the map's reports sheet."""

    def test_observations_redirects_to_map_reports_panel(self, client: Client) -> None:
        """/observations/ 301s straight to /map/?panel=reports."""
        response = client.get("/observations/")

        assert response.status_code == 301
        assert response["Location"] == "/map/?panel=reports"


class TestManifest:
    """The installed app opens on the map."""

    def test_start_url_is_the_map_and_id_is_unchanged(self, client: Client) -> None:
        """start_url moves to /map/; id stays / so existing installs match."""
        manifest = json.loads(client.get("/manifest.webmanifest").content)

        assert manifest["start_url"].endswith("/map/")
        assert manifest["id"].endswith("/")
        assert not manifest["id"].endswith("/map/")
        assert manifest["scope"].endswith("/")
