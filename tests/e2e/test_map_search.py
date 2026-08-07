"""tests/e2e/test_map_search.py — A user searches for a region by name and lands on it.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: MS1
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

_SEARCH_TOGGLE = "#search-toggle"
_SEARCH_INPUT = "#search-input"
_SEARCH_RESULTS = "#search-results"
_RESULT = f"{_SEARCH_RESULTS} .search-result"


def _open_search(page: Page) -> None:
    """Reveal the search input and wait for the map to have its regions.

    The index is built from ``REGION_LOOKUP`` inside ``map.js``'s main
    IIFE once the initial CH load has populated it, so a query typed
    before then legitimately matches nothing.
    """
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.click(_SEARCH_TOGGLE)
    expect(page.locator(_SEARCH_INPUT)).to_be_visible()


@pytest.mark.usefixtures("_load_test_data")
def test_search_matches_a_region_by_name(live_server: LiveServer, page: Page) -> None:
    """Typing a region's name lists it, with its EAWS id.

    The index-building half of the search: if ``indexRegion`` stopped
    feeding ``buildEntry``, or fed it the wrong shape, there would be
    nothing to match and this is where it shows.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    _open_search(page)

    page.fill(_SEARCH_INPUT, "Martigny")

    expect(page.locator(_RESULT).first).to_be_visible()
    assert "CH-4115" in page.locator(_RESULT).first.inner_text()
    assert page_errors == [], f"JS errors: {page_errors}"
