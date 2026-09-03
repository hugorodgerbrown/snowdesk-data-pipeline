"""
tests/public/test_sitemap.py — Tests for the /sitemap.xml endpoint.

Covers:
  - 200 OK + Content-Type is application/xml.
  - Regions with a bulletin for today (Europe/Zurich) appear in the output.
  - Regions from multiple countries appear.
  - Regions whose bulletin date is not today are excluded.
  - Regions with no bulletin at all are excluded.
  - Empty-but-valid XML when no bulletin is published for today.
  - Sitemap entries are evergreen form-2 URLs, not dated form-3 URLs
    (SNOW-395).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.public.sitemaps import StaticViewSitemap
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


@pytest.mark.django_db
class TestSitemapResponse:
    """The /sitemap.xml endpoint returns valid XML with a 200 status."""

    def test_returns_200_ok(self, client: Client) -> None:
        """Bare request with no bulletins returns 200."""
        url = reverse("sitemap")
        response = client.get(url)
        assert response.status_code == 200

    def test_content_type_is_xml(self, client: Client) -> None:
        """Content-Type includes application/xml."""
        url = reverse("sitemap")
        response = client.get(url)
        assert "xml" in response["Content-Type"]

    def test_empty_sitemap_is_valid_xml(self, client: Client) -> None:
        """When no bulletins are published today the response is still well-formed XML."""
        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
        assert "<urlset" in content


@pytest.mark.django_db
class TestSitemapContents:
    """Sitemap includes only regions with today's bulletins."""

    def test_today_bulletin_region_is_listed(self, client: Client) -> None:
        """A region with a bulletin for today appears in the sitemap."""
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        today = _zurich_today()
        _make_bulletin_for_region(region, today)

        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
        assert "ch-4115" in content

    def test_yesterday_bulletin_region_is_excluded(self, client: Client) -> None:
        """A region whose bulletin is from yesterday does not appear."""
        region = MicroRegionFactory.create(
            region_id="CH-4200", name="Graubunden", slug="ch-4200"
        )
        yesterday = _zurich_today() - timedelta(days=1)
        _make_bulletin_for_region(region, yesterday)

        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
        # Only today's bulletins qualify — yesterday's must not appear.
        assert "ch-4200" not in content

    def test_region_with_no_bulletin_excluded(self, client: Client) -> None:
        """A region with no bulletin at all does not appear in the sitemap."""
        MicroRegionFactory.create(region_id="AT-9999", name="No Data", slug="at-9999")

        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
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

        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
        assert "ch-1111" in content
        assert "at-1111" in content
        assert "it-1111" in content

    def test_today_region_in_sitemap_not_yesterday(self, client: Client) -> None:
        """Only the today region appears; the yesterday region is absent."""
        region_today = MicroRegionFactory.create(
            region_id="CH-2222", name="Today Region", slug="ch-2222"
        )
        region_yesterday = MicroRegionFactory.create(
            region_id="CH-3333", name="Yesterday Region", slug="ch-3333"
        )
        today = _zurich_today()
        yesterday = today - timedelta(days=1)
        _make_bulletin_for_region(region_today, today)
        _make_bulletin_for_region(region_yesterday, yesterday)

        url = reverse("sitemap")
        response = client.get(url)
        content = response.content.decode()
        assert "ch-2222" in content
        assert "ch-3333" not in content

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

        response = client.get(reverse("sitemap"))
        content = response.content.decode()

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
@override_settings(POSTHOG_API_KEY="phc_test")
def test_sitemap_path_is_not_posthog_exempt(client: Client) -> None:
    """SNOW-338: /sitemap.xml is deliberately NOT in _POSTHOG_EXEMPT_PATHS.

    Unlike robots.txt / llms.txt / manifest / favicon, the sitemap is not a
    valid target for the PostHog-exemption mechanism:

    * it sets no ``Cache-Control: public`` header, so it is not a
      shared-cacheable surface in the first place; and
    * its ``Vary: Cookie`` header is added by the sitemap-view middleware
      path, not by PosthogContextMiddleware — it persists even with
      POSTHOG_API_KEY unset.

    Exempting it from PostHog would therefore be dead config: it would
    neither remove ``Vary: Cookie`` nor make the response cacheable.  Making
    the sitemap a genuine public-cacheable surface is tracked in SNOW-340.

    This test guards the decision: the request filter must return True for
    /sitemap.xml when a key is set (i.e. the path is NOT short-circuited),
    and the response still carries Vary: Cookie with no public Cache-Control.
    """
    from config.settings.base import _posthog_request_filter

    class _FakeRequest:
        path = "/sitemap.xml"

    assert _posthog_request_filter(_FakeRequest()) is True, (
        "/sitemap.xml must NOT be exempt from PosthogContextMiddleware — "
        "exempting it is dead config (see SNOW-340)"
    )

    response = client.get(reverse("sitemap"))
    assert "public" not in response.get("Cache-Control", ""), (
        "sitemap currently sets no public Cache-Control; if this changes, "
        "revisit SNOW-340 and the exemption decision"
    )
    assert "Cookie" in response.get("Vary", ""), (
        "sitemap Vary: Cookie originates outside PostHog; if this changes, "
        "revisit SNOW-340"
    )


# ---------------------------------------------------------------------------
# SNOW-676 — the resort and static sections
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResortSitemap:
    """The resort section lists every resort's detail page."""

    def test_every_resort_appears_exactly_once(self, client: Client) -> None:
        """Each resort is listed at its canonical URL, with no duplicates."""
        resorts = [ResortFactory.create() for _ in range(3)]

        body = client.get(reverse("sitemap")).content.decode()

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

        body = client.get(reverse("sitemap")).content.decode()

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
        body = client.get(reverse("sitemap")).content.decode()

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
        ["/account/", "/favourites/", "/observations/", "/examples/", "/map/"],
    )
    def test_private_and_unstable_urls_are_absent(
        self, client: Client, excluded: str
    ) -> None:
        """Nothing per-user, sign-in gated, random or redirecting is listed.

        ``/account/`` and ``/favourites/`` are Disallowed in robots.txt, so
        listing them would contradict it. ``/observations/`` shows an
        anonymous visitor a sign-in CTA rather than the stream.
        ``/examples/`` serves a *random* bulletin per request, which would
        collide with the real bulletin pages it samples. ``/map/`` is a
        permanent redirect.
        """
        body = client.get(reverse("sitemap")).content.decode()

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

    body = client.get(reverse("sitemap")).content.decode()

    assert "<url>" in body
    assert reverse("public:home") in body
    assert reverse("public:how_to_read_bulletin") in body


# ---------------------------------------------------------------------------
# SNOW-799 — the locations section
# ---------------------------------------------------------------------------


def _locs(client: Client) -> list[str]:
    """Return every <loc> in the sitemap."""
    content = client.get(reverse("sitemap")).content.decode()
    return re.findall(r"<loc>([^<]+)</loc>", content)


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
        content = client.get(reverse("sitemap")).content.decode()
        assert row.fetched_at.date().isoformat() in content
