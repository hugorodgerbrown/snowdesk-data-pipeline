"""
tests/public/test_colophon_page.py — Tests for the /colophon page (SNOW-122).

Covers:

  * ``GET /colophon/`` returns HTTP 200 for an anonymous user.
  * The page contains the expected section ``data-testid`` markers.
  * Key attribution links and licence references are present, including
    all three avalanche data providers (SLF, ALBINA, Météo-France) added
    in SNOW-294.
  * The attributions that exist to satisfy a licence obligation, rather
    than as a courtesy, are asserted individually: OpenStreetMap/ODbL
    behind the default basemap, and MaxMind GeoLite2 under CC BY-SA 4.0
    (``reference_data/geoip/README.md`` names this page as where that
    attribution is surfaced).
  * Every basemap the picker offers is credited — the page drifted behind
    ``settings.BASEMAP_STYLES`` when IGN and basemap.at were added, so the
    basemap credits are checked against that setting rather than a list
    hard-coded here.
  * The slope-angle overlay's source (SNOW-691), which is a second
    swisstopo dataset rather than a third basemap, so it is asserted on
    the dataset title — a bare publisher check would pass without it.
  * The global site footer contains a link to /colophon/.
  * The URL ``public:colophon`` resolves correctly.

No factories or database fixtures are required — the page is entirely
static and carries no model queries.
"""

from __future__ import annotations

import pytest
from django.conf import settings
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
            "colophon-libraries",
            "colophon-data",
            "colophon-basemap",
            "colophon-icons",
            "colophon-fonts",
            "colophon-email",
            "colophon-analytics",
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

    def test_yr_weather_icon_link_present(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"metno/weathericons" in response.content

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

    def test_openstreetmap_odbl_attribution_present(self, client: Client) -> None:
        """OpenFreeMap serves OSM data under ODbL, which requires credit."""
        response = client.get(reverse("public:colophon"))
        assert b"OpenStreetMap contributors" in response.content
        assert b"opendatacommons.org/licenses/odbl" in response.content

    def test_maxmind_geolite2_attribution_present(self, client: Client) -> None:
        """GeoLite2 is CC BY-SA 4.0; this page is where that credit lives."""
        response = client.get(reverse("public:colophon"))
        assert b"GeoLite2" in response.content
        assert b"MaxMind" in response.content
        assert b"CC BY-SA 4.0" in response.content

    @pytest.mark.parametrize(
        ("basemap_key", "credit"),
        [
            ("openfreemap_liberty", b"openfreemap.org"),
            ("swisstopo_winter", b"swisstopo.admin.ch"),
            ("ign_plan", b"geoservices.ign.fr"),
            ("basemap_at", b"basemap.at"),
        ],
    )
    def test_every_offered_basemap_is_credited(
        self, client: Client, basemap_key: str, credit: bytes
    ) -> None:
        """
        Each basemap in ``BASEMAP_STYLES`` has a credit on the page.

        Parametrised by key rather than asserting a bare list of links, so
        that adding a style to the setting without crediting its provider
        here fails on the missing parameter rather than passing silently.
        """
        assert basemap_key in settings.BASEMAP_STYLES
        response = client.get(reverse("public:colophon"))
        assert credit in response.content

    def test_all_basemap_styles_are_covered_by_the_credit_test(self) -> None:
        """``BASEMAP_STYLES`` has gained no style the test above ignores."""
        covered = {
            "openfreemap_liberty",
            "swisstopo_winter",
            "swisstopo_light",  # env-only override; shares the swisstopo credit
            "ign_plan",
            "basemap_at",
        }
        assert set(settings.BASEMAP_STYLES) == covered

    def test_posthog_credited_with_privacy_link(self, client: Client) -> None:
        response = client.get(reverse("public:colophon"))
        assert b"PostHog" in response.content
        assert reverse("public:privacy").encode() in response.content

    def test_url_reverses_correctly(self) -> None:
        assert reverse("public:colophon") == "/colophon/"
