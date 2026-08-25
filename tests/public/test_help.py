"""
tests/public/test_help.py — Tests for the /help page (SNOW-456).

Covers:

  * ``GET /help/`` returns HTTP 200 for an anonymous user; the URL reverses
    to ``/help/``; the heading marker is present.
  * The ten always-on topic panels render regardless of waffle flag state.
  * The map panel's favourites-overlay, community-reports, and
    Report-button sentences are always present.
  * The Sync-log panel is gated on the ``sync_log`` per-user waffle flag —
    absent by default, present under ``@override_flag``. It is the only
    gated panel left; SNOW-724 opened the Map-weather (SNOW-573) and
    Slope-angle (SNOW-691) topics to everyone.
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
    "help-topic-favourites",
    "help-topic-observations",
    "help-topic-recent-observations",
    "help-topic-timeline",
    "help-topic-accounts",
    "help-topic-install",
]

ALWAYS_ON_MAP_SENTENCE_TESTIDS = [
    "help-map-favourites",
    "help-map-community",
    "help-map-report",
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

    @pytest.mark.parametrize("testid", ALWAYS_ON_MAP_SENTENCE_TESTIDS)
    def test_always_on_map_sentences_present(self, client: Client, testid: str) -> None:
        response = client.get(reverse("public:help"))
        assert f'data-testid="{testid}"'.encode() in response.content

    def test_links_to_bulletin_guide(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert reverse("public:how_to_read_bulletin").encode() in response.content


@pytest.mark.django_db
class TestHelpPageFlagGating:
    """The one gated topic renders only for users who can see the feature.

    SNOW-724 retired the ``weather_layer`` and ``slope_layer`` flags, so
    those two topics are asserted present for an anonymous visitor rather
    than under an override.
    """

    def test_sync_log_panel_absent_by_default(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-sync-log"' not in response.content

    @override_flag("sync_log", active=True)
    def test_sync_log_panel_present_when_flag_active(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-sync-log"' in response.content

    def test_weather_layer_panel_present_for_anonymous(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-weather-layer"' in response.content

    def test_slope_panel_present_for_anonymous(self, client: Client) -> None:
        response = client.get(reverse("public:help"))
        assert b'data-testid="help-topic-slope"' in response.content

    def test_slope_panel_is_an_anchor_target(self, client: Client) -> None:
        """SNOW-691: the map legend links here, so the id has to exist.

        The legend carries the five class swatches and nothing else; the
        heading is a link to ``#help-topic-slope`` and that fragment is the
        only route from the map to the layer's caveats. A panel that
        rendered without the id would leave the link landing at the top of
        the page with the warnings still collapsed somewhere below.
        """
        response = client.get(reverse("public:help"))
        assert b'id="help-topic-slope"' in response.content

    def test_slope_panel_states_the_layer_is_not_a_verdict(
        self, client: Client
    ) -> None:
        """SNOW-691: the shortcomings are the point of this panel.

        Asserted individually rather than as "the panel is non-empty",
        because each is a distinct thing a reader could otherwise get wrong:
        the 10 m grid hides small features, coverage stops mid-map with
        unshaded ground on both sides of the edge, and the layer is an input
        rather than permission to ski a slope.
        """
        content = client.get(reverse("public:help")).content
        for testid in (
            b"help-slope-resolution",
            b"help-slope-coverage",
            b"help-slope-accuracy",
            b"help-slope-not-a-decision",
        ):
            assert testid in content, testid


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
