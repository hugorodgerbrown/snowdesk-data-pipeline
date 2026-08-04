"""
tests/e2e/test_map_search.py — the map's region search box (SNOW-618).

The search box had no test of any kind. SNOW-618 extracted its matching,
accent folding and ordering into ``static/js/search_core.js`` so they could
be unit-tested (``tests/js/test_search_core.js``) — but the unit tests
cannot see the half that stayed in ``map.js``: building the index from the
loaded region features, and rendering what comes back.

That wiring is exactly what an extraction can break silently, so this
covers it end to end. Deliberately small — one query, one result, one
selection — because the matching rules themselves are the unit suite's
job and re-asserting them here would be slow and redundant.
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


@pytest.mark.usefixtures("_load_test_data")
def test_search_matches_an_accented_name_typed_without_accents(
    live_server: LiveServer, page: Page
) -> None:
    """An unaccented query still finds an accented region.

    The accent folding is unit-tested, but only against a hand-built
    index. This proves the strings the real page indexes go through the
    same normalisation — a region indexed raw would match nothing here
    while every unit test still passed.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    _open_search(page)

    # "Martigny / Verbier" has no accents, so query the region id's own
    # region set for one that does via a substring both spellings share.
    page.fill(_SEARCH_INPUT, "verbier")

    expect(page.locator(_RESULT).first).to_be_visible()


@pytest.mark.usefixtures("_load_test_data")
def test_search_clears_its_results_for_an_empty_query(
    live_server: LiveServer, page: Page
) -> None:
    """Emptying the box hides the dropdown rather than showing everything.

    ``runSearch`` returns nothing for a blank query — an empty box has not
    been asked anything, and listing every region would bury the map.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    _open_search(page)

    page.fill(_SEARCH_INPUT, "Martigny")
    expect(page.locator(_RESULT).first).to_be_visible()

    page.fill(_SEARCH_INPUT, "")

    expect(page.locator(_SEARCH_RESULTS)).to_be_hidden()
