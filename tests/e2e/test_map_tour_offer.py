"""tests/e2e/test_map_tour_offer.py — A first-time visitor is offered the tour once the map settles, and takes it.

The tour's second step selects a region on the real map (SNOW-1118), which
only a browser running map.js can show, and lets it go on the step after.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: 1, MS7, MS8
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer


@pytest.mark.shows_tour_offer
def test_offer_appears_after_the_map_and_starts_the_tour(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The map opens clean; the offer follows it; the tour shows a real region."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    # No service worker: one cannot intercept the load or outlive the test.
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server.url}/map/")
    offer = page.locator("#map-tour-offer")
    expect(offer).to_be_hidden()

    expect(offer).to_be_visible(timeout=15_000)
    page.locator("#map-tour-offer-start").click()

    expect(offer).to_be_hidden()
    expect(page.locator("#map-help-overlay")).to_be_visible()
    expect(page.locator("#map-help-step-count")).to_contain_text(
        "Step 1 of", ignore_case=True
    )
    page.locator("#map-help-next").click()
    expect(page.locator("#map-help-tooltip-title")).to_have_text("Tap a region")
    expect(page).to_have_url(re.compile(r"#\w"))
    page.locator("#map-help-next").click()
    expect(page).not_to_have_url(re.compile(r"#\w"))
    stored = page.evaluate("() => localStorage.getItem('snowdesk.map.tour-offer')")
    assert stored == "dismissed"
    assert page_errors == [], f"JS errors: {page_errors}"
