"""
tests/public/test_colophon_page.py — Tests for the /colophon page (SNOW-122).

Covers:

  * ``GET /colophon/`` returns HTTP 200 for an anonymous user.
  * The page contains the expected section ``data-testid`` markers.
  * Key attribution links and licence references are present.
  * The global site footer contains a link to /colophon/.
  * The URL ``public:colophon`` resolves correctly.

No factories or database fixtures are required — the page is entirely
static and carries no model queries.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture()
def client() -> Client:
    """An anonymous Django test client."""
    return Client()


@pytest.mark.django_db
class TestColophonPage:
    """The /colophon page satisfies the SNOW-122 acceptance criteria."""

    def test_returns_200_for_anonymous_user(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert response.status_code == 200

    def test_has_heading(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b'data-testid="colophon-heading"' in response.content

    @pytest.mark.parametrize(
        "testid",
        [
            "colophon-frameworks",
            "colophon-data",
            "colophon-icons",
            "colophon-hosting",
        ],
    )
    def test_required_sections_present(self, client: Client, testid: str) -> None:
        response = client.get(reverse("public:colophon"))
        assert f'data-testid="{testid}"'.encode() in response.content

    def test_slf_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"slf.ch" in response.content

    def test_cc_by_attribution_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"CC BY 4.0" in response.content

    def test_meteocons_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"basmilius" in response.content

    def test_mit_licence_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"MIT" in response.content

    def test_site_footer_rendered(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b'data-testid="site-footer"' in response.content

    def test_footer_links_to_colophon(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        assert reverse("public:colophon").encode() in response.content

    def test_url_reverses_correctly(self) -> None:
        assert reverse("public:colophon") == "/colophon/"
