"""
tests/public/test_help_articles.py — Tests for the /help/<slug>/ articles.

The long-form companions to the /help/ FAQ accordion: that page answers a
question in a few paragraphs, an article walks one feature through step by
step. Both are static text.

Covers:

  * ``GET /help/routes/`` returns 200 for an anonymous user and carries the
    heading marker.
  * An unpublished slug 404s. ``views.HELP_ARTICLES`` — not the presence of a
    template on disk — is what publishes an article, and this is the test that
    makes that true rather than merely intended.
  * The route resolves ahead of the generic ``<region_id>/<slug>/`` pattern.
    "help/routes/" matches that two-segment shape too, so registration order
    is load-bearing and silently reversible in a refactor.
  * Every section of the Routes article renders.
  * The Routes FAQ panel links to the article. The pair is the point: a link
    dropped in a refactor leaves two help surfaces that no longer know about
    each other, which is how the panels and the map's coachmark tour drifted
    apart before SNOW-744.
  * The article issues no database queries, matching ``/help/`` itself.

No factories or fixtures — the pages are static and carry no model queries.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import resolve, reverse

# Each section of the Routes article, by the testid its <section> carries.
ROUTES_SECTION_TESTIDS = [
    "help-article-routes-what",
    "help-article-routes-where",
    "help-article-routes-how",
    "help-article-routes-sharing",
    "help-article-routes-limits",
]


@pytest.mark.django_db
class TestRoutesArticle:
    """The first long-form help article."""

    def test_renders_for_an_anonymous_visitor(self, client: Client) -> None:
        """The article is public — no account, no flag."""
        response = client.get(reverse("public:help_article", args=["routes"]))

        assert response.status_code == 200
        assert b'data-testid="help-article-heading"' in response.content

    def test_url_is_the_expected_shape(self) -> None:
        """The article lives under /help/, not at the site root."""
        assert reverse("public:help_article", args=["routes"]) == "/help/routes/"

    def test_route_wins_over_the_generic_region_pattern(self) -> None:
        """``help/<slug>/`` must resolve before ``<region_id>/<slug>/``.

        Both patterns match two segments, so whichever is registered first
        wins. Moving the article route below the generic ones would send
        /help/routes/ to ``bulletin_detail`` looking for a region called
        "help" — a 404 that reads like a missing page rather than a routing
        mistake.
        """
        assert resolve("/help/routes/").view_name == "public:help_article"

    @pytest.mark.parametrize("testid", ROUTES_SECTION_TESTIDS)
    def test_each_section_renders(self, client: Client, testid: str) -> None:
        """Every section the brief asked for is present."""
        response = client.get(reverse("public:help_article", args=["routes"]))

        assert f'data-testid="{testid}"'.encode() in response.content, testid

    def test_says_where_to_find_routes_in_both_places(self, client: Client) -> None:
        """Both places a reader can find their routes are named.

        The Routes page has no upload control — it renders
        includes/_ugc_panel_row.html rather than includes/_ugc_panel.html —
        so a reader who goes there to add their first route finds nothing to
        press. Naming both surfaces is the point of the section.
        """
        content = client.get(reverse("public:help_article", args=["routes"])).content

        assert b'data-testid="help-article-routes-where-map"' in content
        assert b'data-testid="help-article-routes-where-page"' in content

    def test_issues_no_queries(
        self, client: Client, django_assert_num_queries: pytest.FixtureRequest
    ) -> None:
        """An article is static text and must not touch the ORM."""
        with django_assert_num_queries(0):  # type: ignore[operator]
            client.get(reverse("public:help_article", args=["routes"]))


@pytest.mark.django_db
class TestArticlePublication:
    """``HELP_ARTICLES`` is the publication gate."""

    def test_unknown_slug_404s(self, client: Client) -> None:
        """A slug absent from the mapping is not a page."""
        response = client.get("/help/not-an-article/")

        assert response.status_code == 404

    def test_every_mapped_article_renders(self, client: Client) -> None:
        """Each published slug resolves to a template that actually renders."""
        from apps.public.views import HELP_ARTICLES

        for slug in HELP_ARTICLES:
            response = client.get(reverse("public:help_article", args=[slug]))
            assert response.status_code == 200, slug


@pytest.mark.django_db
class TestPanelLinksToArticle:
    """The FAQ panel and its article point at each other."""

    def test_routes_panel_links_to_the_article(self, client: Client) -> None:
        """/help/ carries the link paragraph added to the Routes panel."""
        content = client.get(reverse("public:help")).content

        assert b'data-testid="help-routes-article-link"' in content
        assert b'href="/help/routes/"' in content
