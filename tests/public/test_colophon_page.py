"""
tests/public/test_colophon_page.py — Tests for the /colophon page (SNOW-122).

Covers:

  * ``GET /colophon/`` returns HTTP 200 for an anonymous user.
  * The page contains the expected section ``data-testid`` markers.
  * Key attribution links and licence references are present, including
    all three avalanche data providers (SLF, ALBINA, Météo-France) added
    in SNOW-294.
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

    def test_albina_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"avalanche.report" in response.content

    def test_meteofrance_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert "Météo-France".encode() in response.content

    def test_meteofrance_licence_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert (
            b"portail-api.meteofrance.fr/web/en/DonneesPubliquesBRA/license"
            in response.content
        )

    def test_slope_layer_source_credited(self, client: Client) -> None:
        """SNOW-691: the slope-angle overlay's source is named here.

        swisstopo's terms for their free geodata and geoservices oblige us
        to indicate the source. The overlay is credited in the map legend's
        "Map data" section as well, but that section is populated at runtime
        from the style's source attributions — this page is the durable
        record, and the only one that survives the map failing to load.

        Asserted on the dataset title rather than the swisstopo domain: two
        basemap styles from the same publisher are already credited above,
        so a bare domain check would pass with this entry deleted.
        """
        response = client.get(reverse("public:colophon"))
        assert "Slope classes over 30°".encode() in response.content

    def test_url_reverses_correctly(self) -> None:
        assert reverse("public:colophon") == "/colophon/"
