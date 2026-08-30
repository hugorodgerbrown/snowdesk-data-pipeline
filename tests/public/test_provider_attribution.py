"""
tests/public/test_provider_attribution.py — Tests for provider data-licence
compliance surfaces (SNOW-30, SNOW-294, SNOW-666).

Renamed from ``test_slf_attribution.py`` by SNOW-666: the surfaces under test
stopped being SLF-only when ALBINA and Météo-France were ingested, and a file
named for one provider while asserting three is the same drift the ticket
exists to fix.

Covers:

  * ``/terms/`` redirects permanently to ``/terms-of-service/``, and the
    merged page carries its twelve sections, the fidelity claim and every
    provider's own disclaimer.
  * **Every legal page names every provider** — the SNOW-666 guard, over
    ``/terms-of-service/`` and ``/privacy/``.
  * The global ``_site_footer.html`` partial renders on every public page
    (home, terms, bulletin, map) and carries the route to the licences.
  * The map legend names all three providers and links to /colophon/.

SNOW-174 note: the inline SLF source + feedback block that previously
lived in the map drawer's expanded fragment has been removed.

SNOW-294: the legend and footer both credited SLF, ALBINA and
Météo-France, with full attribution (including licence links) in the
colophon.

SNOW-769 removed the footer's copy — three links on every page, when the
map already had the legend and every other page has the colophon one
click away. That changes what these tests can assert and where. Anything
about *which surface* names a provider is now scoped to that surface's
own markup, via ``_footer_of`` and ``_legend_of``: on ``/`` both the
footer and the legend render into one body, so a page-wide assertion can
no longer tell which of them satisfied it, and would keep passing after
either one lost its links.

SNOW-770 merged /terms/ into /terms-of-service/ and left the URL as a
permanent redirect. Two of its sections were not duplicates and moved
rather than being deleted — the fidelity claim and the provider
disclaimers, the latter having no other home on the site, since the
colophon carries licence links and not disclaimer text. Both are
asserted here for that reason.

Per the SNOW-30 ticket, the *legal copy* is authored by Hugo separately
— these tests assert the structural scaffold is present, not the wording
of the legal text.
"""

from __future__ import annotations

import json
import re
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


def _footer_of(client: Client, url: str) -> str:
    """Return just the ``<footer>`` markup from the page at *url*.

    SNOW-769 made scoping necessary. The footer used to be the only
    surface naming the three providers on ``/``, so a page-wide assertion
    was unambiguous; now the map legend names them too, and an assertion
    over the whole body cannot tell which surface answered it.

    Args:
        client: The test client to fetch with.
        url: The page to fetch.

    Returns:
        The decoded markup from ``data-testid="site-footer"`` up to the
        closing ``</footer>`` tag.

    """
    body = client.get(url).content.decode("utf-8")
    footer = body[body.index('data-testid="site-footer"') :]
    return footer[: footer.index("</footer>")]


def _legend_of(client: Client, url: str) -> str:
    """Return just the map legend's attribution markup from the page at *url*.

    Args:
        client: The test client to fetch with.
        url: The page to fetch — in practice always the map page.

    Returns:
        The decoded markup of the legend's "Avalanche data" section, from
        its heading to the end of the enclosing ``<section>``.

    """
    body = client.get(url).content.decode("utf-8")
    legend = body[body.index("Avalanche data") :]
    return legend[: legend.index("</section>")]


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
class TestTermsRedirect:
    """``/terms/`` outlived its page and now points at the merged one.

    SNOW-770 folded the page into ``/terms-of-service/``. The URL is kept
    because it was linked from the footer for the project's whole life and
    is the one an external link or a search result would carry.
    """

    def test_terms_redirects_permanently(self, client: Client) -> None:
        """301, not 302 — the move is permanent and crawlers should learn it."""
        response = client.get(reverse("public:terms"))

        assert response.status_code == 301
        assert response["Location"] == reverse("public:terms_of_service")

    def test_terms_does_not_resolve_as_a_region(self, client: Client) -> None:
        """The redirect must stay ahead of the ``<str:region_id>/`` catch-all.

        ``urls.py`` registers literal slugs before the generic region
        patterns. Move this one below them and "terms" becomes a region
        lookup — a 404 on a URL that has worked for years, and the reason
        the original ``path()`` carried a comment saying so.
        """
        response = client.get(reverse("public:terms"), follow=True)

        assert response.status_code == 200
        assert b'data-testid="tos-heading"' in response.content


