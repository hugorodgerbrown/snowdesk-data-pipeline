"""
tests/e2e/test_favourites_problems.py — Playwright test for the SNOW-422
elevation-aware avalanche-problem highlighting on the favourite detail card.

Follows ``test_favourites.py``'s SNOW-417 forecast-panel pattern: opens the
manage page's "My favourites" list, clicks "Details" to hx-get the detail
card into ``#favourite-card-panel``, and asserts the problems section (one
``rating-block`` per problem card plus an altitude-relevance chip) renders
there.

Uses the ``favourites_page`` fixture (no real service-worker lifecycle
needed for this surface).
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from waffle.testutils import override_flag

from bulletins.services.render_model import RENDER_MODEL_VERSION
from tests.e2e.conftest import FavouritesPage
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)


def _render_model_with_one_problem(elevation: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal current-version render_model dict with one dry problem."""
    problem = {
        "problem_type": "wind_slab",
        "comment_html": "<p>Wind slab comment text.</p>",
        "aspects": ["N", "NE", "E"],
        "elevation": elevation,
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "moderate",
    }
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "slf",
        "danger": {
            "key": "moderate",
            "number": "2",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [problem],
                "prose": None,
                "danger_level": 2,
            }
        ],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": "<p>The snowpack is generally stable.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_problems_section_renders_altitude_chip(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """The favourite card's problems section renders a card + altitude chip.

    Builds a favourite at the same altitude as a bulletin problem's lower
    bound, so the expected verdict is ``APPLIES`` — the copy is
    altitude-relative, never a safety verdict.
    """
    with django_db_blocker.unblock():
        region = MicroRegionFactory.create()
        today = datetime.datetime.now(tz=datetime.UTC).date()
        vf = datetime.datetime.combine(today, datetime.time.min, tzinfo=datetime.UTC)
        vt = vf + datetime.timedelta(days=1)
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model=_render_model_with_one_problem(
                {"lower": 2000, "upper": None, "treeline": False}
            ),
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin, region=region, region_name_at_time=region.name
        )
        FavouriteFactory.create(
            user=favourites_page.subscriber.user,
            name="High Pin",
            region=region,
            elevation=2500.0,
        )

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}/account/manage/")
    page.wait_for_selector('[data-testid="favourite-list-row"]')

    page.click('[data-testid="favourite-list-row"] >> text=Details')
    page.wait_for_selector('[data-testid="rating-block"]')

    chip = page.locator('[data-testid="altitude-relevance-chip"]')
    chip.wait_for(state="visible")
    assert chip.get_attribute("data-relevance") == "APPLIES"

    card_text = page.locator('[data-testid="favourite-card"]').inner_text().lower()
    assert "safe" not in card_text
