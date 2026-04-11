"""
tests/public/test_zone_redirect.py — Tests for the zone_redirect view.

Verifies that naked-zone URLs (/<zone>/) redirect to the canonical
/<zone>/<name>/ URL, that query parameters are preserved, that the
zone-slug → name-slug mapping is cached, and that unknown zones 404.
"""

from datetime import UTC, datetime

import pytest

from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def region():
    """Return a Region with a human-readable name."""
    return RegionFactory(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def region_with_bulletin(region):
    """Return a Region that has at least one linked bulletin."""
    bulletin = BulletinFactory(
        issued_at=datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
    )
    RegionBulletinFactory(
        bulletin=bulletin,
        region=region,
        region_name_at_time="Valais",
    )
    return region


@pytest.mark.django_db
class TestZoneRedirect:
    """Tests for the zone_redirect view."""

    def test_redirects_to_canonical_url(self, client: Client, region_with_bulletin):
        """A GET to /<zone>/ should 302 to /<zone>/<name>/."""
        url = reverse("public:zone_redirect", kwargs={"zone": "ch-4115"})
        response = client.get(url)

        assert response.status_code == 302
        assert response.url == "/ch-4115/valais/"

    def test_preserves_query_params(self, client: Client, region_with_bulletin):
        """Query parameters like ?id=... must survive the redirect."""
        url = reverse("public:zone_redirect", kwargs={"zone": "ch-4115"})
        response = client.get(url, {"id": "bulletin-0001", "extra": "value"})

        assert response.status_code == 302
        assert "id=bulletin-0001" in response.url
        assert "extra=value" in response.url
        assert response.url.startswith("/ch-4115/valais/")

    def test_cache_is_populated_on_first_request(
        self, client: Client, region_with_bulletin
    ):
        """After the first redirect the name slug should be in the cache."""
        cache_key = "public:zone_name:ch-4115"
        assert cache.get(cache_key) is None

        url = reverse("public:zone_redirect", kwargs={"zone": "ch-4115"})
        client.get(url)

        assert cache.get(cache_key) == "valais"

    @pytest.mark.django_db
    def test_second_request_uses_cache(self, client: Client, region_with_bulletin):
        """A cached name slug means the redirect skips the database."""
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        cache.set("public:zone_name:ch-4115", "valais")

        url = reverse("public:zone_redirect", kwargs={"zone": "ch-4115"})

        with override_settings(DEBUG=True):
            reset_queries()
            response = client.get(url)
            query_count = len(connection.queries)

        assert response.status_code == 302
        assert response.url == "/ch-4115/valais/"
        assert query_count == 0, (
            f"Expected zero DB queries on cache hit, got {query_count}"
        )

    def test_unknown_zone_returns_404(self, client: Client):
        """A zone slug that doesn't match any Region should 404."""
        url = reverse("public:zone_redirect", kwargs={"zone": "xx-9999"})
        response = client.get(url)

        assert response.status_code == 404

    def test_bulletin_detail_warms_cache(self, client: Client, region_with_bulletin):
        """Visiting the full URL should populate the cache for future redirects."""
        cache_key = "public:zone_name:ch-4115"
        assert cache.get(cache_key) is None

        url = reverse(
            "public:bulletin",
            kwargs={"zone": "ch-4115", "name": "valais"},
        )
        response = client.get(url)

        assert response.status_code == 200
        assert cache.get(cache_key) == "valais"

    def test_multiword_region_name_is_slugified(self, client: Client):
        """Region names with spaces should be properly slugified in the URL."""
        region = RegionFactory(
            region_id="CH-5200",
            name="Haut Val de Bagnes",
            slug="ch-5200",
        )
        bulletin = BulletinFactory(
            issued_at=datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
        )
        RegionBulletinFactory(
            bulletin=bulletin,
            region=region,
            region_name_at_time="Haut Val de Bagnes",
        )

        url = reverse("public:zone_redirect", kwargs={"zone": "ch-5200"})
        response = client.get(url)

        assert response.status_code == 302
        assert response.url == "/ch-5200/haut-val-de-bagnes/"
