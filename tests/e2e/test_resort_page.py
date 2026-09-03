"""tests/e2e/test_resort_page.py — A user opens a resort and saves it.

Smoke tests — the resort page's user journeys, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

The htmx journey below earns a browser because SNOW-650's second defect was
the *absence* of a script: the page shipped with hx-post on its favourite
toggle while ``window.htmx`` stayed undefined, so the control was inert from
the day it landed. No Python test can observe a missing script tag from the
outside. tests/public/test_htmx_pages.py is the cheap structural guard; this
is the proof that the control actually fires.

Scenario: none — the resort page has no manual scenario yet; add one and cite it here
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.regions.models import Resort
from tests.e2e.conftest import FavouritesPage


def test_resort_to_bulletin_to_map_lands_on_the_same_region(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Resort page → its danger link → bulletin → "See this region on the map".

    The resort page is a router to the two documents (SNOW-807) and the
    bulletin's tail is one link back to the map (SNOW-806), so the journey
    runs in that direction: the resort names its region's bulletin, and
    the bulletin hands over to the map at that region's fragment.
    """
    page.goto(f"{live_server.url}/resorts/verbier/")
    page.wait_for_load_state("networkidle")
    heading = page.locator('[data-testid="resort-heading"]')
    assert heading.inner_text().strip() == "Verbier"

    page.locator('[data-testid="resort-danger"] a').click()
    page.wait_for_load_state("networkidle")
    assert "/ch-4115/" in page.url

    map_link = page.locator('[data-testid="bulletin-map-link"]')
    map_link.wait_for(state="visible")
    map_link.click()
    page.wait_for_load_state("load")
    assert page.url.endswith("#CH-4115"), page.url
    expect(page.locator("#map")).to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_save_resort_toggles_the_favourite_button(
    favourites_page: FavouritesPage, _load_test_data: None, django_db_blocker: Any
) -> None:
    """A signed-in user clicks "Save resort" and the button flips to "Saved".

    The toggle posts to ``favourites:resort_toggle``, keyed on ``resort.pk``
    — an integer, so the casing redirect never touched it. It was dead
    purely because the page loaded no htmx, which makes this the case that
    isolates that defect from the redirect one.
    """
    with django_db_blocker.unblock():
        resort_url = Resort.objects.get(name="Verbier").get_absolute_url()

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}{resort_url}")

    toggle = page.get_by_test_id("resort-favourite-toggle")
    expect(toggle).to_have_attribute("data-favourited", "false")

    toggle.click()

    expect(toggle).to_have_attribute("data-favourited", "true")
