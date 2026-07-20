"""
tests/public/test_help.py — Tests for the /help page (SNOW-456).

Covers:

  * ``GET /help/`` returns HTTP 200 for an anonymous user; the URL reverses
    to ``/help/``; the heading marker is present.
  * The seven always-on topic panels render regardless of waffle flag state.
  * The Favourites and Field-observations panels, plus the map panel's
    favourites-overlay, community-reports, and Report-button sentences, are
    gated on the matching per-user waffle flag — absent by default, present
    under ``@override_flag``.
  * The bulletin-guide cross-link is present in the page content.
  * The footer and top nav (both rendered on the homepage) independently
    link to /help/.

No factories or database fixtures are required — the page is entirely
static and carries no model queries.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

# `client` is pytest-django's built-in fixture (an anonymous ``Client()``);
# no local override needed.

ALWAYS_ON_TESTIDS = [
    "help-topic-overview",
    "help-topic-bulletins",
    "help-topic-weather",
    "help-topic-map",
    "help-topic-timeline",
    "help-topic-accounts",
    "help-topic-install",
]


@pytest.mark.django_db
class TestHelpPage:
    """The /help page satisfies the SNOW-456 acceptance criteria."""

    def test_returns_200_for_anonymous_user(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert response.status_code == 200

    def test_url_reverses_correctly(self) -> None:
        assert reverse("public:help") == "/help/"

    def test_has_heading(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-heading"' in response.content

    @pytest.mark.parametrize("testid", ALWAYS_ON_TESTIDS)
    def test_always_on_sections_present(self, client: Client, testid: str) -> None:
        response = client.get(reverse("public:help"))
        assert f'data-testid="{testid}"'.encode() in response.content

    def test_links_to_bulletin_guide(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert reverse("public:how_to_read_bulletin").encode() in response.content


@pytest.mark.django_db
class TestHelpPageFlagGating:
    """Flag-gated topics render only for users who can see the feature."""

    def test_favourites_panel_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-favourites"' not in response.content

    def test_observations_panel_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-observations"' not in response.content

    def test_recent_observations_panel_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-recent-observations"' not in response.content

    def test_map_favourites_sentence_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-favourites"' not in response.content

    def test_map_community_sentence_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-community"' not in response.content

    def test_map_report_sentence_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-report"' not in response.content

    @override_flag("favourites", active=True)
    def test_favourites_panel_present_when_flag_active(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-favourites"' in response.content

    @override_flag("favourites", active=True)
    def test_map_favourites_sentence_present_when_flag_active(
        self, client: Client
    ) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-favourites"' in response.content

    @override_flag("field_observations", active=True)
    def test_observations_panel_present_when_flag_active(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-observations"' in response.content

    @override_flag("field_observations", active=True)
    def test_map_report_sentence_present_when_flag_active(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-report"' in response.content

    @override_flag("community_reports", active=True)
    def test_map_community_sentence_present_when_flag_active(
        self, client: Client
    ) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-map-community"' in response.content

    @override_flag("observations_page", active=True)
    def test_recent_observations_panel_present_when_flag_active(
        self, client: Client
    ) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-recent-observations"' in response.content


@pytest.mark.django_db
class TestHelpPageDiscoverability:
    """The homepage links to /help/ from the footer; the top nav does not.

    SNOW-445 removed the header Help link to declutter the nav bar — the
    footer link is now the sole entry point. The footer link is asserted
    positively and the nav's absence negatively, each as its own regression
    test so a reintroduced nav link or a dropped footer link is caught.
    """

    def test_footer_links_to_help(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        content = response.content
        footer_start = content.index(b'data-testid="site-footer"')
        assert reverse("public:help").encode() in content[footer_start:]

    def test_nav_does_not_link_to_help(self, client: Client) -> None:
        response = client.get(reverse("public:home"))
        content = response.content
        nav_start = content.index(b"<nav")
        nav_end = content.index(b"</nav>", nav_start)
        assert reverse("public:help").encode() not in content[nav_start:nav_end]
