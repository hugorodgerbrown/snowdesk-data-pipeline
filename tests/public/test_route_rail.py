"""
tests/public/test_route_rail.py — the route rail as the map page ships it
(SNOW-1018; one rail since SNOW-1065).

What the page carries before any route is open: the rail itself, hidden and
inside ``#map``, one column — the name, the meta line, the profile — its
eyebrow, its strings template (the meta line's strings byte for byte the
routes panel's, so one route reads the same in both), its actions as ONE
``[data-overflow-menu]`` rather than loose icons (design-system rule 5) —
Terrain first, then the routes row's four in its order — its own × close,
a pending share's claim slot, no trace of rail two or its staff debug rail,
and the scripts that fill it, in order. What the rail does once open is
tests/js/test_route_rail.js's.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.routes.models import Route
from tests.factories import RouteFactory, UserFactory

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
    response = client.get(reverse("public:map"))
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
        """The identity block opens with the ``Route`` eyebrow (SNOW-1045)."""
        rail = _rail(_home(client))

        assert re.search(r'id="route-rail-eyebrow"\s*>\s*Route\s*<', rail)

    def test_it_is_one_column_name_meta_then_profile(self, client: Client) -> None:
        """SNOW-1065: one layout at every width — title, subtitle, lane."""
        rail = _rail(_home(client))

        assert "sm:grid-cols-" not in _opening_tag(rail)
        assert (
            rail.index("data-route-rail-name")
            < rail.index("data-route-rail-leg")
            < rail.index("data-route-rail-meta")
            < rail.index("data-route-rail-lane")
        )
        # The two figure lines SNOW-1045 gave it, and their steep figure.
        assert "data-route-rail-vertical" not in rail
        assert "data-route-rail-horizontal" not in rail

    def test_it_carries_its_strings_template(self, client: Client) -> None:
        """Every user-facing string route_rail.js writes comes from here."""
        rail = _rail(_home(client))
        block = re.search(
            r'<template id="route-rail-strings-template">(.*?)</template>',
            rail,
            re.S,
        )
        assert block is not None
        keys = set(re.findall(r'data-string="([^"]+)"', block.group(1)))

        assert {
            "leg-climb",
            "leg-descent",
            "leg-suffix",
            "unit-km",
            "meta-km",
            "meta-both",
            "meta-ascent",
            "meta-descent",
            "meta-hm",
            "meta-m",
            "meta-duration",
        } <= keys
        assert keys.isdisjoint(
            {"route-ascend", "route-descend", "route-length", "route-steep"}
        )

    def test_its_meta_strings_are_the_routes_panels(self, client: Client) -> None:
        """The rail's subtitle matches the routes list's line for a route.

        The rail formats in JavaScript from the strings rendered here; the
        panel renders server-side with the same msgids. Each rail string,
        with its placeholders filled from a route's own figures, must read
        exactly as the panel's row does for that route.
        """
        rail = _rail(_home(client))
        strings = {
            key: " ".join(value.split())
            for key, value in re.findall(
                r'data-string="(meta-[^"]+)"\s*>(.*?)</span>', rail, re.S
            )
        }
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            distance_m=12900,
            ascent_m=337,
            descent_m=1906,
            started_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
            finished_at=datetime(2026, 2, 1, 10, 51, tzinfo=UTC),
        )
        ascent, descent = route.climb
        figures = strings["meta-both"] % {
            "km": "12.9",
            "ascent": f"{ascent:.0f}",
            "descent": f"{descent:.0f}",
        }
        expected = strings["meta-duration"] % {
            "figures": figures,
            "duration": strings["meta-hm"] % {"hours": "2", "minutes": "51"},
        }
        row = render_to_string("routes/partials/_route.html", {"route": route})

        assert expected == f"12.9km · {ascent:.0f}m ↑ · {descent:.0f}m ↓ · 2h51m"
        assert expected in " ".join(row.split())

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

    def test_the_scripts_load_cursor_then_cores_then_rail(self, client: Client) -> None:
        """The rail reads its cores and the point card's, which load first."""
        page = _home(client)
        order = [
            page.index(f"js/{name}")
            for name in (
                "route_cursor_core.js",
                "route_rail_core.js",
                "route_point_card.js",
                "route_rail.js",
                "route_leader_core.js",
                "route_leader.js",
            )
        ]

        assert order == sorted(order)
        for gone in (
            "bank_ribbon_core.js",
            "route_rail_two_core.js",
            "route_rail_two.js",
        ):
            assert f"js/{gone}" not in page


