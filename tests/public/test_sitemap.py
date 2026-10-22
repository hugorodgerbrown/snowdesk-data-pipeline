"""
tests/public/test_sitemap.py — Tests for the /sitemap.xml endpoint.

Covers:
  - 200 OK + Content-Type is application/xml.
  - Every region that has ever had a bulletin appears, whatever its date
    (SNOW-1121 — the section used to list today's regions only).
  - Regions from multiple countries appear.
  - Regions with no bulletin at all are excluded.
  - A region with several bulletins appears once, lastmod the newest.
  - The bulletin section's query count does not grow with the regions.
  - Empty-but-valid XML when no bulletin exists at all.
  - Sitemap entries are evergreen form-2 URLs, not dated form-3 URLs
    (SNOW-395).
  - /sitemap.xml is an index of three files — main, regions, resorts — each
    cached and served public-cacheable for its own lifetime, without
    ``Vary: Cookie``; the section queries select no boundary polygon
    (SNOW-1178).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.public.sitemaps import (
    SITEMAP_FILES,
    BulletinSitemap,
    LocationWeatherSitemap,
    ResortSitemap,
    StaticViewSitemap,
)
from apps.regions.models import MicroRegion, Resort
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

_ZURICH_TZ = ZoneInfo("Europe/Zurich")


@pytest.fixture(autouse=True)
def _clear_sitemap_cache() -> None:
    """Start every test with no cached sitemap (SNOW-1178)."""
    cache.clear()


def _zurich_today() -> date:
    """Return the current local date in Europe/Zurich, matching production sitemaps.py."""
    return timezone.localdate(timezone=_ZURICH_TZ)


def _make_bulletin_for_region(region: MicroRegion, day: date) -> None:
    """Create a morning bulletin valid on *day* and link it to *region*."""
    valid_from = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    valid_to = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        valid_from=valid_from,
        valid_to=valid_to,
        issued_at=valid_from - timedelta(minutes=30),
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)


def _file_url(name: str) -> str:
    """Return the path of one sitemap file, e.g. ``/sitemap-regions.xml``."""
    return reverse("sitemap_file", kwargs={"name": name})


def _sitemap(client: Client) -> str:
    """Return every sitemap file's body, concatenated, as a crawler reads them."""
    return "".join(
        client.get(_file_url(name)).content.decode() for name in SITEMAP_FILES
    )


@pytest.mark.django_db
class TestSitemapResponse:
    """/sitemap.xml is an index of three sitemap files (SNOW-1178)."""

    def test_index_lists_the_three_files(self, client: Client) -> None:
        """The index names main, regions and resorts on SITE_BASE_URL."""
        with override_settings(SITE_BASE_URL="https://snowdesk.info"):
            response = client.get(reverse("sitemap"))

        assert response.status_code == 200
        assert "xml" in response["Content-Type"]
        assert re.findall(r"<loc>([^<]+)</loc>", response.content.decode()) == [
            "https://snowdesk.info/sitemap-main.xml",
            "https://snowdesk.info/sitemap-regions.xml",
            "https://snowdesk.info/sitemap-resorts.xml",
        ]

    @pytest.mark.parametrize("name", list(SITEMAP_FILES))
    def test_each_file_is_a_valid_urlset_when_empty(
        self, client: Client, name: str
    ) -> None:
        """With no data at all every file is still a well-formed <urlset>."""
        response = client.get(_file_url(name))

        assert response.status_code == 200
        assert "xml" in response["Content-Type"]
        assert "<urlset" in response.content.decode()

    def test_an_unknown_file_is_a_404(self, client: Client) -> None:
        """Only the files in SITEMAP_FILES exist."""
        assert client.get(_file_url("bulletins")).status_code == 404

    def test_sections_are_split_by_file(self, client: Client) -> None:
        """Regions, resorts and the rest each land in their own file."""
        region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
        _make_bulletin_for_region(region, _zurich_today())
        resort = ResortFactory.create(region=region)

        def body(name: str) -> str:
            return client.get(_file_url(name)).content.decode()

        assert region.get_absolute_url() in body("regions")
        assert resort.get_absolute_url() in body("resorts")
        assert reverse("public:home") in body("main")
        assert region.get_absolute_url() not in body("main") + body("resorts")


