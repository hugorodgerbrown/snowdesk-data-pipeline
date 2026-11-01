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
  - The daily read (SNOW-1357): under the hero on a rated day, linking the
    biggest swing to its dated bulletin; absent off season; cached; never
    built for a map-state 301.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.regions.models import MicroRegion
from tests.factories import MicroRegionFactory, RegionDayRatingFactory

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

    def test_has_no_help_compare_or_blog_panels(self, client: Client) -> None:
        """The Help, Compare apps and build-blog panels are off until reworked."""
        content = client.get("/").content.decode()

        assert 'data-testid="home-links"' not in content
        assert f'href="{reverse("public:compare")}"' not in content
        assert 'href="https://build.snowdesk.info/"' not in content

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


class TestDailyRead:
    """SNOW-1357: "Across the Alps today" under the hero."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        """Each test builds its own read."""
        cache.clear()

    def _swing_day(self) -> MicroRegion:
        """Rate one region up a level since yesterday; return it."""
        today = timezone.localdate()
        place = MicroRegionFactory.create(
            region_id="CH-4115", name="Martigny – Verbier"
        )
        RegionDayRatingFactory.create(
            region=place,
            date=today - datetime.timedelta(days=1),
            max_rating=RegionDayRating.Rating.MODERATE,
        )
        RegionDayRatingFactory.create(
            region=place, date=today, max_rating=RegionDayRating.Rating.HIGH
        )
        return place

    def test_renders_under_the_hero_with_the_swing_linked(self, client: Client) -> None:
        """The read follows the hero and links the swing to today's bulletin."""
        place = self._swing_day()

        content = client.get("/").content.decode()

        assert 'data-testid="home-daily-read"' in content
        assert content.index('data-testid="home-hero"') < content.index(
            'data-testid="home-daily-read"'
        )
        assert content.index('data-testid="home-daily-read"') < content.index(
            'data-testid="home-features"'
        )
        assert f'href="{place.get_absolute_url(timezone.localdate())}"' in content
        assert "Moderate → High" in content

    def test_absent_off_season(self, client: Client) -> None:
        """No rated region today: the page reads as it always has."""
        RegionDayRatingFactory.create(
            date=timezone.localdate(), max_rating=RegionDayRating.Rating.NO_RATING
        )

        content = client.get("/").content.decode()

        assert 'data-testid="home-daily-read"' not in content

    def test_a_second_request_reads_the_cache(self, client: Client) -> None:
        """Inside the cache window the homepage makes no query for the read.

        The page's other queries (the site banners) are not the read's, so
        the check is on the tables the read reads.
        """
        self._swing_day()
        client.get("/")

        with CaptureQueriesContext(connection) as queries:
            client.get("/")

        sql = " ".join(q["sql"] for q in queries.captured_queries)
        assert "regiondayrating" not in sql
        assert "bulletins_bulletin" not in sql

    def test_a_map_state_redirect_builds_no_read(self, client: Client) -> None:
        """/?d=… 301s before the read is built, so nothing is cached."""
        self._swing_day()

        response = client.get("/?d=2026-01-14")

        assert response.status_code == 301
        assert cache.get(f"daily-read:{timezone.localdate().isoformat()}") is None


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
        assert 'addEventListener("hashchange"' in content

    def test_installed_app_launches_are_forwarded(self, client: Client) -> None:
        """An app installed before the move launches /; it must open the map.

        The manifest's ``start_url`` is /map/ now, but an existing install
        keeps launching / until the browser refreshes its metadata, and an
        iOS home-screen icon may never. The head script forwards when the
        page runs standalone.
        """
        content = client.get("/").content.decode()

        assert 'matchMedia("(display-mode: standalone)")' in content
        assert "navigator.standalone === true" in content

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


class TestAttributionListsMatchTheWorker:
    """views.home and static/js/sw.js apply the same attribution rule."""

    def test_worker_lists_equal_the_view_lists(self) -> None:
        """The worker answers the legacy redirect offline; the rule must match.

        A parameter treated as attribution on one side and map state on the
        other would send the same link to the homepage online and to the map
        offline.
        """
        from apps.public import views

        source = (Path(__file__).resolve().parents[2] / "static/js/sw.js").read_text()
        prefixes = re.search(r"const ATTRIBUTION_PARAM_PREFIXES = \[([^\]]*)\]", source)
        params = re.search(
            r"const ATTRIBUTION_PARAMS = new Set\(\[([^\]]*)\]\)", source
        )
        assert prefixes and params

        def names(group: str) -> set[str]:
            return set(re.findall(r"'([^']+)'", group))

        assert names(prefixes.group(1)) == set(views._ATTRIBUTION_PARAM_PREFIXES)
        assert names(params.group(1)) == set(views._ATTRIBUTION_PARAMS)
