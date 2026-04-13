"""
tests/public/test_zone_redirect.py — Tests for the region_redirect view.

Verifies that naked-region URLs (/<region_id>/) redirect to the canonical
/<region_id>/<slug>/ URL, that the name-slug cache is warmed, and that
unknown region IDs return 404.
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
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def region_with_bulletin(region):
    """Return a Region that has at least one linked bulletin."""
    bulletin = BulletinFactory.create(
        issued_at=datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time="Valais",
    )
    return region


@pytest.mark.django_db
class TestRegionRedirect:
    """Tests for the region_redirect view."""

    def test_redirects_to_canonical_url(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A GET to /<region_id>/ should 302 to /<region_id>/<slug>/."""
        url = reverse("public:region_redirect", kwargs={"region_id": "CH-4115"})
        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == "/CH-4115/valais/"

    def test_case_insensitive_region_id(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Both ``CH-4115`` and ``ch-4115`` resolve to the same region."""
        response = client.get("/ch-4115/")
        assert response.status_code == 302
        assert "/valais/" in response["Location"]

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region ID that doesn't match any Region should 404."""
        url = reverse("public:region_redirect", kwargs={"region_id": "XX-9999"})
        response = client.get(url)
        assert response.status_code == 404

    def test_bulletin_detail_warms_cache(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Visiting the full URL should populate the cache."""
        cache_key = "public:zone_name:ch-4115"
        assert cache.get(cache_key) is None

        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "CH-4115", "slug": "valais"},
        )
        response = client.get(url)

        assert response.status_code == 200
        assert cache.get(cache_key) == "valais"

    def test_multiword_region_name_is_slugified(self, client: Client) -> None:
        """Region names with spaces should be properly slugified."""
        region = RegionFactory.create(
            region_id="CH-5200",
            name="Haut Val de Bagnes",
            slug="ch-5200",
        )
        bulletin = BulletinFactory.create(
            issued_at=datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time="Haut Val de Bagnes",
        )

        url = reverse("public:region_redirect", kwargs={"region_id": "CH-5200"})
        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == "/CH-5200/haut-val-de-bagnes/"
