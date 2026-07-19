"""
tests/e2e/test_map_zoom_indicator.py — Playwright test for the SNOW-442
zoom-level readout pill and the raised ``maxZoom`` cap.

A MapLibre ``Map``'s camera state (pan/zoom) is available synchronously off
the constructor — it does not require the style JSON or any basemap tiles to
have finished loading, and ``MAP.setZoom()`` fires its ``'zoom'`` event
synchronously too. This test drives the camera directly via ``MAP.setZoom()``
rather than waiting on ``MAP.loaded()`` (which needs real network access to
the swisstopo tile/style servers and would make the test flaky in
network-restricted environments — see the "offline headless" note in
``tests/e2e/test_scrubber_reverse.py``). Driving the camera directly still
exercises the real ``map.js`` ``zoomIndicatorInit`` listener end to end, and
additionally proves out the SNOW-442 cap bump (``maxZoom`` 12 -> 18) by
asking the camera to zoom past the old cap and asserting the clamped result
lands above it.
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


def test_zoom_indicator_tracks_live_zoom_and_exceeds_old_cap(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Zooming past the old maxZoom:12 cap updates the readout and lands above 12.

    ``MAP.setZoom(20)`` asks for a zoom above the new ``maxZoom: 18`` cap, so
    the assertion exercises the constructor's clamp as well as the readout.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)

    indicator_value = page.locator("#map-zoom-indicator-value")
    assert indicator_value.count() == 1, "#map-zoom-indicator-value should render"

    initial_text = indicator_value.inner_text()

    page.evaluate("() => MAP.setZoom(20)")

    page.wait_for_function(
        "(prev) => {"
        " const el = document.getElementById('map-zoom-indicator-value');"
        " return !!el && el.textContent !== prev;"
        "}",
        arg=initial_text,
    )

    updated_text = indicator_value.inner_text()
    assert updated_text != initial_text, (
        "the readout should update after a zoom gesture"
    )
    assert int(updated_text) > 12, (
        f"zooming past the old maxZoom:12 cap should land above 12; got {updated_text!r}"
    )

    clamped_zoom = cast("float", page.evaluate("() => MAP.getZoom()"))
    assert clamped_zoom <= 18, (
        f"maxZoom:18 should clamp setZoom(20); got {clamped_zoom}"
    )
    assert clamped_zoom > 12, (
        f"the raised cap should allow zooming past the old maxZoom:12; got {clamped_zoom}"
    )

    assert page_errors == [], f"JS errors: {page_errors}"
