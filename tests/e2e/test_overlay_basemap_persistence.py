"""
tests/e2e/test_overlay_basemap_persistence.py — Playwright regression tests
for SNOW-473 (overlay tier visibility reverting on basemap swap).

``overlayState`` in ``static/js/map.js`` was seeded once at boot from
localStorage and never updated by the basemap picker's toggle handler
(which writes localStorage + mutates the live layer only), so the
``styledata`` re-install handler — which runs after every
``MAP.setStyle()`` call — re-seeded layer visibility from the stale
``overlayState``, reverting every tier to its boot value: L4 turned off
came back on, and a runtime-enabled lazy tier (l1/l2/l3/resorts/
community_reports) vanished.

Drives ``MAP.setStyle(MAP.getStyle(), {diff: false})`` to exercise the
exact teardown/re-install path without loading an external basemap style
(unreachable in the headless/CI harness) — same technique the plan
verified against the ``styledata`` handler in ``map.js``.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _reload_style_and_wait_for_regions(page: Page) -> None:
    """Force a full style re-install and wait for ``regions`` to reappear.

    ``MAP.setStyle(MAP.getStyle(), {diff: false})`` wipes every source and
    layer we added, exercising the same ``styledata`` re-install path a
    real basemap swap does. The re-install is async, so poll on the
    ``regions`` source reappearing rather than a fixed sleep.
    """
    page.evaluate("() => MAP.setStyle(MAP.getStyle(), { diff: false })")
    page.wait_for_function("() => !!MAP.getSource('regions')")


@pytest.mark.django_db(transaction=True)
def test_l4_stays_hidden_across_basemap_swap(
    live_server: LiveServer, page: Page
) -> None:
    """Turning L4 (micro regions) off must survive a basemap swap.

    Regression coverage for SNOW-473: before the fix, the stale
    ``overlayState.l4`` (seeded ``true`` at boot) was re-applied by the
    ``styledata`` handler's ``installRegionsLayers`` call, silently turning
    L4 back on the moment the user changed basemap.
    """
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="l4"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "true"

    toggle.click()
    assert toggle.get_attribute("aria-checked") == "false"
    assert (
        page.evaluate("() => MAP.getLayoutProperty('regions-fill', 'visibility')")
        == "none"
    )

    _reload_style_and_wait_for_regions(page)

    assert (
        page.evaluate("() => MAP.getLayoutProperty('regions-fill', 'visibility')")
        == "none"
    )


@pytest.mark.django_db(transaction=True)
def test_runtime_enabled_tier_survives_basemap_swap(
    live_server: LiveServer, page: Page
) -> None:
    """Enabling a lazy tier (resorts) at runtime must survive a basemap swap.

    Regression coverage for SNOW-473: before the fix, the stale
    ``overlayState.resorts`` (seeded ``false`` at boot — the picker's
    ``snowdesk:overlay-load`` handler reveals the layer but never sets
    ``overlayState.resorts = true``) was re-applied by
    ``installResortsLayer``, so the resorts pins vanished the moment the
    user changed basemap.
    """
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="resorts"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    with page.expect_response(lambda r: "resorts" in r.url and ".geojson" in r.url):
        toggle.click()
    assert toggle.get_attribute("aria-checked") == "true"

    page.wait_for_function(
        "() => MAP.getLayer('resorts-pin') && "
        "MAP.getLayoutProperty('resorts-pin', 'visibility') === 'visible'"
    )

    _reload_style_and_wait_for_regions(page)

    page.wait_for_function("() => !!MAP.getLayer('resorts-pin')")
    assert (
        page.evaluate("() => MAP.getLayoutProperty('resorts-pin', 'visibility')")
        == "visible"
    )
