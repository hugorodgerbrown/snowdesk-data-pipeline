"""
tests/public/test_examples.py — Tests for the example URL views.

Covers ``/examples/random/``, ``/examples/category/<danger_level>/``,
and the deprecated ``/random/`` redirect.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


def _wrap(properties: dict) -> dict:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


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


def _make_bulletin_with_region(region, danger_level: str, issued_at: datetime):
    """Create a bulletin with a specific danger level linked to a region."""
    bulletin = BulletinFactory(
        raw_data=_wrap(
            {
                "dangerRatings": [{"mainValue": danger_level}],
                "avalancheProblems": [],
            }
        ),
        issued_at=issued_at,
        valid_from=issued_at,
        valid_to=issued_at,
    )
    RegionBulletinFactory(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


@pytest.mark.django_db
class TestDeprecatedRandomRedirect:
    """Tests for the deprecated ``/random/`` URL."""

    def test_redirects_permanently_to_examples_random(self, client: Client) -> None:
        """``/random/`` should 301 to ``/examples/random/``."""
        url = reverse("public:random")
        response = client.get(url)
        assert response.status_code == 301
        assert response["Location"] == "/examples/random/"


@pytest.mark.django_db
class TestExamplesRandom:
    """Tests for the ``/examples/random/`` view."""

    def test_redirects_to_bulletin(self, client: Client, region) -> None:
        """With bulletins available, redirects to a region bulletin page."""
        _make_bulletin_with_region(
            region, "moderate", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_random")
        response = client.get(url)
        assert response.status_code == 302
        assert "/CH-4115/" in response["Location"]

    def test_redirects_to_home_when_no_bulletins(self, client: Client) -> None:
        """When no bulletins exist, redirects to the homepage."""
        url = reverse("public:examples_random")
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"] == "/"


@pytest.mark.django_db
class TestExamplesCategory:
    """Tests for the ``/examples/category/<danger_level>/`` view."""

    @pytest.mark.parametrize(
        "slug,caaml_key",
        [
            ("low", "low"),
            ("moderate", "moderate"),
            ("considerable", "considerable"),
            ("high", "high"),
            ("very-high", "very_high"),
        ],
    )
    def test_redirects_for_each_danger_level(
        self, client: Client, region, slug: str, caaml_key: str
    ) -> None:
        """Each valid danger level slug redirects to a matching bulletin."""
        _make_bulletin_with_region(
            region, caaml_key, datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_category", kwargs={"danger_level": slug})
        response = client.get(url)
        assert response.status_code == 302
        assert "/CH-4115/" in response["Location"]

    def test_unknown_danger_level_returns_404(self, client: Client) -> None:
        """An unrecognised danger level slug returns 404."""
        url = reverse(
            "public:examples_category",
            kwargs={"danger_level": "extreme"},
        )
        response = client.get(url)
        assert response.status_code == 404

    def test_no_matching_bulletins_returns_404(self, client: Client, region) -> None:
        """Returns 404 when no bulletins match the requested danger level."""
        _make_bulletin_with_region(
            region, "low", datetime(2025, 3, 15, 8, 0, tzinfo=UTC)
        )
        url = reverse("public:examples_category", kwargs={"danger_level": "high"})
        response = client.get(url)
        assert response.status_code == 404
