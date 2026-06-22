"""
tests/public/test_sitemap.py — Tests for the /sitemap.xml endpoint.

Covers:
  - 200 OK + Content-Type is application/xml.
  - Regions with a bulletin for today (Europe/Zurich) appear in the output.
  - Regions from multiple countries appear.
  - Regions whose bulletin date is not today are excluded.
  - Regions with no bulletin at all are excluded.
  - Empty-but-valid XML when no bulletin is published for today.
"""

from __future__ import annotations

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


@override_settings(POSTHOG_API_KEY="phc_test")
def test_sitemap_path_is_posthog_exempt() -> None:
    """SNOW-338 regression: /sitemap.xml is exempt from PosthogContextMiddleware.

    When POSTHOG_API_KEY is non-empty, PosthogContextMiddleware would read
    request.user for identity tagging — which, for any path that doesn't
    already trigger session access, would cause SessionMiddleware to append
    Vary: Cookie.  The _POSTHOG_EXEMPT_PATHS set in config/settings/base.py
    prevents that access by having _posthog_request_filter return False for
    this path.

    Note: Django's own sitemap framework independently marks the session as
    accessed (for CSRF/sites lookups), so Vary: Cookie appears on the
    sitemap regardless of PostHog. The exemption here ensures PostHog does
    not add a further distinct source of that header.
    """
    from config.settings.base import _posthog_request_filter

    class _FakeRequest:
        path = "/sitemap.xml"

    assert _posthog_request_filter(_FakeRequest()) is False, (
        "/sitemap.xml must be exempt from PosthogContextMiddleware "
        "(i.e. _posthog_request_filter must return False for it)"
    )