@pytest.mark.django_db
class TestSitemapContents:
    """The bulletin section lists every region that has ever had a bulletin."""

    def test_today_bulletin_region_is_listed(self, client: Client) -> None:
        """A region with a bulletin for today appears in the sitemap."""
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        today = _zurich_today()
        _make_bulletin_for_region(region, today)

        content = _sitemap(client)
        assert "ch-4115" in content

    def test_out_of_season_region_is_listed_with_its_lastmod(
        self, client: Client
    ) -> None:
        """SNOW-1121: a region whose only bulletin ended months ago is listed.

        The evergreen page renders all year, so the section must not empty
        out between May and November — those are the weeks a crawler has
        time to index it before the season.
        """
        region = MicroRegionFactory.create(
            region_id="CH-4200", name="Graubunden", slug="ch-4200"
        )
        _make_bulletin_for_region(region, _zurich_today() - timedelta(days=150))
        bulletin = region.bulletins.get()

        content = _sitemap(client)

        entry = re.search(
            r"/ch-4200/graubunden/</loc>\s*<lastmod>([^<]+)</lastmod>", content
        )
        assert entry, "expected the out-of-season region's evergreen entry"
        assert entry.group(1) == bulletin.updated_at.date().isoformat()

    def test_out_of_season_evergreen_page_renders(self, client: Client) -> None:
        """The URL the sitemap lists for an out-of-season region is a 200."""
        region = MicroRegionFactory.create(
            region_id="CH-4201", name="Davos", slug="ch-4201"
        )
        _make_bulletin_for_region(region, _zurich_today() - timedelta(days=150))

        response = client.get(region.get_absolute_url())

        assert response.status_code == 200

    def test_region_with_several_bulletins_is_listed_once_at_the_newest(
        self, client: Client
    ) -> None:
        """One <url> per region, its lastmod the newest bulletin's updated_at."""
        region = MicroRegionFactory.create(
            region_id="CH-5000", name="Many", slug="ch-5000"
        )
        today = _zurich_today()
        for days_ago in (300, 200, 1):
            _make_bulletin_for_region(region, today - timedelta(days=days_ago))
        newest = max(b.updated_at for b in region.bulletins.all())

        content = _sitemap(client)

        assert content.count("/ch-5000/many/</loc>") == 1
        lastmods = re.findall(
            r"/ch-5000/many/</loc>\s*<lastmod>([^<]+)</lastmod>", content
        )
        assert lastmods == [newest.date().isoformat()]

    def test_bulletin_section_query_count_is_flat(
        self, django_assert_num_queries: Any
    ) -> None:
        """Listing the section costs the same queries for 1 region as for 5."""
        today = _zurich_today()

        def _render() -> None:
            sitemap = BulletinSitemap()
            for item in sitemap.items():
                sitemap.location(item)
                sitemap.lastmod(item)

        _make_bulletin_for_region(
            MicroRegionFactory.create(region_id="CH-6000", slug="ch-6000"), today
        )
        with django_assert_num_queries(1):
            _render()

        for index in range(1, 5):
            _make_bulletin_for_region(
                MicroRegionFactory.create(
                    region_id=f"CH-600{index}", slug=f"ch-600{index}"
                ),
                today - timedelta(days=index * 40),
            )
        with django_assert_num_queries(1):
            _render()

    def test_region_with_no_bulletin_excluded(self, client: Client) -> None:
        """A region with no bulletin at all does not appear in the sitemap."""
        MicroRegionFactory.create(region_id="AT-9999", name="No Data", slug="at-9999")

        content = _sitemap(client)
        assert "at-9999" not in content

    def test_multiple_countries_appear(self, client: Client) -> None:
        """Regions from multiple countries are all included when they have today's bulletins."""
        region_ch = MicroRegionFactory.create(
            region_id="CH-1111", name="Swiss Region", slug="ch-1111"
        )
        region_at = MicroRegionFactory.create(
            region_id="AT-1111", name="Austrian Region", slug="at-1111"
        )
        region_it = MicroRegionFactory.create(
            region_id="IT-1111", name="Italian Region", slug="it-1111"
        )
        today = _zurich_today()
        for region in (region_ch, region_at, region_it):
            _make_bulletin_for_region(region, today)

        content = _sitemap(client)
        assert "ch-1111" in content
        assert "at-1111" in content
        assert "it-1111" in content

    def test_urls_are_evergreen_not_dated(self, client: Client) -> None:
        """SNOW-395: every <loc> is form 2 (/<region>/<slug>/), not form 3.

        A dated URL is stale by tomorrow — LLMs and search engines cite
        whatever the sitemap advertises, so the evergreen form keeps
        "current conditions" queries pointing at a live page.
        """
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        _make_bulletin_for_region(region, _zurich_today())

        content = _sitemap(client)

        locs = re.findall(r"<loc>([^<]+)</loc>", content)
        assert locs, "expected at least one <loc> entry"
        date_segment = re.compile(r"/\d{4}-\d{2}-\d{2}/?$")
        for loc in locs:
            assert not date_segment.search(loc), (
                f"sitemap URL still carries a date segment: {loc!r}"
            )
        # The bulletin section carries the evergreen form. Asserted of that
        # entry rather than of every <loc>: since SNOW-676 the sitemap also
        # holds the resort and static sections, whose URLs are neither
        # dated nor region-shaped.
        assert any(loc.endswith("/ch-4115/valais/") for loc in locs), (
            "expected the region's evergreen form-2 URL in the sitemap"
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "max_age"),
    [(None, 86400), ("main", 21600), ("regions", 3600), ("resorts", 86400)],
)
def test_sitemaps_are_public_cacheable_surfaces(
    client: Client, name: str | None, max_age: int
) -> None:
    """SNOW-1178 (closing SNOW-340): every sitemap is shared-cacheable.

    The bodies are rendered without the request and no middleware reads
    ``request.user``, so no ``Vary: Cookie`` splits the edge cache per
    visitor. Each file has its own lifetime.
    """
    path = reverse("sitemap") if name is None else _file_url(name)

    response = client.get(path)
    assert response["Cache-Control"] == f"public, max-age={max_age}"
    assert "Cookie" not in response.get("Vary", "")
    assert response["X-Robots-Tag"] == "noindex, noodp, noarchive"


