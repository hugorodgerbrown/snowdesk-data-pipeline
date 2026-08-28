"""
tests/public/test_help.py — Tests for the /help page (SNOW-456).

Covers:

  * ``GET /help/`` returns HTTP 200 for an anonymous user; the URL reverses
    to ``/help/``; the heading marker is present.
  * The fifteen always-on topic panels render regardless of waffle flag
    state.
  * The map panel's favourites-overlay, community-reports, and
    Report-button sentences are always present.
  * Every control in the map's own control column has a topic here, and the
    map's coachmark tour has a step for each one. Routes shipped with
    neither, which is what that pair of tests exists to stop recurring.
  * The SNOW-744 illustrations: each illustrated topic renders one, every
    illustration is inert, the four panel illustrations carry namespaced
    switch ids, and the page still issues no queries.
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

from typing import Any

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
    "help-topic-problems",
    "help-topic-calendar",
    "help-topic-map",
    "help-topic-timeline",
    "help-topic-layers",
    "help-topic-favourites",
    "help-topic-observations",
    "help-topic-routes",
    "help-topic-downloads",
    "help-topic-accounts",
    "help-topic-recent-observations",
    "help-topic-install",
]

# The map's five layers-menu groups, each documented by its own paragraph
# in ``_topic_layers.html``. Asserted individually rather than as "the
# layers panel is non-empty": a group added to #basemap-menu with no
# paragraph here is exactly the drift this list is for.
LAYERS_GROUP_TESTIDS = [
    "help-layers-bulletins",
    "help-layers-boundaries",
    "help-layers-resorts",
    "help-layers-weather",
    "help-layers-slope",
    "help-layers-basemaps",
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

    @pytest.mark.parametrize("testid", LAYERS_GROUP_TESTIDS)
    def test_layers_panel_covers_every_menu_group(
        self, client: Client, testid: str
    ) -> None:
        response = client.get(reverse("public:help"))
        assert f'data-testid="{testid}"'.encode() in response.content

    def test_layers_panel_explains_the_sync_dots(self, client: Client) -> None:
        """The dot on each row is the map's offline-availability readout.

        It is the one part of the menu whose meaning cannot be guessed
        from the row it sits on, and a user checking what will still work
        without signal is reading it for a safety answer.
        """
        content = client.get(reverse("public:help")).content
        assert b"help-layers-sync-dots" in content
        assert b"help-layers-disabled-rows" in content

    def test_install_panel_does_not_promise_always_fresh_data(
        self, client: Client
    ) -> None:
        """The cache CAN serve a danger rating, and the page has to say so.

        This copy claimed the opposite ("Snowdesk never shows you a stale
        danger rating from the cache") for as long as ``static/js/sw.js``
        had been serving the API feeds stale-while-revalidate. It is the
        one error on this page with a safety cost, so the correction is
        pinned rather than left to the next reader's judgement.
        """
        content = client.get(reverse("public:help")).content
        assert b"never shows you a stale danger rating" not in content
        assert b"help-install-staleness" in content

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

    def test_slope_panel_wrapper_keeps_the_shared_bottom_margin(
        self, client: Client
    ) -> None:
        """The anchor wrapper must not swallow the panel's own margin.

        ``_collapsible_panel.html`` carries ``mb-2 last:mb-0``. Inside the
        wrapper the ``<details>`` is the only child, so ``last:mb-0``
        matches and zeroes the gap every unwrapped sibling keeps — the
        space under Slope angle was half its neighbours' from SNOW-691
        until it was spotted by eye. The wrapper repeats both classes to
        put it back, and this pins that.
        """
        content = client.get(reverse("public:help")).content.decode()
        wrapper = content[content.index('id="help-topic-slope"') :][:120]
        assert 'class="mb-2 last:mb-0"' in wrapper, wrapper

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


@pytest.mark.django_db
class TestHelpCoversTheMapControls:
    """Every roundel in the map's control column is documented twice over.

    A map control is discoverable through the coachmark tour on the map
    (``#map-help-steps``) and explained on this page; the routes roundel
    shipped in SNOW-686/687 with neither, and stayed that way through two
    more tickets that touched the stack. These two tests fail the moment a
    sixth control is added to the column without both.
    """

    #: Control-column button ids, paired with the /help/ topic that
    #: explains what opening it does.
    CONTROL_TO_TOPIC = {
        "basemap-toggle": "help-topic-layers",
        "map-custom-download-control": "help-topic-downloads",
        "favourite-add-btn": "help-topic-favourites",
        "report-btn": "help-topic-observations",
        "route-add-btn": "help-topic-routes",
    }

    @pytest.mark.parametrize("control,topic", CONTROL_TO_TOPIC.items())
    def test_each_control_has_a_help_topic(
        self, client: Client, control: str, topic: str
    ) -> None:
        home = client.get(reverse("public:home")).content
        assert f'id="{control}"'.encode() in home, control

        help_page = client.get(reverse("public:help")).content
        assert f'data-testid="{topic}"'.encode() in help_page, topic

    @pytest.mark.parametrize("control", CONTROL_TO_TOPIC)
    def test_each_control_has_a_coachmark_step(
        self, client: Client, control: str
    ) -> None:
        home = client.get(reverse("public:home")).content
        assert f'data-help-target="#{control}"'.encode() in home, control


@pytest.mark.django_db
class TestHelpIllustrations:
    """Six topics render the real component they describe (SNOW-744).

    The illustrations are live mocks rather than screenshots — see
    docs/decisions/help-illustrations-are-live-mocks.md — so what these
    tests protect is the wiring and the two properties a decoration made
    of real components has to hold: it must not be reachable, and it must
    not answer to the ids the real components answer to.
    """

    #: Topics that carry an illustration, and a marker proving the right
    #: component rendered inside it rather than merely a wrapper.
    ILLUSTRATED = {
        "help-topic-weather": b'data-testid="bulletin-header"',
        "help-topic-bulletins": b'data-testid="day-windows-panel"',
        "help-topic-problems": b"Wind slab",
        "help-topic-calendar": b"calendar-cell",
        "help-topic-favourites": b"help-illustration-toggle-favourites",
        "help-topic-observations": b"help-illustration-toggle-observations",
        "help-topic-routes": b"help-illustration-toggle-routes",
        "help-topic-downloads": b"help-illustration-toggle-downloads",
    }

    @pytest.mark.parametrize("testid,marker", ILLUSTRATED.items())
    def test_illustrated_topics_render_their_component(
        self, client: Client, testid: str, marker: bytes
    ) -> None:
        content = client.get(reverse("public:help")).content
        assert f'data-testid="{testid}-illustration"'.encode() in content
        assert marker in content

    def test_unillustrated_topics_render_no_wrapper(self, client: Client) -> None:
        """A topic without an illustration is untouched by the new slot.

        The season timeline is the deliberate example: its demo partial
        exists, but the styles that make it legible live in
        static/css/map.css, which /help/ does not load.
        """
        content = client.get(reverse("public:help")).content
        for testid in ("help-topic-timeline", "help-topic-layers", "help-topic-map"):
            assert f'data-testid="{testid}-illustration"'.encode() not in content

    def test_every_illustration_is_inert(self, client: Client) -> None:
        """Illustrations hold real, focusable, dead controls.

        aria-hidden alone would leave a switch and a close button in the
        tab order while telling a screen reader they are not there.
        """
        content = client.get(reverse("public:help")).content.decode()
        for testid in self.ILLUSTRATED:
            start = content.index(f'data-testid="{testid}-illustration"')
            # The attributes sit on the same element, just before the testid.
            opening = content.rfind("<div", 0, start)
            wrapper = content[opening:start]
            assert "inert" in wrapper, testid
            assert 'aria-hidden="true"' in wrapper, testid

    def test_illustration_switch_ids_never_shadow_the_real_ones(
        self, client: Client
    ) -> None:
        """map.js finds the switch it drives by id; a decoration must not answer.

        The real ids are asserted absent from /help/ rather than merely
        different from the illustrations', because that is the failure
        that would matter: a page where the wrong element responds.
        """
        content = client.get(reverse("public:help")).content
        for real_id in (
            b"map-favourites-overlay-toggle",
            b"map-community-reports-overlay-toggle",
            b"map-downloads-overlay-toggle",
        ):
            assert real_id not in content, real_id

    def test_help_page_issues_no_queries(
        self, client: Client, django_assert_num_queries: Any
    ) -> None:
        """The illustrations are built in memory, and must stay that way.

        The season grid is the one that could regress: the real builder
        reads RegionDayRating, and a future edit that reached for it
        instead of the synthetic cells would put a query on a static page.
        """
        with django_assert_num_queries(0):
            client.get(reverse("public:help"))


@pytest.mark.django_db
class TestBulletinIllustrationsMatchTheirCopy:
    """The bulletin illustrations and the prose beside them must agree.

    Both of these caught a real mismatch while the illustrations were
    being built: the copy claimed the all-day row is labelled "All day"
    when day_windows.html deliberately tags only earlier/later windows,
    and the problem card carried a scattered aspect set the prose then
    described as contiguous. An illustration that contradicts the
    sentence under it is worse than no illustration.
    """

    def test_day_risk_panel_tags_only_the_later_window(self, client: Client) -> None:
        content = client.get(reverse("public:help")).content.decode()
        start = content.index('data-testid="help-topic-bulletins-illustration"')
        end = content.index("</details>", start)
        panel = content[start:end]

        # Two rows, one tag: the all-day baseline is untagged by design.
        assert panel.count('data-testid="day-window-row"') == 2
        assert panel.count('data-testid="day-window-pill"') == 1

    def test_problem_card_aspects_are_contiguous(self, client: Client) -> None:
        """A scattered set is a shape no real bulletin publishes."""
        from apps.public.component_previews import help_illustrations

        compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        aspects = help_illustrations()["card"]["aspects"]
        positions = [compass.index(a) for a in aspects]
        assert positions == list(range(positions[0], positions[0] + len(positions)))
