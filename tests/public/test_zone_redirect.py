"""
tests/public/test_zone_redirect.py — Tests for the three bulletin URL forms.

Verifies that:

* Form 1 (``/<region_id>/``) renders today's bulletin in place — never
  redirects, even when the inbound region_id casing differs from the
  canonical lowercase form.
* Form 2 (``/<region_id>/<slug>/``) renders today's bulletin in place
  with the same in-place semantics.
* Form 3 (``/<region_id>/<slug>/<date>/``) renders that day's bulletin
  when the URL components are canonical, and 302s to the canonical form
  when they are not (e.g. preserved-case region_id, or a stale
  ``ch_4124``-style slug).
* Every render advertises the canonical form-3 URL via
  ``<link rel="canonical">``.
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
class TestForm1Render:
    """Form 1 (``/<region_id>/``) renders today's bulletin in place."""

    def test_form1_renders_today_inline(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A GET to /<region_id>/ renders today's bulletin (200, no 302)."""
        url = reverse("public:region_root", kwargs={"region_id": "CH-4115"})
        response = client.get(url)

        assert response.status_code == 200

    def test_form1_canonical_link_points_at_today_form2(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """The rendered page advertises the no-date form-2 ("today") URL.

        Two canonical URL families (SNOW-99): the no-date form is the
        live "today" page, the dated form is the historical record. A
        no-date inbound URL canonicalises to the no-date URL — it's a
        live page, not a frozen one.
        """
        url = reverse("public:region_root", kwargs={"region_id": "CH-4115"})
        response = client.get(url)

        assert response.status_code == 200
        canonical = response.context["canonical_url"]
        assert canonical.endswith("/ch-4115/valais/")
        assert b'<link rel="canonical"' in response.content

    def test_form1_case_insensitive_region_id(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Both ``CH-4115`` and ``ch-4115`` resolve to the same region."""
        response = client.get("/ch-4115/")
        assert response.status_code == 200
        assert response.context["region"].region_id == "CH-4115"

    def test_form1_unknown_region_returns_404(self, client: Client) -> None:
        """A region ID that doesn't match any Region should 404."""
        url = reverse("public:region_root", kwargs={"region_id": "XX-9999"})
        response = client.get(url)
        assert response.status_code == 404

    def test_form1_query_string_honored(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """``?issue=`` on form 1 is read by the renderer (not redirected)."""
        # No matching bulletin issue → renderer falls back to the default,
        # but the request must NOT 302; the URL should render at form 1.
        response = client.get("/CH-4115/?issue=00000000-0000-0000-0000-000000000000")
        assert response.status_code == 200


@pytest.mark.django_db
class TestForm2Render:
    """Form 2 (``/<region_id>/<slug>/``) renders today's bulletin in place."""

    def test_form2_renders_today_inline(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A GET to /<region_id>/<slug>/ renders today's bulletin (200)."""
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "CH-4115", "slug": "valais"},
        )
        response = client.get(url)
        assert response.status_code == 200

    def test_form2_with_arbitrary_slug_still_renders(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """Form 2 ignores the slug for lookup; renders in place even if wrong."""
        # No-date URLs render in place even when components are non-canonical.
        # The canonical link in the HTML still points at the no-date
        # form-2 "today" URL with the proper region_id + name slug.
        response = client.get("/CH-4115/wrong-slug/")
        assert response.status_code == 200
        assert response.context["canonical_url"].endswith("/ch-4115/valais/")

    def test_form2_unknown_region_returns_404(self, client: Client) -> None:
        """An unknown region_id at form 2 still 404s."""
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": "XX-9999", "slug": "anything"},
        )
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestForm3CanonicalRedirect:
    """Form 3 with a non-canonical region_id or slug 302s to canonical."""

    def test_uppercase_region_id_redirects_to_lowercase(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """``/CH-4115/valais/<date>/`` 302s to ``/ch-4115/valais/<date>/``."""
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "CH-4115",
                "slug": "valais",
                "date_str": "2025-03-15",
            },
        )
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"] == "/ch-4115/valais/2025-03-15/"

    def test_stale_underscore_slug_redirects_to_name_slug(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """``/ch-4115/ch_4115/<date>/`` 302s to the name-derived slug."""
        # Mirrors the bug the user reported on production data: the
        # second segment had been ``ch_4115`` (auto-generated from the
        # ``Region.slug`` field) instead of ``valais`` (slugified name).
        response = client.get("/ch-4115/ch_4115/2025-03-15/")
        assert response.status_code == 302
        assert response["Location"] == "/ch-4115/valais/2025-03-15/"

    def test_non_canonical_redirect_preserves_query_string(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """A non-canonical form-3 redirect carries any inbound ``?issue=``."""
        response = client.get("/CH-4115/valais/2025-03-15/?issue=abc-123")
        assert response.status_code == 302
        assert response["Location"] == ("/ch-4115/valais/2025-03-15/?issue=abc-123")

    def test_canonical_form3_url_renders_directly(
        self, client: Client, region_with_bulletin: None
    ) -> None:
        """When the inbound URL is already canonical, render with 200."""
        response = client.get("/ch-4115/valais/2025-03-15/")
        assert response.status_code == 200


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
                "region_id": "ch-4115",
                "slug": "valais",
                "date_str": today,
            },
        )
        response = client.get(url)

        assert response.status_code == 200
        assert cache.get(cache_key) == "valais"