@pytest.mark.django_db
class TestSitemapCache:
    """The rendered sitemap is cached; a hit never reaches the sections (SNOW-1178)."""

    def test_a_cache_hit_runs_no_query(
        self, client: Client, django_assert_num_queries: Any
    ) -> None:
        """The second read of every file is served from the cache, byte for byte."""
        _make_bulletin_for_region(MicroRegionFactory.create(), _zurich_today())
        ResortFactory.create()
        first = _sitemap(client)

        with django_assert_num_queries(0):
            second = _sitemap(client)

        assert second == first

    def test_a_new_region_appears_once_the_cache_expires(self, client: Client) -> None:
        """Until the entry is dropped the cached body stands; then it rebuilds."""
        _sitemap(client)
        region = MicroRegionFactory.create(region_id="CH-7001", slug="ch-7001")
        _make_bulletin_for_region(region, _zurich_today())
        path = region.get_absolute_url()

        assert path not in _sitemap(client)
        cache.clear()
        assert path in _sitemap(client)

    def test_the_edge_gets_only_the_remaining_lifetime(self, client: Client) -> None:
        """A body cached 50 minutes ago is offered to the edge for 10 more."""
        url = _file_url("regions")
        with freeze_time("2026-10-22 08:00:00"):
            client.get(url)
        with freeze_time("2026-10-22 08:50:00"):
            response = client.get(url)

        assert response["Cache-Control"] == "public, max-age=600"

    def test_the_cache_is_keyed_on_the_origin(self, client: Client) -> None:
        """A body rendered for one SITE_BASE_URL is never served for another."""
        with override_settings(SITE_BASE_URL="https://snowdesk.info"):
            _sitemap(client)
        with override_settings(SITE_BASE_URL="https://staging.snowdesk.info"):
            locs = _locs(client)

        assert all(loc.startswith("https://staging.snowdesk.info/") for loc in locs)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("section", "query_count"), [(BulletinSitemap, 1), (ResortSitemap, 2)]
)
def test_section_query_reads_no_boundary_polygon(
    section: Any, query_count: int
) -> None:
    """SNOW-1178: the region polygons are what made the bulletin query take 1.8 s."""
    region = MicroRegionFactory.create()
    _make_bulletin_for_region(region, _zurich_today())
    ResortFactory.create(region=region)

    with CaptureQueriesContext(connection) as queries:
        urls = section().get_urls()

    # The bulletin section is a list, so the paginator counts it with len();
    # the resort section's COUNT(*) is a cheap scan of one table.
    assert urls
    assert len(queries) == query_count
    assert not any("boundary" in query["sql"] for query in queries)