@pytest.mark.django_db
class TestTermsOfServicePage:
    """The merged page carries everything both originals carried.

    SNOW-30's acceptance criteria were written against ``/terms/``. That
    page is gone; the obligations are not, so the same assertions now run
    against the page that absorbed them.
    """

    def test_returns_200(self, client: Client) -> None:
        response = client.get(reverse("public:terms_of_service"))
        assert response.status_code == 200

    def test_has_heading(self, client: Client) -> None:
        response = client.get(reverse("public:terms_of_service"))
        assert b'data-testid="tos-heading"' in response.content

    @pytest.mark.parametrize(
        "marker",
        [
            b'data-testid="tos-about"',
            b'data-testid="tos-acceptance"',
            b'data-testid="tos-service"',
            b'data-testid="tos-safety"',
            b'data-testid="tos-ip"',
            b'data-testid="tos-provider-disclaimers"',
            b'data-testid="tos-subscriptions"',
            b'data-testid="tos-acceptable-use"',
            b'data-testid="tos-liability"',
            b'data-testid="tos-governing-law"',
            b'data-testid="tos-changes"',
            b'data-testid="tos-contact"',
        ],
    )
    def test_has_required_sections(self, client: Client, marker: bytes) -> None:
        response = client.get(reverse("public:terms_of_service"))
        assert marker in response.content

    def test_sections_are_numbered_one_to_twelve_in_order(self, client: Client) -> None:
        """SNOW-770 inserted a section mid-document and renumbered the rest.

        A merge that leaves two sections numbered 6, or jumps from 5 to 7,
        is the obvious way to get this wrong, and it reads as sloppy on a
        legal page.
        """
        content = client.get(reverse("public:terms_of_service")).content.decode()
        numbers = [int(n) for n in re.findall(r"<h2>(\d+)\.", content)]

        assert numbers == list(range(1, 13)), f"section numbers were {numbers}"

    def test_carries_the_fidelity_claim(self, client: Client) -> None:
        """The SNOW-666 framing moved here from /terms/ and must not be lost.

        It is the positive claim — the render is complete — that the
        fidelity guard in ``tests/sentinels/`` exists to keep true. The
        surrounding section only says what Snowdesk is *not*.
        """
        content = client.get(reverse("public:terms_of_service")).content.decode()

        assert "render each bulletin in full" in content
        assert "Nothing is withheld or summarised away" in content

    def test_carries_each_providers_own_disclaimer(self, client: Client) -> None:
        """The one surface that has them — the colophon carries licences only.

        Dropping this section in favour of a colophon link would delete the
        text rather than move it.
        """
        content = client.get(reverse("public:terms_of_service")).content.decode()

        assert "slf.ch/en/services-and-products/slf-data-service" in content
        assert "avalanche.report/more/open-data" in content
        assert "portail-api.meteofrance.fr" in content

    def test_links_to_cc_by_4_0(self, client: Client) -> None:
        response = client.get(reverse("public:terms_of_service"))
        assert b"creativecommons.org/licenses/by/4.0/" in response.content

    def test_no_longer_points_at_the_merged_page(self, client: Client) -> None:
        """§5 used to send the reader to /terms/ for "full licence details".

        That link would now bounce them straight back here. It points at
        the colophon instead, which is where the licence list actually is.
        """
        content = client.get(reverse("public:terms_of_service")).content.decode()

        assert reverse("public:terms") not in content
        assert reverse("public:colophon") in content


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
    # SNOW-770 merged ``public:terms`` into the Terms of Service, so there
    # are two legal pages to walk rather than three. The guard did not
    # weaken: the merged page inherited every provider the old one named,
    # which ``TestTermsOfServicePage`` asserts section by section.
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
    """The global footer renders on every public page, with its legal links.

    SNOW-769 removed the three provider links from this footer — they were
    the third copy of the same attribution, after the map legend and the
    colophon. What the footer still owes the reader is the route to the
    licences, which is the colophon link asserted below.
    """

    def test_home_renders_footer(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_terms_of_service_renders_footer(self, client: Client) -> None:
        response = client.get(reverse("public:terms_of_service"))
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

    def test_footer_links_to_the_terms_of_service(self, client: Client) -> None:
        footer = _footer_of(client, reverse("public:home"))

        assert reverse("public:terms_of_service") in footer

    def test_footer_links_to_privacy_and_colophon(self, client: Client) -> None:
        """The other two legal destinations, on every page (SNOW-769)."""
        footer = _footer_of(client, reverse("public:home"))

        assert reverse("public:privacy") in footer
        assert reverse("public:colophon") in footer

    def test_footer_links_past_the_redirect(self, client: Client) -> None:
        """The link points at the page, not at the URL that redirects to it.

        SNOW-769 put a "Terms of Use" link here aimed at /terms/. SNOW-770
        turned that URL into a redirect, so leaving the link alone would
        have cost every reader an extra round trip to reach the same page.
        """
        footer = _footer_of(client, reverse("public:home"))

        assert reverse("public:terms") not in footer

    def test_footer_no_longer_names_the_providers(self, client: Client) -> None:
        """The SNOW-769 removal, asserted where it happened.

        Scoped to the footer rather than the page, because on ``/`` the
        legend supplies these same three links and a page-wide assertion
        would pass whether the footer had been changed or not.
        """
        footer = _footer_of(client, reverse("public:home"))

        assert "slf.ch" not in footer
        assert "avalanche.report" not in footer
        assert "Météo-France" not in footer

    def test_a_non_map_page_still_reaches_the_licences(self, client: Client) -> None:
        """A page with no legend and no bulletin still has a route.

        ``/privacy/`` carries neither the map legend's attribution card nor
        a per-bulletin Source link, so the footer's colophon link is the
        only path to the provider licences from there. That makes it the
        one this ticket must not break.
        """
        footer = _footer_of(client, reverse("public:privacy"))

        assert reverse("public:colophon") in footer


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
# region info rather than bulletin content.
#
# SNOW-769: the footer stopped naming the providers, so the sentence that
# used to end this note — "attribution is now fully covered by the global
# site footer" — no longer describes anything. On the map page it is the
# legend; on a bulletin page it is that page's own per-bulletin Source
# link in the metadata strip, which names the service that issued the
# bulletin being read rather than all three regardless.


# ---------------------------------------------------------------------------
# Map drawer — no inline attribution in the expanded fragment
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegionExpandedAttribution:
    """The drawer's expanded fragment carries no attribution of its own.

    SNOW-174 removed the inline block when the fragment was rewritten to
    show structural region info rather than bulletin content. The map page
    around it still attributes — via the legend, asserted in
    ``TestMapLegendAttribution`` below.
    """

    def test_map_page_carries_the_footer(self, client: Client) -> None:
        """The canonical map page (/) carries the global site footer.

        SNOW-344: /map/ is now a 301 redirect; the live map page is /.
        SNOW-769: this asserts the footer is present, not that it names a
        provider — that moved to the legend, one class down.
        """
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

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
        # covered by the map legend, not the per-region tooltip.
        assert 'data-testid="expanded-slf-attribution"' not in payload["html"]


# ---------------------------------------------------------------------------
# Map legend — three-provider attribution (SNOW-294)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMapLegendAttribution:
    """The map legend's Avalanche data section names all three providers.

    SNOW-294: the legend was updated from SLF-only to SLF + ALBINA +
    Météo-France, with full attribution delegated to the colophon.

    SNOW-769: these assertions are scoped to the legend section rather
    than the page body. They used to be page-wide, which was unambiguous
    while the footer carried the same three links — it does not any more,
    and a page-wide assertion would now pass on the footer's evidence
    even if the legend lost them.
    """

    def test_map_legend_names_all_providers(self, client: Client) -> None:
        """The legend section links all three avalanche data providers.

        SNOW-344: /map/ is now a 301 redirect; the live map page is /.
        """
        legend = _legend_of(client, reverse("public:home"))

        assert "slf.ch" in legend
        assert "avalanche.report" in legend
        assert "Météo-France" in legend

    def test_map_legend_links_to_colophon(self, client: Client) -> None:
        """The legend section links to /colophon/ for full attribution."""
        legend = _legend_of(client, reverse("public:home"))

        assert reverse("public:colophon") in legend
