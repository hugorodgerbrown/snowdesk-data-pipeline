"""
tests/public/test_route_point_card.py — the point header's partial, its
place in the route panel and its registry entry (SNOW-1064, SNOW-1068).

The partial renders hidden with every hook route_point_card.js reads and a
strings template carrying every word the header says; the map page renders
it inside the route panel's identity block, between the title and the
meta line it replaces, and loads its scripts before the rail's, which
attaches it; the registry at /_components/ shows every headline family.
What the header says for a point is tests/js/test_route_point_card_core.js's.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.public._component_fixtures import POINT_CARD_VARIANTS
from tests.factories import UserFactory


@pytest.fixture()
def staff_client(db: Any) -> Client:
    """Return a logged-in staff client carrying the HX-Request header."""
    user: User = UserFactory.create(is_staff=True)
    client = Client()
    client.force_login(user)
    client.defaults["HTTP_HX_REQUEST"] = "true"
    return client


def test_the_partial_renders_hidden_with_every_hook() -> None:
    """With no context the header is the map's: hidden, every hook."""
    body = render_to_string("includes/_route_point_card.html", {})
    assert 'id="route-point-card"' in body
    assert re.search(r"<div[^>]*\sdata-route-point-card[^>]*\shidden[\s>]", body)
    for hook in (
        "data-route-point-card-wheel",
        "data-route-point-card-headline",
        "data-route-point-card-ground",
    ):
        assert hook in body
    # SNOW-1068: no card chrome and no × of its own — the panel has both.
    assert "rounded-card" not in body.split("<template")[0]
    assert "_icon_close" not in body and "<svg" not in body.split("<template")[0]


def test_the_wheel_is_the_button_that_clears_the_point() -> None:
    """2026-10-02: pressing the wheel clears the point and keeps the route."""
    body = render_to_string("includes/_route_point_card.html", {})
    button = re.search(
        r"<button[^>]*data-route-point-card-clear[^>]*>(.*?)</button>", body, re.S
    )
    assert button is not None
    assert 'type="button"' in button.group(0)
    assert 'aria-label="Clear the point"' in button.group(0)
    assert "data-route-point-card-wheel" in button.group(1)


def test_the_partial_carries_every_word_in_its_strings_template() -> None:
    """Every key route_point_card.js reads renders under the template id."""
    body = render_to_string("includes/_route_point_card.html", {})
    assert '<template id="route-point-card-strings-template">' in body
    keys = (
        ["label", "join", "label-join", "heading-pair", "label-heading-pair"]
        + [f"compass-{n}" for n in range(8)]
        + [
            f"steepness-{s}"
            for s in ("level", "gentle", "moderate", "steep", "very-steep")
        ]
        + [
            f"kind-{k}"
            for k in (
                "ascent",
                "descent",
                "traverse",
                "fall-line",
                "turn",
                "switchback",
            )
        ]
        + ["headline-no-height"]
        + [
            f"ground-{g}"
            for g in (
                "flat",
                "moderate",
                "steep",
                "very-steep",
                "extremely-steep",
                "unknown",
                "falling-left",
                "falling-right",
                "falling-left-then-right",
                "falling-right-then-left",
            )
        ]
    )
    for key in keys:
        assert f'data-string="{key}"' in body, key


def test_a_filled_card_shows_its_two_lines() -> None:
    """The library's filled state renders the lines and a wheel host."""
    body = render_to_string(
        "includes/_route_point_card.html",
        {
            "static": True,
            "state_json": json.dumps({"track": [2], "gradeDeg": -37}),
            "headline": "E • Very steep • fall line",
            "ground": "Extremely steep slope",
        },
    )
    assert 'id="route-point-card"' not in body
    assert "E • Very steep • fall line" in body
    assert "Extremely steep slope" in body
    assert "data-aspect-wheel" in body


@pytest.mark.django_db
def test_the_map_page_renders_the_header_inside_the_panel() -> None:
    """The header sits in the panel's identity block, and its scripts load first."""
    body = Client().get(reverse("public:map")).content.decode()
    assert body.count('id="route-point-card"') == 1
    panel = body.split('id="route-rail"', 1)[1].split("</section>", 1)[0]
    assert panel.index("data-route-rail-title") < panel.index('id="route-point-card"')
    assert panel.index('id="route-point-card"') < panel.index("data-route-rail-meta")


@pytest.mark.django_db
def test_the_header_spans_the_panel_below_the_buttons() -> None:
    """The header follows the buttons' row, not beside them (SNOW-1069).

    In the eyebrow's column it stopped at the buttons' left edge, and
    ``text-balance`` split a long ground line into two even halves.
    """
    body = Client().get(reverse("public:map")).content.decode()
    panel = body.split('id="route-rail"', 1)[1].split("</section>", 1)[0]
    assert panel.index("data-route-rail-close") < panel.index('id="route-point-card"')
    positions = [
        body.index("js/aspect_wheel_core"),
        body.index("js/route_point_card_core"),
        body.index("js/route_point_card."),
        body.index("js/route_rail."),
    ]
    assert positions == sorted(positions)


def test_the_fixtures_cover_every_family() -> None:
    """Each kind, a turning heading, a side, both sides, flat and no terrain."""
    headlines = [v["context"]["headline"] for v in POINT_CARD_VARIANTS]
    assert all(headlines)
    joined = " | ".join(headlines)
    for phrase in (
        "• fall line",
        "• traverse",
        "• turn",
        "• switchback",
        "• ascent",
        "• descent",
        "Level • traverse",
        "• Level",
        " → ",
    ):
        assert phrase in joined, phrase
    grounds = {v["context"]["ground"] for v in POINT_CARD_VARIANTS}
    assert {"Flat ground", "No terrain data"} <= grounds
    assert any(g.endswith("falling skier's left") for g in grounds)
    assert any(", then " in g for g in grounds)


def test_the_registry_panel_renders_every_state(staff_client: Client) -> None:
    """The panel renders each variant's card in each theme."""
    response = staff_client.get(
        reverse("public:components_panel", kwargs={"slug": "point-card"})
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("data-route-point-card-headline") == 2 * len(POINT_CARD_VARIANTS)
    assert "S → NE • Gentle • switchback" in body
