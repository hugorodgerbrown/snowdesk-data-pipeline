"""
tests/e2e/test_map_zoom_console.py — Playwright test for the SNOW-445 console
zoom exposure and the raised ``maxZoom`` cap (SNOW-442).

The always-visible on-map zoom pill (SNOW-442) was a developer/debug artefact
and has been removed from the user-facing UI (SNOW-445). The live zoom is now
exposed on the console instead: ``window.snowdeskMap`` is the MapLibre instance
and carries a read-only ``zoom_level`` getter that proxies ``getZoom()``.

A MapLibre ``Map``'s camera state (pan/zoom) is available synchronously off the
constructor — it does not require the style JSON or any basemap tiles to have
finished loading, and ``MAP.setZoom()`` fires its ``'zoom'`` event
synchronously too. This test drives the camera directly via ``MAP.setZoom()``
rather than waiting on ``MAP.loaded()`` (which needs real network access to the
swisstopo tile/style servers and would make the test flaky in network-restricted
environments — see the "offline headless" note in
``tests/e2e/test_scrubber_reverse.py``). Driving the camera directly still
proves out the SNOW-442 cap bump (``maxZoom`` 12 -> 18) by asking the camera to
zoom past the old cap and asserting the clamped result lands above it.
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for map.js's module-scope MAP global to exist."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function("() => typeof MAP !== 'undefined' && MAP !== null")


def test_zoom_exposed_on_console_and_exceeds_old_cap(
    live_server: LiveServer,
    page: Page,
) -> None:
    """window.snowdeskMap.zoom_level tracks the camera and honours maxZoom:18.

    ``MAP.setZoom(20)`` asks for a zoom above the new ``maxZoom: 18`` cap, so
    the assertion exercises the constructor's clamp as well as the getter.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)

    # The debug pill is gone from the user-facing UI.
    assert page.locator("#map-zoom-indicator").count() == 0, (
        "the on-map zoom pill should no longer render"
    )

    # window.snowdeskMap is the live MapLibre instance with a zoom_level getter.
    assert page.evaluate("() => window.snowdeskMap === MAP") is True, (
        "window.snowdeskMap should expose the MapLibre instance"
    )

    page.evaluate("() => MAP.setZoom(20)")

    zoom_level = cast("float", page.evaluate("() => window.snowdeskMap.zoom_level"))
    get_zoom = cast("float", page.evaluate("() => MAP.getZoom()"))
    assert zoom_level == get_zoom, "zoom_level getter should proxy getZoom()"

    assert zoom_level <= 18, f"maxZoom:18 should clamp setZoom(20); got {zoom_level}"
    assert zoom_level > 12, (
        f"the raised cap should allow zooming past the old maxZoom:12; got {zoom_level}"
    )

    assert page_errors == [], f"JS errors: {page_errors}"
