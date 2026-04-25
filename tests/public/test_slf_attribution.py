"""
tests/public/test_slf_attribution.py — Tests for SLF data-licence
compliance surfaces (SNOW-30).

Covers:

  * ``/terms/`` page renders, has the four placeholder sections, and
    is reachable.
  * The global ``_site_footer.html`` partial renders on every public
    page (home, terms, bulletin, season, history, map) with the SLF
    licence link and a link to /terms.
  * ``_bulletin_panel.html`` includes the inline "Source: SLF" line
    plus the field-observation feedback link.
  * The map ``/api/region/<id>/summary/`` ``expanded`` fragment includes
    the same inline source + feedback line.

Per the SNOW-30 ticket, the *legal copy* on /terms is to be authored
by Hugo separately — these tests assert the structural scaffold is
present, not the wording of the legal text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _today_window() -> tuple[datetime, datetime]:
    """Return a (valid_from, valid_to) pair covering today in UTC."""
    today = timezone.localdate()
    vf = datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC)
    vt = datetime(today.year, today.month, today.day, 17, 0, tzinfo=UTC)
    return vf, vt


def _make_today_bulletin(region) -> object:
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
            "fallback_key_message": None,
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
def region(db):
    """A test Region instance."""
    return RegionFactory.create(
        region_id="CH-SLF1",
        name="Test Valley",
        slug="ch-slf1",
    )


@pytest.fixture()
def client():
    """An anonymous Django test client."""
    return Client()


# ---------------------------------------------------------------------------
# /terms page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTermsPage:
    """The /terms page satisfies the SNOW-30 acceptance criteria."""

    def test_returns_200(self, client):
        response = client.get(reverse("public:terms"))
        assert response.status_code == 200

    def test_has_heading(self, client):
        response = client.get(reverse("public:terms"))
        assert b'data-testid="terms-heading"' in response.content

    @pytest.mark.parametrize(
        "marker",
        [
            b'data-testid="terms-not-substitute"',
            b'data-testid="terms-on-site-assessment"',
            b'data-testid="terms-liability"',
            b'data-testid="terms-slf-no-liability"',
        ],
    )
    def test_has_four_required_sections(self, client, marker):
        response = client.get(reverse("public:terms"))
        assert marker in response.content

    def test_links_to_slf_data_service_terms(self, client):
        response = client.get(reverse("public:terms"))
        assert b"slf.ch/en/services-and-products/slf-data-service" in response.content

    def test_links_to_cc_by_4_0(self, client):
        response = client.get(reverse("public:terms"))
        assert b"creativecommons.org/licenses/by/4.0/" in response.content


# ---------------------------------------------------------------------------
# Global site footer — renders on every public page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGlobalSiteFooter:
    """The site-wide SLF licence footer renders on every public page."""

    def test_home_renders_footer(self, client):
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content
        assert b"slf.ch" in response.content
        assert b"CC BY 4.0" in response.content

    def test_terms_renders_footer(self, client):
        response = client.get(reverse("public:terms"))
        assert b'data-testid="site-footer"' in response.content

    def test_map_renders_footer(self, client):
        response = client.get(reverse("public:map"))
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_bulletin_renders_footer(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": region.region_id, "slug": region.slug},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_season_renders_footer(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:season_bulletins",
            kwargs={"region_id": region.region_id},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_random_bulletins_renders_footer(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:random_bulletins",
            kwargs={"region_id": region.region_id},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b'data-testid="site-footer"' in response.content

    def test_footer_links_to_terms(self, client):
        response = client.get(reverse("public:home"))
        assert reverse("public:terms").encode() in response.content


# ---------------------------------------------------------------------------
# Bulletin panel — inline source + feedback link
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinPanelAttribution:
    """The compact bulletin panel includes inline SLF attribution.

    ``_bulletin_panel.html`` is included by season + history pages
    (the standalone bulletin page is a separate WhiteRisk-replica
    template with its own rich footer). Both panel-rendering pages
    must surface SLF attribution.
    """

    def test_season_panel_includes_slf_source_block(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:season_bulletins",
            kwargs={"region_id": region.region_id},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b'data-testid="panel-slf-attribution"' in response.content

    def test_season_panel_includes_slf_feedback_link(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:season_bulletins",
            kwargs={"region_id": region.region_id},
        )
        response = client.get(url)
        assert b"pro.slf.ch/reply/public" in response.content

    def test_history_panel_includes_slf_source_block(self, client, region):
        _make_today_bulletin(region)
        url = reverse(
            "public:random_bulletins",
            kwargs={"region_id": region.region_id},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b'data-testid="panel-slf-attribution"' in response.content

    def test_bulletin_page_includes_slf_feedback_link(self, client, region):
        """The standalone bulletin page surfaces the SLF feedback link.

        The link lives in the page's rich SECTION 6 footer, not via
        ``_bulletin_panel.html`` (which this template doesn't include).
        """
        _make_today_bulletin(region)
        url = reverse(
            "public:bulletin",
            kwargs={"region_id": region.region_id, "slug": region.slug},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b"pro.slf.ch/reply/public" in response.content
        assert b'data-testid="slf-feedback-link"' in response.content


# ---------------------------------------------------------------------------
# Map expanded fragment — inline source + feedback link
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegionExpandedAttribution:
    """The /api/region/<id>/summary/ expanded fragment carries SLF attribution."""

    def test_expanded_includes_slf_source_block(self, client, region):
        _make_today_bulletin(region)
        url = reverse("api:region_summary", kwargs={"region_id": region.region_id})
        response = client.get(url)
        assert response.status_code == 200
        payload = json.loads(response.content)
        assert 'data-testid="expanded-slf-attribution"' in payload["expanded"]

    def test_expanded_includes_slf_feedback_link(self, client, region):
        _make_today_bulletin(region)
        url = reverse("api:region_summary", kwargs={"region_id": region.region_id})
        response = client.get(url)
        payload = json.loads(response.content)
        assert "pro.slf.ch/reply/public" in payload["expanded"]
