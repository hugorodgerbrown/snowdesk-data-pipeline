"""
tests/public/test_map_legend.py — the map legend's two tiers (3 Nov 2026).

The (i) card opens on "On the map now", one compact key per layer that
static/js/map_legend.js shows only while the current view draws it, and
keeps every key and the sources on a second page, "All keys & sources",
whose full descriptions are the help page's "Map key" topic. These tests
pin the server-rendered half: the two pages, the keys and their order, the
danger scale shown once, the wildlife zones behind their flag, and "Show
map demo" moved from the card to the "?" menu.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from tests.factories import UserFactory

TERRAIN_CLASS_URL = "https://tiles.example.test/terrain-class/{z}/{x}/{y}.png"

#: The first tier's keys, in card order: the day's first, then every day's.
#: Mirrors KEYS in static/js/map_legend_core.js.
KEYS = [
    "danger",
    "problem_terrain",
    "community_reports",
    "weather",
    "terrain_filter",
    "ski_tours",
    "rest_zones",
    "routes",
    "pins",
    "boundaries",
    "location",
    "downloads",
]


def _section(page: str, marker: str) -> str:
    """Return the section a marker opens, to its closing tag.

    Args:
        page: One of the card's pages.
        marker: An attribute that names the section.

    Returns:
        The section's markup.

    """
    section = page[page.index(marker) :]
    return section[: section.index("</section>")]


def _map(client: Client | None = None) -> str:
    """Return the map page's markup.

    Args:
        client: The client to fetch with; an anonymous one by default.

    Returns:
        The decoded page.

    """
    return (client or Client()).get(reverse("public:map")).content.decode()


def _card(content: str) -> str:
    """Return the legend card's markup, up to the date picker that follows it.

    Args:
        content: The map page.

    Returns:
        The card's markup.

    """
    card = content[content.index('id="map-legend-card"') :]
    return card[: card.index('id="map-calendar"')]


def _panel(content: str, name: str) -> str:
    """Return one of the card's two pages.

    Args:
        content: The map page.
        name: ``now`` or ``all``.

    Returns:
        The page's markup, to the start of the next one or the card's end.

    """
    card = _card(content)
    panel = card[card.index(f'data-legend-panel="{name}"') :]
    if name == "now":
        panel = panel[: panel.index('data-legend-panel="all"')]
    return panel


@pytest.fixture(autouse=True)
def _terrain_tiles(settings: Any) -> None:
    """Serve terrain-class tiles, which the two terrain keys are gated on."""
    settings.TERRAIN_CLASS_TILE_URL = TERRAIN_CLASS_URL


@pytest.fixture
def signed_in_drawer() -> Client:
    """Return a client signed in with the route_drawing flag on for it."""
    client = Client()
    client.force_login(UserFactory.create())
    return client


@pytest.mark.django_db
class TestTwoPages:
    """The card is two pages, and it opens on the first."""

    def test_the_card_opens_on_the_map_now(self) -> None:
        """The first page is shown, the second hidden, the card says which."""
        card = _card(_map())
        assert 'data-legend-page="now"' in card
        assert re.search(r'data-legend-panel="now"\s*>', card)
        assert re.search(r'data-legend-panel="all"\s+hidden', card)
        assert "On the map now" in _panel(_map(), "now")

    def test_each_page_leads_to_the_other(self) -> None:
        """The "All keys & sources" button goes on; the back button comes back."""
        content = _map()
        now = _panel(content, "now")
        assert 'data-legend-go="all"' in now
        assert "All keys & sources" in now
        back = _panel(content, "all")
        assert 'data-legend-go="now"' in back
        assert "aria-label='Back to on the map now'" in back


@pytest.mark.django_db
class TestFirstTier:
    """One key per layer, in order, compact, each switched by map_legend.js."""

    @override_flag("route_drawing", active=True)
    def test_every_key_is_there_in_order(self, signed_in_drawer: Client) -> None:
        """Every overlay has a key, once each, the day's first."""
        now = _panel(_map(signed_in_drawer), "now")
        found = re.findall(r'data-legend-key="([a-z_]+)"', now)
        assert found == KEYS

    def test_the_danger_scale_is_shown_once(self) -> None:
        """The strip is the scale; problem slopes say "same colours"."""
        now = _panel(_map(), "now")
        assert now.count("map-legend-strip ") + now.count('map-legend-strip"') == 1
        for level in (
            "low",
            "moderate",
            "considerable",
            "high",
            "very-high",
            "no-rating",
        ):
            assert f"map-legend-swatch--{level}" in now
        problem = now[now.index('data-legend-key="problem_terrain"') :]
        problem = problem[: problem.index("</section>")]
        assert "map-legend-swatch--terrain-" in problem
        assert "map-legend-swatch--terrain-very_high" not in problem
        assert "Same colours, on the slopes" in problem

    def test_the_rows_inside_a_key_are_view_tested(self) -> None:
        """Field-report families and states, and the pins, each name a row."""
        now = _panel(_map(), "now")
        for item in (
            "report-WARNING",
            "report-SURFACE",
            "report-TRAIL",
            "report-live",
            "report-recent",
            "pin-resort",
            "pin-pinned",
            "pin-favourite",
            "route-climb",
            "route-descent",
            "route-slope",
            "route-flat",
            "route-transition",
            "route-ends",
            "boundary-major",
            "boundary-minor",
            "boundary-micro",
            "boundary-unsupported",
            "boundary-grouping",
        ):
            assert f'data-legend-item="{item}"' in now, item

    @override_flag("route_drawing", active=True)
    def test_ski_tours_are_keyed_as_drawn(self, signed_in_drawer: Client) -> None:
        """A way up, a way down, a summit and a hut, each a mark of its own."""
        tours = _section(
            _panel(_map(signed_in_drawer), "now"), 'data-legend-key="ski_tours"'
        )
        for item, mark in (
            ("tour-up", "map-legend-line--tour-up"),
            ("tour-down", "map-legend-line--tour-down"),
            ("tour-summit", "map-legend-mark--summit"),
            ("tour-hut", "map-legend-mark--hut"),
        ):
            assert f'data-legend-item="{item}"' in tours
            assert mark in tours

    def test_weather_boundaries_and_position_are_keyed(self) -> None:
        """The weather symbol, the boundary tiers and the position dot."""
        now = _panel(_map(), "now")
        weather = _section(now, 'data-legend-key="weather"')
        assert "partly_cloudy-day.svg" in weather
        assert "Height of the point" in weather
        boundaries = _section(now, 'data-legend-key="boundaries"')
        for tier in ("major", "minor", "micro", "unsupported", "grouping"):
            assert f"map-legend-line--{tier}" in boundaries
        assert "map-legend-mark--location" in _section(
            now, 'data-legend-key="location"'
        )

    def test_it_says_so_when_nothing_needs_a_key(self) -> None:
        """An empty view gets a line, hidden until map_legend.js shows it."""
        now = _panel(_map(), "now")
        assert re.search(r"data-legend-empty\s+hidden", now)
        assert "Nothing in this view needs a key." in now

    def test_the_credit_line_and_no_long_attribution(self) -> None:
        """The first tier carries the one-line credit; the licences are page two."""
        content = _map()
        now = _panel(content, "now")
        assert "data-legend-sources" in now
        assert 'id="map-attribution-section"' not in now
        assert "Avalanche data" not in now
        everything = _panel(content, "all")
        assert 'id="map-attribution-section"' in everything
        assert "Avalanche data" in everything
        assert reverse("public:colophon") in everything