# ---------------------------------------------------------------------------
# SNOW-676 — the resort and static sections
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResortSitemap:
    """The resort section lists every resort's detail page."""

    def test_every_resort_appears_exactly_once(self, client: Client) -> None:
        """Each resort is listed at its canonical URL, with no duplicates."""
        resorts = [ResortFactory.create() for _ in range(3)]

        body = _sitemap(client)

        for resort in resorts:
            url = resort.get_absolute_url()
            assert body.count(f"{url}</loc>") == 1, f"{url} should appear exactly once"

    def test_touring_terrain_is_listed_too(self, client: Client) -> None:
        """Lift-less touring terrain has a real page, so it belongs here.

        ``resort_detail`` does not filter by kind. Omitting touring terrain
        would hide precisely the pages least likely to be found any other
        way.
        """
        touring = ResortFactory.create(kind=Resort.Kind.TOURING_TERRAIN)

        body = _sitemap(client)

        assert touring.get_absolute_url() in body

    def test_listed_resort_urls_resolve(self, client: Client) -> None:
        """A sitemap entry that 404s is worse than an absent one."""
        resort = ResortFactory.create()

        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200


@pytest.mark.django_db
class TestStaticViewSitemap:
    """The static section lists the pages that aren't generated from data."""

    def test_every_static_route_is_listed(self, client: Client) -> None:
        """Each route in the registry appears in the sitemap."""
        body = _sitemap(client)

        for route in StaticViewSitemap.ROUTES:
            assert f"{reverse(route)}</loc>" in body, (
                f"{route} missing from the sitemap"
            )

    def test_every_static_route_returns_200(self, client: Client) -> None:
        """The listed pages actually render.

        This is the test that catches a route being renamed or gated out
        from under the list — a sitemap full of 404s is a worse signal to a
        crawler than a short one.
        """
        for route in StaticViewSitemap.ROUTES:
            response = client.get(reverse(route))
            assert response.status_code == 200, (
                f"{route} returned {response.status_code}"
            )

    @pytest.mark.parametrize(
        "excluded",
        ["/account/", "/favourites/", "/observations/", "/examples/"],
    )
    def test_private_and_unstable_urls_are_absent(
        self, client: Client, excluded: str
    ) -> None:
        """Nothing per-user, sign-in gated, random or redirecting is listed.

        ``/account/`` and ``/favourites/`` are Disallowed in robots.txt, so
        listing them would contradict it. ``/observations/`` shows an
        anonymous visitor a sign-in CTA rather than the stream.
        ``/examples/`` serves a *random* bulletin per request, which would
        collide with the real bulletin pages it samples.
        """
        body = _sitemap(client)

        assert excluded not in body


@pytest.mark.django_db
def test_sitemap_is_not_empty_out_of_season(client: Client) -> None:
    """With no bulletin for today, the sitemap still advertises the site.

    This is the point of SNOW-676. The Alps publish no bulletins from
    roughly May to November, and until this change the whole sitemap was
    empty for those months — the exact window in which slow-moving
    reference pages have time to be indexed.
    """
    ResortFactory.create()

    body = _sitemap(client)

    assert "<url>" in body
    assert reverse("public:home") in body
    assert reverse("public:map") in body
    assert reverse("public:how_to_read_bulletin") in body


# ---------------------------------------------------------------------------
# SNOW-799 — the locations section
# ---------------------------------------------------------------------------


def _locs(client: Client) -> list[str]:
    """Return every <loc> across the sitemap files."""
    return re.findall(r"<loc>([^<]+)</loc>", _sitemap(client))


