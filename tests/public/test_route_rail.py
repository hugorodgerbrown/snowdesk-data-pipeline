"""
tests/public/test_route_rail.py — rail one as the map page ships it (SNOW-1018).

What the page carries before any route is open: the rail itself, hidden and
inside ``#map``, its eyebrow, its strings template, its actions as ONE
``[data-overflow-menu]`` rather than loose icons (design-system rule 5) —
Terrain and bulletin first, then the routes row's four in its order — its
own × close, a pending share's claim slot, and the three scripts that fill
it. What the rail
does once open is tests/js/test_route_rail.js's.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.routes.models import Route
from tests.factories import UserFactory

# The rail's own markup: from its opening tag to the end of the section.
_RAIL_RE = re.compile(r'<section\s+id="route-rail".*?</section>', re.S)
# The overflow menu's item list inside it.
_MENU_RE = re.compile(r'<ul\s+id="route-rail-menu".*?</ul>', re.S)


def _home(client: Client) -> str:
    """Render the homepage and return its decoded body.

    Args:
        client: The Django test client.

    Returns:
        The page's HTML.

    """
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    return response.content.decode()


def _rail(body: str) -> str:
    """Return the rail section's markup, failing if it is absent.

    Args:
        body: A rendered page or partial.

    Returns:
        The ``#route-rail`` section's HTML.

    """
    match = _RAIL_RE.search(body)
    assert match is not None
    return match.group(0)


def _opening_tag(rail: str) -> str:
    """Return the rail's own opening tag.

    Args:
        rail: The section's HTML.

    Returns:
        Everything up to the first ``>``.

    """
    return rail[: rail.index(">") + 1]


@pytest.mark.django_db
class TestTheRailShipsWithTheMap:
    """The rail is on the map page, hidden, for every visitor."""

    def test_the_rail_is_inside_the_map_and_hidden(self, client: Client) -> None:
        """Inside #map, so a press on it is not a click outside the sheet.

        The first toast after the map's closing tag is the marker for
        "after #map": the rail has to sit before it.
        """
        page = _home(client)
        rail_at = page.index('id="route-rail"')

        assert re.search(r"\shidden\s", _opening_tag(_rail(page)))
        assert (
            page.index('id="map"')
            < rail_at
            < page.index('id="map-offline-toast-layer"')
        )

    def test_it_carries_the_eyebrow(self, client: Client) -> None:
        """The identity block opens with the ``Route profile`` eyebrow."""
        rail = _rail(_home(client))

        assert re.search(r'id="route-rail-eyebrow"\s*>\s*Route profile\s*<', rail)

    def test_it_carries_its_strings_template(self, client: Client) -> None:
        """Every user-facing string route_rail.js writes comes from here."""
        rail = _rail(_home(client))

        assert '<template id="route-rail-strings-template">' in rail
        keys = set(re.findall(r'data-string="([^"]+)"', rail))
        assert {"leg-climb", "leg-descent", "figure-distance", "unit-km"} <= keys

    def test_the_endpoints_are_templated_on_the_uuid(self, client: Client) -> None:
        """The script addresses whichever route is open by substitution."""
        tag = _opening_tag(_rail(_home(client)))

        for attribute in (
            "data-route-rename-url-template",
            "data-route-share-url-template",
            "data-route-delete-url-template",
        ):
            value = re.search(rf'{attribute}="([^"]*)"', tag)
            assert value is not None
            assert "__UUID__" in value.group(1)
        assert f'data-route-plan-trip-url="{reverse("trips:new")}"' in tag

    def test_the_scripts_load_cursor_before_core_before_rail(
        self, client: Client
    ) -> None:
        """The rail reads both cores, so both come first."""
        page = _home(client)
        order = [
            page.index(f"js/{name}")
            for name in ("route_cursor_core.js", "route_rail_core.js", "route_rail.js")
        ]

        assert order == sorted(order)


@pytest.mark.django_db
class TestTheActionsAreAMenu:
    """Four actions in a restricted area collapse into one "…" menu."""

    def test_the_actions_are_one_overflow_menu(self, client: Client) -> None:
        """One menu, and every action inside it — none loose beside it."""
        client.force_login(UserFactory.create())
        rail = _rail(_home(client))
        menu = _MENU_RE.search(rail)

        assert rail.count("data-overflow-menu") == 1
        assert menu is not None
        outside = rail.replace(menu.group(0), "")
        for hook in (
            "data-route-rail-details",
            "data-route-rail-plan-trip",
            "data-route-rail-share",
            "data-route-rename",
            "data-route-rail-delete",
        ):
            # Followed by `=`, space or `>`, so the section's own
            # ``data-route-rename-url-template`` is not read as the item.
            exact = re.compile(rf"{hook}(?=[\s=>])")
            assert exact.search(menu.group(0))
            assert not exact.search(outside)

    def test_the_order_leads_with_details_then_the_routes_rows(
        self, client: Client
    ) -> None:
        """Terrain and bulletin → Plan a trip → Share → Rename → Delete."""
        menu = _MENU_RE.search(_rail(_home(client)))
        assert menu is not None

        positions = [
            menu.group(0).index(label)
            for label in (
                "Terrain and bulletin",
                "Plan a trip",
                "Share",
                "Rename",
                "Delete",
            )
        ]
        assert positions == sorted(positions)

    def test_every_owner_item_is_marked_for_a_pending_share_to_hide(
        self, client: Client
    ) -> None:
        """The details item alone is unmarked: a pending share keeps it."""
        menu = _MENU_RE.search(_rail(_home(client)))
        assert menu is not None

        items = re.findall(r"<li\b[^>]*>", menu.group(0))
        marked = [item for item in items if "data-route-rail-owner" in item]
        assert len(items) - len(marked) == 1

    def test_the_rail_has_its_own_close_outside_the_menu(self, client: Client) -> None:
        """A bare × with a translated name, not an item inside the menu."""
        rail = _rail(_home(client))
        menu = _MENU_RE.search(rail)
        assert menu is not None

        close = re.search(r"<button[^>]*data-route-rail-close[^>]*>", rail)
        assert close is not None
        assert 'aria-label="Close the route profile"' in close.group(0)
        assert "data-route-rail-close" not in menu.group(0)

    def test_the_claim_slot_ships_hidden(self, client: Client) -> None:
        """Filled and shown by route_rail.js for a pending share only."""
        rail = _rail(_home(client))

        assert re.search(r"<div\s+data-route-rail-claim\s+hidden", rail)

    def test_the_rename_input_is_capped_at_the_name_column(
        self, client: Client
    ) -> None:
        """routes:rename answers 400 past Route.name's max_length."""
        rail = _rail(_home(client))
        field = Route._meta.get_field("name")

        tag = re.search(r"<input[^>]*data-row-rename-input[^>]*>", rail)
        assert tag is not None
        assert f'maxlength="{field.max_length}"' in tag.group(0)
        assert field.max_length == 100

    def test_no_htmx_attribute_ships_in_the_rail(self, client: Client) -> None:
        """Delete is a fetch, so the page's htmx pairing is unchanged."""
        assert " hx-" not in _rail(_home(client))


class TestTheComponentLibraryVariant:
    """The ``static`` variant renders visible, for /_components/."""

    def test_static_drops_hidden_and_the_docked_position(self) -> None:
        """No JS runs in the library to reveal the rail."""
        html = render_to_string(
            "includes/_route_rail.html",
            {
                "route_rename_url_template": "/r/__UUID__/",
                "route_share_url_template": "/s/__UUID__/",
                "route_delete_url_template": "/d/__UUID__/",
                "static": True,
            },
        )
        tag = _opening_tag(_rail(html))

        assert not re.search(r"\shidden\s", tag)
        assert 'class="grid' in tag
