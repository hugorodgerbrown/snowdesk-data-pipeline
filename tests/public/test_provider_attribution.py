"""
tests/public/test_provider_attribution.py — Tests for provider data-licence
compliance surfaces (SNOW-30, SNOW-294, SNOW-666).

Renamed from ``test_slf_attribution.py`` by SNOW-666: the surfaces under test
stopped being SLF-only when ALBINA and Météo-France were ingested, and a file
named for one provider while asserting three is the same drift the ticket
exists to fix.

Covers:

  * ``/terms/`` page renders, has its required sections, and is reachable.
  * **Every legal page names every provider** — the SNOW-666 guard, over
    ``/terms/``, ``/terms-of-service/`` and ``/privacy/``.
  * The global ``_site_footer.html`` partial renders on every public
    page (home, terms, bulletin, map) with all three provider links
    (SLF, ALBINA, Météo-France) and a link to /terms.
  * The map legend names all three providers and links to /colophon/.

SNOW-174 note: the inline SLF source + feedback block that previously
lived in the map drawer's expanded fragment has been removed. Attribution
is fully covered by the global ``_site_footer.html`` which renders on
every page (including ``/map/``). The ``TestRegionExpandedAttribution``
class now asserts that the site footer — not the drawer fragment — is
the single canonical attribution surface.

SNOW-294: the legend/footer now credits SLF, ALBINA, and Météo-France.
Full attribution (including licence links) lives in the colophon.

Per the SNOW-30 ticket, the *legal copy* on /terms is to be authored
by Hugo separately — these tests assert the structural scaffold is
present, not the wording of the legal text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _today_window() -> tuple[datetime, datetime]:
    """Return a (valid_from, valid_to) pair covering today in UTC."""
    today = timezone.localdate()
    vf = datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC)
    vt = datetime(today.year, today.month, today.day, 17, 0, tzinfo=UTC)
    return vf, vt


def _make_today_bulletin(region: MicroRegion) -> object:
    """Create a bulletin covering today for *region*."""
    vf, vt = _today_window()
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        render_model_version=3,
        render_model={
            "version": 3,
            "danger": {"key": "moderate", "number": "2", "subdivision": None},
            "traits": [],
            "snowpack_structure": None,
            "metadata": {
                "publication_time": "2026-03-15T05:30:00+00:00",
                "valid_from": vf.isoformat(),
                "valid_until": vt.isoformat(),
                "next_update": vt.isoformat(),
                "unscheduled": False,
            },
        },
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


@pytest.fixture()
def region(db: Any) -> MicroRegion:
    """A test MicroRegion instance with full hierarchy for breadcrumb rendering."""
    major = MajorRegionFactory.create(
        prefix="CH-9", country="CH", name_native="Test Major"
    )
    sub = SubRegionFactory.create(prefix="CH-91", major=major, name_native="Test Sub")
    return MicroRegionFactory.create(
        region_id="CH-SLF1",
        name="Test Valley",
        slug="ch-slf1",
        subregion=sub,
    )


@pytest.fixture()
def client() -> Client:
    """An anonymous Django test client."""
    return Client()


# ---------------------------------------------------------------------------
# /terms page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTermsPage:
    """The /terms page satisfies the SNOW-30 acceptance criteria."""

    def test_returns_200(self, client: Client) -> None:
        response = client.get(reverse("public:terms"))
        assert response.status_code == 200

    def test_has_heading(self, client: Client) -> None:
        response = client.get(reverse("public:terms"))
        assert b'data-testid="terms-heading"' in response.content

    @pytest.mark.parametrize(
        "marker",
        [
            b'data-testid="terms-data-sources"',
            b'data-testid="terms-not-authoritative"',
            b'data-testid="terms-on-site-assessment"',
            b'data-testid="terms-liability"',
            b'data-testid="terms-provider-disclaimers"',
        ],
    )
    def test_has_required_sections(self, client: Client, marker: bytes) -> None:
        response = client.get(reverse("public:terms"))
        assert marker in response.content

    def test_links_to_slf_data_service_terms(self, client: Client) -> None:
        response = client.get(reverse("public:terms"))
        assert b"slf.ch/en/services-and-products/slf-data-service" in response.content

    def test_links_to_cc_by_4_0(self, client: Client) -> None:
        response = client.get(reverse("public:terms"))
        assert b"creativecommons.org/licenses/by/4.0/" in response.content

    def test_links_to_meteofrance_open_data_licence(self, client: Client) -> None:
        """Météo-France's licence is not CC BY 4.0 and needs its own link (SNOW-666)."""
        response = client.get(reverse("public:terms"))
        assert b"portail-api.meteofrance.fr" in response.content


# ---------------------------------------------------------------------------
# Three-provider coverage across every legal surface (SNOW-666)
# ---------------------------------------------------------------------------

# Byte fragments that must appear on each legal page, one per provider. Chosen
# to survive rewording: a provider's name, not a sentence it happens to sit in.
_PROVIDER_MARKERS: tuple[bytes, ...] = (
    b"SLF",
    b"ALBINA",
    b"M\xc3\xa9t\xc3\xa9o-France",  # UTF-8 "Météo-France"
)

