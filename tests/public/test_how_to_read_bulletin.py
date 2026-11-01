"""
tests/public/test_how_to_read_bulletin.py — Tests for /how-to-read-a-bulletin/ (SNOW-679).

Covers:

  * The page renders for an anonymous user and names all three providers —
    SLF, ALBINA and Météo-France — in its intro and meta description, not
    SLF alone.
  * The "What differs by provider" section and its four linkable fragments
    (avalanche-activity, tendency-outlook, slf-weather,
    meteofrance-conventions) render, each with its own testid.
  * The fragments sit before the Limitations section, so the provider layer
    reads as part of the guide's spine rather than a footnote.
  * The section's claims match the code they describe: the Météo-France
    tendency is derived from the J+2 level, and its aspects are
    bulletin-wide.
  * The page reads no bulletin, rating or region table. It is not
    query-free — every page extending ``public/base.html`` looks up the
    site banners (``docs/query-counts.md``) — so the assertion is on the
    tables, as ``tests/public/test_help.py`` does.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

PROVIDER_FRAGMENTS = [
    ("avalanche-activity", "guide-avalanche-activity"),
    ("tendency-outlook", "guide-tendency-outlook"),
    ("slf-weather", "guide-slf-weather"),
    ("meteofrance-conventions", "guide-meteofrance-conventions"),
]


def _page(client: Client) -> str:
    """Return the guide page's rendered HTML.

    Args:
        client: pytest-django's anonymous test client.

    Returns:
        The decoded response body.

    """
    response = client.get(reverse("public:how_to_read_bulletin"))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestProviderCoverage:
    """The guide speaks for all three providers, not SLF alone."""

    def test_intro_names_all_three_providers(self, client: Client) -> None:
        """The intro names SLF, ALBINA and Météo-France."""
        body = _page(client)
        for provider in ("SLF", "ALBINA", "Météo-France"):
            assert provider in body

    def test_meta_description_names_all_three_providers(self, client: Client) -> None:
        """The meta description no longer says the guide is SLF-only."""
        body = _page(client)
        assert "SLF, ALBINA or Météo-France" in body
        assert "reading the SLF Swiss avalanche bulletin" not in body


@pytest.mark.django_db
class TestProviderDifferences:
    """The "What differs by provider" section and its fragments."""

    def test_section_renders(self, client: Client) -> None:
        """The section is present with its id and testid."""
        body = _page(client)
        assert 'id="provider-differences"' in body
        assert 'data-testid="guide-provider-differences"' in body

    @pytest.mark.parametrize(("fragment", "testid"), PROVIDER_FRAGMENTS)
    def test_fragment_is_linkable(
        self, client: Client, fragment: str, testid: str
    ) -> None:
        """Each fragment carries the id a link can target and its testid."""
        body = _page(client)
        assert f'id="{fragment}"' in body
        assert f'data-testid="{testid}"' in body

    def test_section_sits_before_limitations(self, client: Client) -> None:
        """The provider layer comes before the Limitations section."""
        body = _page(client)
        assert body.index('id="provider-differences"') < body.index(
            'data-testid="guide-limitations"'
        )

    def test_meteofrance_tendency_is_described_as_derived(self, client: Client) -> None:
        """The Météo-France tendency is the J+2 level against today's."""
        body = _page(client)
        tendency = body[body.index('id="tendency-outlook"') :]
        tendency = tendency[: tendency.index('id="slf-weather"')]
        assert "J+2" in tendency

    def test_meteofrance_aspects_are_bulletin_wide(self, client: Client) -> None:
        """The conventions fragment says the aspects cover the whole bulletin."""
        body = _page(client)
        conventions = body[body.index('id="meteofrance-conventions"') :]
        assert "once for the whole bulletin" in conventions


@pytest.mark.django_db
def test_reads_no_bulletin_tables(client: Client) -> None:
    """The guide is static: it reads no bulletin, rating or region table."""
    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse("public:how_to_read_bulletin"))
    data_queries = [
        q["sql"]
        for q in ctx.captured_queries
        if "regiondayrating" in q["sql"].lower()
        or "bulletins_bulletin" in q["sql"].lower()
        or "regions_microregion" in q["sql"].lower()
    ]
    assert data_queries == []
