"""
tests/public/test_zone_redirect.py — Tests for the canonical-redirect views.

Verifies that forms 1 (``/<region_id>/``) and 2 (``/<region_id>/<slug>/``)
both 302 to the fully-qualified form-3 URL (``/<region_id>/<slug>/<date>/``)
with today's date defaulted in, that query strings are preserved across
the redirect, that the name-slug cache is warmed by the canonical render,
and that unknown region IDs return 404.
"""

from datetime import UTC, datetime

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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
    """Tests for the form 1 (``/<region_id>/``) → form-3 redirect."""

    def test_redirects_to_canonical_form3_url(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A GET to /<region_id>/ should 302 to the form-3 URL with today."""
        today = timezone.now().date().isoformat()
        url = reverse("public:region_redirect", kwargs={"region_id": "CH-4115"})
        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == f"/CH-4115/valais/{today}/"

    def test_case_insensitive_region_id(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Both ``CH-4115`` and ``ch-4115`` resolve to the same region."""
        today = timezone.now().date().isoformat()
        response = client.get("/ch-4115/")
        assert response.status_code == 302
        assert response["Location"] == f"/CH-4115/valais/{today}/"

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region ID that doesn't match any Region should 404."""
        url = reverse("public:region_redirect", kwargs={"region_id": "XX-9999"})
        response = client.get(url)
        assert response.status_code == 404

    def test_query_string_preserved(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Query strings (e.g. ``?issue=<uuid>``) survive the redirect."""
        today = timezone.now().date().isoformat()
        response = client.get("/CH-4115/?issue=abc-123&foo=bar")
        assert response.status_code == 302
        assert response["Location"] == (
            f"/CH-4115/valais/{today}/?issue=abc-123&foo=bar"
        )

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

        today = timezone.now().date().isoformat()
        url = reverse("public:region_redirect", kwargs={"region_id": "CH-5200"})
        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == f"/CH-5200/haut-val-de-bagnes/{today}/"


@pytest.mark.django_db
class TestRegionSlugRedirect:
    """Tests for the form 2 (``/<region_id>/<slug>/``) → form-3 redirect."""

    def test_form2_redirects_to_form3(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A GET to /<region_id>/<slug>/ should 302 to form-3 with today."""
        today = timezone.now().date().isoformat()
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "CH-4115", "slug": "valais"},
        )
        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == f"/CH-4115/valais/{today}/"

    def test_form2_normalises_arbitrary_slug(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Form 2 ignores the inbound slug and uses the canonical name slug."""
        today = timezone.now().date().isoformat()
        # Inbound slug is wrong/historical — redirect target must use the
        # canonical name slug regardless.
        response = client.get("/CH-4115/wrong-slug/")
        assert response.status_code == 302
        assert response["Location"] == f"/CH-4115/valais/{today}/"

    def test_form2_query_string_preserved(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Query strings survive the form-2 redirect too."""
        today = timezone.now().date().isoformat()
        response = client.get("/CH-4115/valais/?issue=abc-123")
        assert response.status_code == 302
        assert response["Location"] == (f"/CH-4115/valais/{today}/?issue=abc-123")

    def test_form2_unknown_region_returns_404(self, client: Client) -> None:
        """An unknown region_id at form 2 still 404s."""
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "XX-9999", "slug": "anything"},
        )
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestCanonicalRenderWarmsCache:
    """Visiting the canonical form-3 URL should warm the name-slug cache."""

    def test_form3_warms_cache(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Visiting the canonical URL populates the name-slug cache."""
        cache_key = "public:zone_name:ch-4115"
        assert cache.get(cache_key) is None

        today = timezone.now().date().isoformat()
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "CH-4115",
                "slug": "valais",
                "date_str": today,
            },
        )
        response = client.get(url)

        assert response.status_code == 200
        assert cache.get(cache_key) == "valais"