@pytest.mark.django_db
class TestRestZones:
    """The wildlife zones' key ships with the layer, behind route_drawing."""

    @override_flag("route_drawing", active=True)
    def test_four_rows_in_the_readers_words(self, signed_in_drawer: Client) -> None:
        """Hugo's labels: by law and advised, each in season and faint out of it."""
        now = _panel(_map(signed_in_drawer), "now")
        zones = now[now.index('data-legend-key="rest_zones"') :]
        zones = zones[: zones.index("</section>")]
        assert "Closed by law" in zones
        assert "Advised" in zones
        assert "Closed by law, out of season" in zones
        assert "Advised, out of season" in zones
        for kind in (
            "binding",
            "recommended",
            "binding-out-of-season",
            "recommended-out-of-season",
        ):
            assert f'data-legend-item="rest-{kind}"' in zones
            assert f"map-legend-swatch--rest-{kind}" in zones

    def test_no_key_without_the_layer(self) -> None:
        """Anonymous, the flag is never asked, and neither page names the zones."""
        card = _card(_map())
        assert 'data-legend-key="rest_zones"' not in card
        assert 'data-legend-on="rest_zones"' not in card


@pytest.mark.django_db
class TestSecondTier:
    """Every key short, the "On" marks, and the way to the full descriptions."""

    @override_flag("route_drawing", active=True)
    def test_every_key_has_an_on_mark(self, signed_in_drawer: Client) -> None:
        """One hidden "On" per first-tier key, for map_legend.js to show."""
        everything = _panel(_map(signed_in_drawer), "all")
        marks = re.findall(r'data-legend-on="([a-z_]+)"\s+hidden', everything)
        assert marks == KEYS

    def test_the_full_scale_and_the_help_topic(self) -> None:
        """The danger scale in words, and a link to the Map key topic."""
        everything = _panel(_map(), "all")
        for label in (
            "Low (1)",
            "Moderate (2)",
            "Considerable (3)",
            "High (4)",
            "Very high (5)",
        ):
            assert label in everything
        assert "#help-topic-map-key" in everything

    @override_flag("route_drawing", active=True)
    def test_every_key_is_drawn_not_described(self, signed_in_drawer: Client) -> None:
        """Hugo: "the entries must be visual" — each key is its marks, no prose.

        Every section of the second page but the sources carries a swatch,
        a line sample or a mark, and none carries a paragraph.
        """
        everything = _panel(_map(signed_in_drawer), "all")
        keys = everything[: everything.index("map-legend-help-link")]
        sections = keys.split('<section class="map-legend-section">')[1:]
        assert len(sections) == len(KEYS)
        for section in sections:
            assert re.search(r"map-legend-(swatch|line|mark|pin|icon)", section), (
                section[:200]
            )
            assert "map-legend-attribution" not in section, section[:200]

    def test_the_second_page_rows_are_not_view_tested(self) -> None:
        """Page two shows every row: it carries no data-legend-item, no test ids."""
        everything = _panel(_map(), "all")
        assert "data-legend-item" not in everything
        assert 'data-testid="map-legend-observations-' not in everything
        assert "data-terrain-filter-key" not in everything