@pytest.mark.django_db
class TestLocationWeatherSitemap:
    """The locations section lists every NAMED public location's weather page."""

    def test_named_public_location_is_listed_undated(self, client: Client) -> None:
        """A resort's peak is listed at /weather/<short_id>/ — no ?date=."""
        peak = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=peak)

        locs = _locs(client)

        matches = [loc for loc in locs if loc.endswith(peak.get_absolute_url())]
        assert len(matches) == 1
        assert "?date=" not in matches[0]
        # The segment is the opaque short id — the pk is nowhere in the URL.
        assert matches[0].rsplit("/weather/", 1)[1] == f"{peak.short_id}/"

    def test_anonymous_centroid_is_excluded(self, client: Client) -> None:
        """A region centroid has no name of its own and is not listed."""
        region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
        centroid = LocationFactory.create(anonymous=True)
        region.centroid_location = centroid
        region.save(update_fields=["centroid_location"])

        assert not any("/weather/" in loc for loc in _locs(client))

    def test_location_without_a_short_id_is_excluded(self, client: Client) -> None:
        """A location with no short id has no address, so it is not listed.

        SNOW-810: ``short_id`` is nullable between the SNOW-797 migration
        and ``backfill_location_short_ids``, and this section reached
        ``get_absolute_url()`` for every row. Reversing on ``None`` raised
        ``NoReverseMatch``, which took the WHOLE sitemap down with a 500 —
        every bulletin, resort and static entry with it. The unaddressable
        row drops out; the rest of the sitemap is unaffected.
        """
        listed = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=listed)
        unaddressable = LocationFactory.create(name="Bec des Rosses", short_id=None)
        ResortLocationFactory.create(location=unaddressable)

        locs = _locs(client)
        assert any(loc.endswith(listed.get_absolute_url()) for loc in locs)
        # The excluded row has no id to search the rendered output for, so
        # the exclusion is asserted on the section itself. Django's
        # ``Sitemap`` interpolates ``location()`` into "protocol://domain"
        # with no check, so an unfiltered row would not be dropped from the
        # XML — it would appear as a bare site-root entry, which is
        # indistinguishable from the homepage the static section lists.
        assert unaddressable.get_absolute_url() == ""
        items = list(LocationWeatherSitemap().items())
        assert listed in items
        assert unaddressable not in items

    def test_private_pin_is_excluded(self, client: Client) -> None:
        """A favourite's location is outside public() and never listed."""
        favourite = FavouriteFactory.create(name="Secret col")
        assert favourite.location is not None
        # Named, so only the public() ceiling keeps it out.
        favourite.location.name = "Secret col"
        favourite.location.save(update_fields=["name"])

        assert not any("/weather/" in loc for loc in _locs(client))

    def test_listed_url_resolves_and_lastmod_is_the_weather_fetch(
        self, client: Client
    ) -> None:
        """The listed page is a 200, and lastmod follows the newest fetch."""
        peak = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=peak)
        row = WeatherFactory.create(location=peak, observed_on=_zurich_today())

        assert client.get(peak.get_absolute_url()).status_code == 200
        content = _sitemap(client)
        assert row.fetched_at.date().isoformat() in content


# ---------------------------------------------------------------------------
# SNOW-1175 — the origin comes from SITE_BASE_URL, not the django_site row
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSitemapOrigin:
    """Every <loc> is on SITE_BASE_URL's origin, whatever the Site row says."""

    def test_locs_ignore_the_example_com_site_row(self, client: Client) -> None:
        """The untouched ``example.com`` Site row never reaches the sitemap."""
        from django.contrib.sites.models import Site

        assert Site.objects.get_current().domain == "example.com"
        _make_bulletin_for_region(MicroRegionFactory.create(), _zurich_today())
        ResortFactory.create()

        with override_settings(SITE_BASE_URL="https://snowdesk.info"):
            locs = _locs(client)

        assert locs
        assert {urlsplit(loc)[:2] for loc in locs} == {("https", "snowdesk.info")}

    def test_scheme_comes_from_site_base_url_not_the_request(
        self, client: Client
    ) -> None:
        """A plain-HTTP hop (Render's proxy) does not downgrade the scheme."""
        with override_settings(SITE_BASE_URL="https://staging.snowdesk.info"):
            locs = _locs(client)

        assert locs
        assert all(loc.startswith("https://staging.snowdesk.info/") for loc in locs)
