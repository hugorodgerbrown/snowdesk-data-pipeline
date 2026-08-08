"""tests/e2e/test_home_intro_tour.py — A first-time visitor sees the intro card over the map and dismisses it.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: 1, MS7
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

STORAGE_KEY = "snowdesk.home.intro"
DISMISSED_VALUE = "dismissed"
MAP_HELP_STORAGE_KEY = "snowdesk.map.help"


def _assert_tour_on_first_step(page: Page) -> None:
    """Assert the open coachmark tour is showing step 1.

    ``#map-help-step-count`` is styled ``text-transform: uppercase``, and
    ``inner_text()`` returns text as rendered — so the DOM's "Step 1 of 13"
    arrives as "STEP 1 OF 13". Compare case-insensitively rather than
    against the casing of either the template string or the CSS.
    """
    count = page.locator("#map-help-step-count").inner_text().strip()
    assert count.upper().startswith("STEP 1"), (
        f"the tour should open on step 1; step counter read {count!r}"
    )


def _navigate_home_with_sw_stripped(
    page: Page, live_server_url: str, query: str = ""
) -> None:
    """Load / with navigator.serviceWorker stripped, wait for DOMContentLoaded.

    Stripping serviceWorker (before any page script runs) makes
    sw_register.js bail out immediately — mirrors
    test_report_sheet.py / test_edit_resorts_panel.py's identical helper —
    so a real SW can neither intercept this test's reload nor leave a
    stale shell behind for a later test in the same worker.

    ``query`` is appended verbatim (including its leading "?") for the
    ?intro=1 force-open case.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/{query}")
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.shows_home_intro
def test_explore_cta_dismisses_and_opens_the_tour(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The "Explore the map" CTA dismisses #home-intro AND opens the tour."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home_with_sw_stripped(page, live_server.url)

    intro = page.locator("#home-intro")
    assert intro.is_visible(), "#home-intro should be showing on a fresh visit"
    tour = page.locator("#map-help-overlay")
    assert tour.get_attribute("hidden") is not None, "the tour must not already be open"

    page.locator("#home-intro-dismiss").click()

    assert intro.get_attribute("hidden") is not None, (
        'the CTA should dismiss #home-intro, same as the "×"'
    )
    assert page.evaluate(f"() => localStorage.getItem({STORAGE_KEY!r})") == (
        DISMISSED_VALUE
    )
    page.wait_for_selector("#map-help-overlay:not([hidden])", timeout=5_000)
    _assert_tour_on_first_step(page)
    assert page_errors == [], f"JS errors: {page_errors}"