@pytest.mark.django_db
class TestRailTwoIsGone:
    """SNOW-1065 retired rail two and the staff debug rail drawn in it."""

    @pytest.mark.parametrize("staff", [False, True])
    def test_no_rail_two_markup_for_anyone(self, client: Client, staff: bool) -> None:
        """No row, no strings template, no debug fields — staff included."""
        client.force_login(UserFactory.create(is_staff=staff))
        rail = _rail(_home(client))

        assert "data-route-rail-two" not in rail
        assert "route-rail-two-strings-template" not in rail
        assert "data-route-rail-debug" not in rail
        assert "data-route-rail-window" not in rail

    def test_the_staff_terrain_json_stays(self, client: Client) -> None:
        """The server half of the debug rail is kept for later use."""
        client.force_login(UserFactory.create(is_staff=True))
        route = RouteFactory.create()

        response = client.get(
            reverse("public:route_terrain", kwargs={"route_uuid": route.uuid})
            + "?format=json"
        )

        assert response.status_code == 200
        assert "rows" in response.json()


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
        """Terrain → Plan a trip → Share → Rename → Delete."""
        menu = _MENU_RE.search(_rail(_home(client)))
        assert menu is not None

        positions = [
            menu.group(0).index(label)
            for label in (
                "Terrain",
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


# SNOW-1019: the floating map controls withdrawn while a route is open.
_MAP_CSS = Path(__file__).resolve().parents[2] / "static" / "css" / "map.css"
_WITHDRAWN = (
    "season-ribbon",
    "map-utility-cluster",
    "map-legend",
    "map-controls-br",
    "home-intro",
)


def _withdrawn_selectors() -> set[str]:
    """The ids the rail-open rule hides, read off the stylesheet.

    Returns:
        Every ``#id`` in a ``#map[data-route-rail-open] #id`` selector of a
        rule that sets ``visibility: hidden``.

    """
    css = _MAP_CSS.read_text(encoding="utf-8")
    ids: set[str] = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "visibility: hidden" not in body:
            continue
        ids.update(re.findall(r"#map\[data-route-rail-open\]\s+#([\w-]+)", selectors))
    return ids


class TestTheMapControlsWithdrawWhileARouteIsOpen:
    """Following a route is the one thing the reader is doing."""

    def test_every_floating_container_is_withdrawn(self) -> None:
        """The stylesheet hides all five while the rail is open."""
        assert set(_WITHDRAWN) <= _withdrawn_selectors()

    def test_the_containers_exist_in_the_map_partials(self) -> None:
        """A renamed container would leave its controls over the rail.

        Read from the templates rather than a rendered page: the season
        ribbon and the intro card are conditional, and the test database
        has no season for the ribbon to render.
        """
        partials = Path(__file__).resolve().parents[2] / "apps" / "public" / "templates"
        source = "".join(
            (partials / "public" / "partials" / name).read_text(encoding="utf-8")
            for name in ("_map_embed.html", "_season_ribbon.html")
        )

        for container in _WITHDRAWN:
            assert re.search(rf'id="{container}"', source), container

    def test_the_rail_its_leader_and_the_route_sheet_stay(self) -> None:
        """What the reader is using now is never among the hidden."""
        hidden = _withdrawn_selectors()

        for kept in ("route-rail", "route-detail-sheet", "map"):
            assert kept not in hidden, kept
        css = _MAP_CSS.read_text(encoding="utf-8")
        assert "[data-route-rail-open] .route-leader" not in css
        assert "[data-route-rail-open] .route-rail" not in css

    def test_the_rail_shares_the_point_cards_column(self) -> None:
        """One left-aligned column, one width token, for both (SNOW-1065)."""
        css = _MAP_CSS.read_text(encoding="utf-8")
        rules = dict(
            (sel.split("*/")[-1].strip(), body)
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        )

        for selector in (".route-rail", ".route-point-card"):
            assert "width: var(--route-column-width)" in rules[selector], selector
        assert "right:" not in rules[".route-rail"]
        assert "touch-action: pan-y" in rules[".route-rail-lane"]


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
