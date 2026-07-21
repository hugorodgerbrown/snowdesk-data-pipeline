"""
tests/e2e/test_offline_basemap_fallback.py — Playwright regression tests for
SNOW-483 (blank map when the basemap style fails to load offline).

Offline, the basemap style JSON (``settings.BASEMAP_STYLE_URL``) is a
third-party URL that ``static/js/sw.js`` deliberately treats as
network-only, so the fetch fails, MapLibre never fires ``load``, and every
overlay installed inside ``map.on('load')`` in ``static/js/map.js`` is
skipped — the whole map blanks even though the regions GeoJSON and ratings
are both SW-cached and fetch fine offline.

The fix registers a ``map.on('error')`` listener that swaps in an inline
fallback background style (``buildFallbackStyle``) the first time a style
fails to load, so MapLibre still fires ``load`` and the existing overlay
install path paints the cached region overlays on a plain background.
Connectivity returning (``window`` ``online``) retries the real basemap via
the existing ``resolveBasemapStyle`` swap machinery.

``page.route`` does not intercept the service worker's own fetches, but
MapLibre's style fetch is a page-level fetch, so routing
``settings.BASEMAP_STYLE_URL`` to abort simulates the offline failure
without needing a real SW / offline browser context. CSS is not compiled in
this harness (see ``docs/client-side-tests.md``), so these tests assert
overlay presence/interactivity rather than the fallback's exact background
colour. Any ``route.fulfill`` serving a cross-origin URL (the configured
basemap is always third-party) needs an explicit
``Access-Control-Allow-Origin`` header — MapLibre's internal style fetch
uses CORS mode, and Playwright's mock response doesn't bypass that check.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.conf import settings
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the season scrubber to finish booting."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')


def _poll(page: Page, expr: str, *, timeout_ms: int = 20000) -> None:
    """Poll a JS boolean ``expr`` via ``page.evaluate`` until it is truthy.

    Used instead of ``page.wait_for_function`` for MapLibre source/layer
    predicates: on the strict-CSP map page ``wait_for_function``'s polling
    proved unreliable for these, whereas ``page.evaluate`` (CDP
    ``Runtime.evaluate``) reads them reliably.
    """
    waited = 0
    while waited < timeout_ms:
        if page.evaluate(expr):
            return
        page.wait_for_timeout(250)
        waited += 250
    raise AssertionError(f"condition never became true within {timeout_ms}ms: {expr}")


def _minimal_style(origin: str) -> dict[str, Any]:
    """A minimal but valid MapLibre style, used to simulate a real basemap.

    Same shape as the Frutiger style in ``test_map_glyphs.py``, trimmed to
    the bare essentials since font derivation isn't under test here — but
    ``glyphs`` must still be declared: the boot-installed ``regions-label``
    symbol layer survives the basemap swap (SNOW-473 re-install), and
    MapLibre rejects any ``text-field`` layer on a glyphs-less style, which
    would otherwise fire a spurious ``error`` and mask the recovery this
    test is asserting on. ``name`` is a sentinel the test polls for — it is
    never ``snowdesk-offline-fallback``, so its presence proves the real
    basemap swap happened rather than the fallback simply lingering.
    """
    return {
        "version": 8,
        "name": "snowdesk-recovered",
        "glyphs": f"{origin}/static/fonts/{{fontstack}}/{{range}}.pbf",
        "sources": {},
        "layers": [],
    }


@pytest.mark.django_db(transaction=True)
def test_fallback_paints_cached_overlays_when_basemap_style_fails(
    live_server: LiveServer, page: Page
) -> None:
    """Overlays install against the fallback style when the basemap 404s.

    Regression coverage for SNOW-483: aborting the basemap style fetch
    simulates the offline failure mode. Before the fix, MapLibre never
    fired ``load`` and the map canvas stayed blank. After the fix, the
    ``error`` handler swaps in the inline fallback style, ``load`` fires,
    and the boot overlay install runs as normal.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.route(settings.BASEMAP_STYLE_URL, lambda route: route.abort())

    _navigate_home(page, live_server.url)

    # The fallback flag flips and the sentinel style loads before the boot
    # overlay handler runs, so getStyle().name is the first observable sign
    # the fix engaged.
    _poll(page, "() => MAP && MAP.getStyle()?.name === 'snowdesk-offline-fallback'")

    # The existing map.on('load') overlay-install path still runs against
    # the fallback style, painting the SW-cached region overlays.
    _poll(page, "() => !!MAP.getSource('regions')")
    _poll(
        page,
        "() => MAP.getLayer('regions-fill') && "
        "MAP.getLayoutProperty('regions-fill', 'visibility') === 'visible'",
    )

    # The map is interactive despite the dead basemap: the style finishes
    # loading (no pending style-load promise leaves the map half-initialised)
    # and it responds to a camera query.
    _poll(page, "() => MAP.isStyleLoaded()")
    assert isinstance(page.evaluate("() => MAP.getZoom()"), float)
    assert page_errors == [], f"unexpected JS errors: {page_errors}"


@pytest.mark.django_db(transaction=True)
def test_basemap_swaps_back_in_once_online_again(
    live_server: LiveServer, page: Page
) -> None:
    """Reconnecting retries the real basemap and keeps the overlays.

    Regression coverage for SNOW-483: once the fallback is active, a
    ``window`` ``online`` event should retry ``resolveBasemapStyle`` and
    swap the real style back in, without disturbing the region overlays
    that painted while degraded.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    route_state: dict[str, Any] = {"style": None}

    def _handle_route(route: Any) -> None:
        if route_state["style"] is None:
            route.abort()
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps(route_state["style"]),
            )

    page.route(settings.BASEMAP_STYLE_URL, _handle_route)

    _navigate_home(page, live_server.url)
    _poll(page, "() => MAP && MAP.getStyle()?.name === 'snowdesk-offline-fallback'")
    _poll(page, "() => !!MAP.getSource('regions')")

    # Connectivity returns: the route now serves a valid style, and the
    # online listener retries resolveBasemapStyle(initialBasemapKey, …),
    # which for a native (non-ESRI) basemap re-fetches the same URL.
    route_state["style"] = _minimal_style(live_server.url)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")

    _poll(page, "() => MAP.getStyle()?.name === 'snowdesk-recovered'")
    _poll(page, "() => !!MAP.getSource('regions')")
    assert page_errors == [], f"unexpected JS errors: {page_errors}"
