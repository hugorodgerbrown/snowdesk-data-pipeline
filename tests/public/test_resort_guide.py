"""
tests/public/test_resort_guide.py — the "Find a resort" walkthrough's server
half (SNOW-1231).

Covers:
  - popular_resorts() lists the popular slugs that have a pin, in
    POPULAR_RESORT_SLUGS order, with their region's id and name; a missing
    or unpinned resort is left out.
  - guide_date() keeps the map's day in season, and out of season moves it
    to the example's day, or the default example day with none set.
  - The map page renders the guide (#map-resort-guide with its day, the
    bar, the pill, the finish card, the strings template) and the popular
    resorts as JSON, and the finish card's pin is a favourites star for a
    signed-in visitor and a sign-in link otherwise.
  - The map's context carries both values, the day read from the example
    share in the same query that answers the welcome card's link.
"""

from __future__ import annotations

import datetime
import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.public import resort_guide
from apps.routes.services.example import DEFAULT_EXAMPLE_DATE
from tests.factories import (
    MicroRegionFactory,
    ResortFactory,
    RouteShareFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _resort(
    name: str, region_id: str, region_name: str, *, pinned: bool = True
) -> None:
    """Create a resort named ``name`` (slug from the name) in a new region."""
    region = MicroRegionFactory.create(region_id=region_id, name=region_name)
    ResortFactory.create(name=name, region=region, geocoded=pinned)


class TestPopularResorts:
    """popular_resorts() — the rows search lists before anything is typed."""

    def test_lists_the_popular_resorts_in_their_order(self) -> None:
        """Each row names the resort and its region, in the curated order."""
        _resort("Zermatt", "CH-4222", "Zermatt")
        _resort("Verbier", "CH-4115", "Martigny-Verbier")
        _resort("Elsewhere", "CH-9999", "Nowhere")

        assert resort_guide.popular_resorts() == [
            {
                "slug": "verbier",
                "name": "Verbier",
                "region_id": "CH-4115",
                "region_name": "Martigny-Verbier",
            },
            {
                "slug": "zermatt",
                "name": "Zermatt",
                "region_id": "CH-4222",
                "region_name": "Zermatt",
            },
        ]

    def test_leaves_out_a_resort_with_no_pin(self) -> None:
        """A row opens the resort's pin, so a resort without one is not listed."""
        _resort("Davos", "CH-5123", "Davos", pinned=False)

        assert resort_guide.popular_resorts() == []

    def test_is_one_query(self, django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]  # pytest-django fixture
        """The resorts and their regions arrive together."""
        _resort("Verbier", "CH-4115", "Martigny-Verbier")
        _resort("Davos", "CH-5123", "Davos")

        with django_assert_num_queries(1):
            resort_guide.popular_resorts()


class TestGuideDate:
    """guide_date() — the day the guide moves the map to."""

    def test_keeps_the_maps_day_in_season(self) -> None:
        """In season there is a bulletin to read today."""
        assert resort_guide.guide_date(False, datetime.date(2026, 4, 10)) is None

    def test_moves_to_the_examples_day_out_of_season(self) -> None:
        """Out of season it borrows the example route's day."""
        day = datetime.date(2026, 3, 2)
        assert resort_guide.guide_date(True, day) == day

    def test_falls_back_to_the_default_example_day(self) -> None:
        """With no example set, the default example day still has a bulletin."""
        assert resort_guide.guide_date(True, None) == DEFAULT_EXAMPLE_DATE


class TestMapPage:
    """The map page renders the guide and gives it its data."""

    def test_renders_the_guide(self) -> None:
        """Every surface of the guide is on the page, hidden, with its strings."""
        content = Client().get(reverse("public:map")).content.decode()

        assert re.search(r'<div\s+id="map-resort-guide"\s+data-guide-date="', content)
        for element_id in (
            "map-resort-guide-bar",
            "map-resort-guide-pill",
            "map-resort-guide-finish",
            "map-resort-guide-strings-template",
        ):
            assert f'id="{element_id}"' in content
        assert "js/map_resort_guide.js" in content
        assert "js/resort_guide_core.js" in content

    def test_renders_the_popular_resorts_as_json(self) -> None:
        """The popular resorts reach the script as a json_script block."""
        _resort("Verbier", "CH-4115", "Martigny-Verbier")
        content = Client().get(reverse("public:map")).content.decode()

        match = re.search(
            r'<script id="map-resort-guide-popular" type="application/json">(.*?)</script>',
            content,
            re.DOTALL,
        )
        assert match is not None
        assert json.loads(match.group(1))[0]["slug"] == "verbier"

    def test_context_carries_the_guide_day_from_the_example(self) -> None:
        """The day comes from the example share, as guide_date() decides it."""
        share = RouteShareFactory.create(example=True)
        response = Client().get(reverse("public:map"))

        assert response.context["resort_guide_date"] == resort_guide.guide_date(
            response.context["is_offseason"], share.example_date
        )
        assert response.context["example_route_url"] == reverse("routes:example")

    def test_anonymous_finish_card_signs_in_to_pin(self) -> None:
        """A signed-out visitor is offered sign-in, not a star."""
        content = Client().get(reverse("public:map")).content.decode()

        assert "data-resort-guide-signin" in content
        assert "data-resort-guide-pin" not in content

    def test_signed_in_finish_card_carries_a_star(self) -> None:
        """A signed-in visitor's pin is a favourites.js resort star."""
        client = Client()
        client.force_login(UserFactory.create())
        content = client.get(reverse("public:map")).content.decode()

        assert re.search(r"data-resort-star\s+data-resort-guide-pin", content)
        assert "data-resort-guide-signin" not in content
