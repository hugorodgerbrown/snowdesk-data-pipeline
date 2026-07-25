"""
tests/e2e/test_layers_menu_removed_items.py — Playwright regression test for
SNOW-521's layers-menu cleanup: the L3 (bulletin groupings) overlay row, the
Options section (Auto-zoom), and the basemap sync-status caption were all
removed from ``#basemap-menu`` alongside the per-region download rework, but
the menu itself must still open and render its remaining sections normally.

Pytest already covers the server-rendered HTML (``tests/public/test_map_page.
py``'s ``test_map_layer_menu_section_order`` /
``test_map_layer_menu_renders_sync_status_dots`` /
``test_basemap_menu_omits_sync_status_caption``); this e2e test is the
client-side companion — a real menu open, asserting the removed controls
never appear in the live DOM and the menu still functions.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


@pytest.mark.django_db(transaction=True)
def test_layers_menu_omits_removed_items_and_still_opens(
    live_server: LiveServer, page: Page
) -> None:
    """Opening the layers menu never shows l3/autozoom/basemap-sync-status."""
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )

    page.click("#basemap-toggle")
    menu = page.locator("#basemap-menu")
    menu.wait_for(state="visible")

    assert menu.get_attribute("hidden") is None

    assert page.locator('[data-overlay-key="l3"]').count() == 0
    assert page.locator("#autozoom-toggle").count() == 0
    assert page.locator("#basemap-sync-status").count() == 0

    # The menu still functions: the remaining overlay rows and the Base
    # map section are present and openable.
    assert page.locator('[data-overlay-key="l1"]').count() == 1
    assert page.locator('[data-overlay-key="l2"]').count() == 1
    assert page.locator(".basemap-menu-section-label", has_text="Base map").count() == 1
