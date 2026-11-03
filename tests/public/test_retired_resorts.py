"""
tests/public/test_retired_resorts.py — a retired resort on the public site (SNOW-1404).

Retiring a resort is a soft delete: the row is kept, and every public
surface treats it as gone. Covered here, across the surfaces it reaches:

  - the resort page, its dated page and the map's resort sheet answer
    410 Gone (an unknown slug is still 404), and the pre-slug legacy URL
    still redirects — to the same 410;
  - resorts.geojson, the sitemap's resort file and the resorts index
    leave it out, through ``Resort.objects`` and no edit of their own;
  - the map editor refuses a new resort with a retired one's name in the
    same region, and says to restore it instead.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.regions.models import Resort
from tests.factories import MicroRegionFactory, ResortFactory, UserFactory


@pytest.fixture
def retired() -> Resort:
    """Return a retired, geocoded resort."""
    return ResortFactory.create(
        name="Gone Hill", latitude=46.2, longitude=7.2, retired=True
    )


@pytest.mark.django_db
class TestGone:
    """The resort's own addresses answer 410."""

    def test_page_is_410(self, client: Client, retired: Resort) -> None:
        """The resort page says the place is no longer listed."""
        response = client.get(retired.get_absolute_url())
        assert response.status_code == 410
        assert "no longer listed" in response.content.decode()

    def test_dated_page_is_410(self, client: Client, retired: Resort) -> None:
        """Another day's page is gone too."""
        url = reverse(
            "public:resort_date",
            kwargs={"slug": retired.slug, "date_str": "2026-01-15"},
        )
        assert client.get(url).status_code == 410

    def test_sheet_is_410(self, client: Client, retired: Resort) -> None:
        """The map's resort sheet gives the page's answer."""
        url = reverse(
            "public:resort_sheet_partial",
            kwargs={"slug": retired.slug},
        )
        assert client.get(url, HTTP_HX_REQUEST="true").status_code == 410

    def test_legacy_url_redirects_to_the_410(
        self, client: Client, retired: Resort
    ) -> None:
        """The pre-slug URL still 301s, and the new address answers 410."""
        url = reverse(
            "public:resort_legacy",
            kwargs={"resort_id": retired.pk, "slug": "x"},
        )
        response = client.get(url, follow=True)
        assert response.redirect_chain[0][1] == 301
        assert response.status_code == 410

    def test_unknown_slug_is_still_404(self, client: Client) -> None:
        """Gone is only for a resort that existed."""
        url = reverse("public:resort", kwargs={"slug": "never-was"})
        assert client.get(url).status_code == 404


@pytest.mark.django_db
class TestAbsent:
    """The resort lists leave a retired resort out."""

    def test_not_in_resorts_geojson(self, client: Client, retired: Resort) -> None:
        """The map's resort layer draws live resorts only."""
        live = ResortFactory.create(name="Live Hill", latitude=46.3, longitude=7.3)
        body = client.get(reverse("api:resorts_geojson")).json()
        assert [f["properties"]["id"] for f in body["features"]] == [live.slug]

    def test_not_in_the_sitemap(self, client: Client, retired: Resort) -> None:
        """A gone page is not offered to a crawler."""
        url = reverse("sitemap_file", kwargs={"name": "resorts"})
        assert str(retired.slug) not in client.get(url).content.decode()

    def test_not_in_the_resorts_index(self, client: Client, retired: Resort) -> None:
        """The resorts board lists live resorts only."""
        ResortFactory.create(name="Live Hill")
        page = client.get(reverse("public:resorts")).content.decode()
        assert "Live Hill" in page
        assert "Gone Hill" not in page


@pytest.mark.django_db
def test_editor_points_a_retired_namesake_at_restore(client: Client) -> None:
    """The map editor refuses a duplicate of a retired resort, naming it."""
    region = MicroRegionFactory.create(
        region_id="CH-4115",
        boundary={
            "type": "Polygon",
            "coordinates": [
                [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]
            ],
        },
    )
    ResortFactory.create(name="Gone Hill", region=region, retired=True)
    client.force_login(UserFactory.create(is_superuser=True, is_staff=True))

    response = client.post(
        reverse("api:edit_resort_create"),
        data=json.dumps(
            {"name": "Gone Hill", "latitude": 46.2, "longitude": 7.2, "canton": "VS"}
        ),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert "retired: restore it instead" in response.json()["detail"]
