"""
tests/public/test_route_point_card.py — the point card's partial, its place
on the map page and its registry entry (SNOW-1064).

The partial renders empty and hidden with every hook route_point_card.js
reads and a strings template carrying every word the card says; the map
page renders it inside #map beside the rail and loads its scripts before
the rail's, which attaches it; the registry at /_components/ shows the
empty card and every headline family. What the card says for a point is
tests/js/test_route_point_card_core.js's.
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


def test_the_partial_renders_empty_and_hidden() -> None:
    """With no context the card is the map's: hidden, empty, every hook."""
    body = render_to_string("includes/_route_point_card.html", {})
    assert 'id="route-point-card"' in body
    assert re.search(r"<section[^>]*\shidden[\s>]", body)
    assert "data-empty" in body
    for hook in (
        "data-route-point-card-wheel",
        "data-route-point-card-headline",
        "data-route-point-card-ground",
        "data-route-point-card-clear",
    ):
        assert hook in body
    assert "Select a point on the route to view terrain data" in body


def test_the_partial_carries_every_word_in_its_strings_template() -> None:
    """Every key route_point_card.js reads renders under the template id."""
    body = render_to_string("includes/_route_point_card.html", {})
    assert '<template id="route-point-card-strings-template">' in body
    keys = (
        ["empty", "label", "label-empty"]
        + [f"steepness-{s}" for s in ("gentle", "moderate", "steep", "very-steep")]
        + [
            f"headline-{h}"
            for h in (
                "fall-descent",
                "fall-climb",
                "rising-traverse",
                "descending-traverse",
                "level-traverse",
                "climb-turning",
                "descent-turning",
                "climb",
                "descent",
                "level",
                "no-height",
            )
        ]
        + [
            f"ground-{g}"
            for g in (
                "flat",
                "moderate",
                "steep",
                "very-steep",
                "extremely-steep",
                "unknown",
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
            "headline": "Very steep fall line descent",
            "ground": "Extremely steep slope",
        },
    )
    assert 'id="route-point-card"' not in body
    assert "Very steep fall line descent" in body
    assert "Extremely steep slope" in body
    assert "data-aspect-wheel" in body
    assert "Select a point on the route" not in body.split("<template")[0]


@pytest.mark.django_db
def test_the_map_page_renders_the_card_inside_the_map() -> None:
    """The card sits in #map after the rail, and its scripts load first."""
    body = Client().get(reverse("public:map")).content.decode()
    assert body.index('id="route-rail"') < body.index('id="route-point-card"')
    positions = [
        body.index("js/aspect_wheel_core"),
        body.index("js/route_point_card_core"),
        body.index("js/route_point_card."),
        body.index("js/route_rail."),
    ]
    assert positions == sorted(positions)


def test_the_fixtures_cover_the_empty_card_and_every_family() -> None:
    """Empty, each headline family, flat ground and no terrain data."""
    headlines = [v["context"]["headline"] for v in POINT_CARD_VARIANTS]
    assert headlines[0] == ""
    joined = " | ".join(headlines)
    for phrase in (
        "fall line descent",
        "fall line climb",
        "rising traverse",
        "descending traverse",
        "Level traverse",
        "turning",
        "Gentle descent",
        "Level track",
    ):
        assert phrase in joined, phrase
    grounds = {v["context"]["ground"] for v in POINT_CARD_VARIANTS}
    assert {"Flat ground", "No terrain data"} <= grounds


def test_the_registry_panel_renders_every_state(staff_client: Client) -> None:
    """The panel renders each variant's card in each theme."""
    response = staff_client.get(
        reverse("public:components_panel", kwargs={"slug": "point-card"})
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("data-route-point-card-headline") == 2 * len(POINT_CARD_VARIANTS)
    assert "Very steep fall line descent" in body