_LEGAL_PAGE_ROUTES: tuple[str, ...] = (
    "public:terms",
    "public:terms_of_service",
    "public:privacy",
)


@pytest.mark.django_db
class TestEveryLegalPageNamesEveryProvider:
    """Every legal page names all three bulletin providers (SNOW-666).

    Snowdesk republishes bulletins for 461 micro-regions, of which only 149
    are Swiss. The legal pages described an SLF-only site for long enough
    that 312 regions were served under licences those pages never
    acknowledged — so this is the guard that stops a fourth provider, or a
    copy rewrite, silently dropping one again.

    Deliberately asserts on provider *names* rather than section testids:
    the failure mode being guarded against is a provider going unmentioned,
    which no structural marker would catch.
    """

    @pytest.mark.parametrize("route", _LEGAL_PAGE_ROUTES)
    @pytest.mark.parametrize("provider", _PROVIDER_MARKERS)
    def test_page_names_provider(
        self, client: Client, route: str, provider: bytes
    ) -> None:
        response = client.get(reverse(route))
        assert response.status_code == 200
        assert provider in response.content, (
            f"{route} does not name {provider.decode()}"
        )


# ---------------------------------------------------------------------------
# Global site footer — renders on every public page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGlobalSiteFooter:
    """The site-wide three-provider attribution footer renders on every public page."""

    def test_home_renders_footer(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content
        assert b"slf.ch" in response.content
        assert b"avalanche.report" in response.content
        assert "Météo-France".encode() in response.content

    def test_terms_renders_footer(self, client: Client) -> None:
        response = client.get(reverse("public:terms"))
        assert b'data-testid="site-footer"' in response.content

    def test_map_renders_footer(self, client: Client) -> None:
        """The canonical map page (/) carries the site footer (SNOW-344: was /map/)."""
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_bulletin_renders_footer(self, client: Client, region: MicroRegion) -> None:
        _make_today_bulletin(region)
        # SNOW-99: hit the canonical form-3 URL directly via the model's
        # ``get_absolute_url`` so the test isn't affected by the form-1/2
        # or off-canonical-form-3 redirect chains.
        response = client.get(region.get_absolute_url())
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_footer_links_to_terms(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        assert reverse("public:terms").encode() in response.content


# ---------------------------------------------------------------------------
# Bulletin page — historical note
# ---------------------------------------------------------------------------
#
# The bulletin page used to carry an inline SLF feedback link in its
# SECTION 6 footer. SNOW-80 removed that footer entirely (it duplicated
# the licence row carried by the global ``_site_footer.html``), so the
# per-page feedback link is gone too.
#
# SNOW-174: the map drawer's expanded fragment previously carried an
# inline source + feedback block (expanded-slf-attribution). That block
# was removed when the expanded fragment was rewritten to show structural
# region info rather than bulletin content. Attribution is now fully
# covered by the global site footer on ``/`` (SNOW-344: /map/ redirects there).


# ---------------------------------------------------------------------------
# Map page — SLF attribution via site footer (not the drawer fragment)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegionExpandedAttribution:
    """SLF attribution on the map page is carried by the site footer only.

    SNOW-174: the inline attribution block was removed from the drawer's
    expanded fragment. The global ``_site_footer.html`` on ``/map/`` is the
    single SLF attribution surface — verified by ``TestGlobalSiteFooter``
    above.

    These tests assert the map page itself has the footer (belt-and-braces)
    and that the drawer expanded fragment no longer duplicates it.
    """

    def test_map_page_carries_slf_footer(self, client: Client) -> None:
        """The canonical map page (/) carries the global SLF attribution footer.

        SNOW-344: /map/ is now a 301 redirect; the live map page is /.
        """
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content
        assert b"slf.ch" in response.content

    def test_expanded_fragment_does_not_duplicate_attribution(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The region tooltip HTML does NOT embed an inline SLF attribution block."""
        url = reverse(
            "api:region_summary", kwargs={"region_id": region.region_id.lower()}
        )
        response = client.get(url)
        assert response.status_code == 200
        payload = json.loads(response.content)
        # SNOW-174: the tooltip returns {"html": "..."} only; attribution is
        # covered by the global site footer, not the per-region tooltip.
        assert 'data-testid="expanded-slf-attribution"' not in payload["html"]


# ---------------------------------------------------------------------------
# Map legend — three-provider attribution (SNOW-294)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMapLegendAttribution:
    """The map legend's Avalanche data section names all three providers.

    SNOW-294: the legend was updated from SLF-only to SLF + ALBINA +
    Météo-France, with full attribution delegated to the colophon.
    """

    def test_map_legend_names_all_providers(self, client: Client) -> None:
        """The map legend contains links to all three avalanche data providers.

        SNOW-344: /map/ is now a 301 redirect; the live map page is /.
        """
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b"slf.ch" in response.content
        assert b"avalanche.report" in response.content
        assert "Météo-France".encode() in response.content

    def test_map_legend_links_to_colophon(self, client: Client) -> None:
        """The map legend links to /colophon/ for full attribution.

        SNOW-344: /map/ is now a 301 redirect; the live map page is /.
        """
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert reverse("public:colophon").encode() in response.content
