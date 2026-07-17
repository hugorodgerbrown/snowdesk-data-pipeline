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

from regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
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
            assert loc.endswith("/ch-4115/valais/")


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