@pytest.mark.django_db
class TestMapDemoMoved:
    """Show map demo is help, not information: it is in the "?" menu."""

    def test_the_demo_is_in_the_help_menu_not_the_card(self) -> None:
        """The link keeps its id (map.js keeps its ?d= in step)."""
        content = _map()
        assert "Show map demo" not in _card(content)
        menu = content[content.index('id="map-help-menu"') :]
        menu = menu[: menu.index("</ul>")]
        assert 'id="map-explainer-link"' in menu
        assert 'data-help-menu="demo"' in menu
        assert "Show map demo" in menu


@pytest.mark.django_db
class TestMapKeyHelpTopic:
    """The legend's long form is a help topic of its own."""

    def test_the_topic_describes_every_key(self) -> None:
        """One paragraph per key, rest zones' three states among them."""
        content = Client().get(reverse("public:help")).content.decode()
        assert 'id="help-topic-map-key"' in content
        for part in (
            "tiers",
            "danger",
            "problem-terrain",
            "field-reports",
            "weather",
            "steep-terrain",
            "ski-tours",
            "rest-zones",
            "routes",
            "pins",
            "boundaries",
            "location",
            "downloads",
        ):
            assert f'data-testid="help-map-key-{part}"' in content, part
        assert "Closed by law, out of season" in content
        assert "Advised, out of season" in content
