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
  * The routes panel illustration renders, and renders inert: it is the real
    panel with real controls, and a help page wires none of them.
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
    "help-article-routes-lead",
    "help-article-routes-where",
    "help-article-routes-how",
    "help-article-routes-popup",
    "help-article-routes-row",
    "help-article-routes-notes",
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

    def test_illustrates_the_routes_panel_inertly(self, client: Client) -> None:
        """The article shows the real panel, dead to the keyboard and to AT.

        The wrapper is includes/_help_illustration.html; ``inert`` is what
        keeps the panel's real buttons out of the tab order, and the rows
        carry the real action cluster, so the Remove form's presence is the
        proof the cluster rendered.
        """
        content = client.get(reverse("public:help_article", args=["routes"])).content

        assert b'data-testid="help-article-routes-panel-illustration"' in content
        wrapper_start = content.index(
            b'data-testid="help-article-routes-panel-illustration"'
        )
        tag = content[content.rfind(b"<div", 0, wrapper_start) : wrapper_start]
        assert b"inert" in tag
        assert b'aria-hidden="true"' in tag
        assert b"data-row-remove" in content

    def test_illustrates_the_popup_with_a_profile(self, client: Client) -> None:
        """The popup illustration carries a drawn profile and the timings note.

        The chart is the mirror of the JavaScript-built popup, so the thing
        to pin is that its path data made it into the page: an empty
        ``paths`` list would render the popup's text with a bare baseline
        and nothing would fail.
        """
        content = client.get(reverse("public:help_article", args=["routes"])).content

        assert b'data-testid="help-article-routes-popup-illustration"' in content
        assert b'class="mt-1.5 block h-auto w-full text-route-line"' in content
        assert b'fill-opacity="0.16"' in content
        assert b'data-testid="help-article-routes-timings"' in content

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
        """An article is static text and must not touch the ORM.

        The illustrations are in-memory contexts, and the row illustration's
        Remove form takes the request's CSRF token — a cookie read, not a
        query. The first request is a warm-up outside the window: the CSP
        middleware fills its rule cache with one query on a cold worker, and
        that query is not this page's — see the same pattern on
        ``test_help_page_issues_no_queries``.
        """
        url = reverse("public:help_article", args=["routes"])
        client.get(url)
        with django_assert_num_queries(0):  # type: ignore[operator]
            client.get(url)


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
