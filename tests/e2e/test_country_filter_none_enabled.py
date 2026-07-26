"""
tests/e2e/test_country_filter_none_enabled.py — Playwright regression test
for the "no countries enabled" map filter (SNOW-524).

``applyCountryFilters`` (``static/js/map.js``) builds an always-false filter
when every country toggle is off. That expression used to be
``['==', false, true]``, which MapLibre parses as the LEGACY ``==`` filter —
whose second element must be a property name — so it rejected the entire
style with "layers.regions-fill.filter[1]: string expected, boolean found".
The map then had no style at all and rendered as a blank canvas, and because
the preference persists in localStorage it stayed blank on every subsequent
load. Unticking the last country was enough to trigger it.

This needs a real browser: the bug is MapLibre's own filter validation
rejecting the style, which no fake-map unit test would reproduce. Assertions
read MapLibre's style state (``isStyleLoaded``, ``getFilter``) rather than
pixels, matching the rest of the map e2e suite — a canvas screenshot would
depend on WebGL frame timing in headless Chromium.

``_load_test_data`` is required: the region layers only install once the
GeoJSON feeds return features, and the filter under test lives on
``regions-fill``. The service worker is stripped (same idiom as
test_map_layer_sync_status.py) so it cannot intercept those feeds.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

_COUNTRY_KEYS = ("ch", "fr", "at", "it")


def _navigate_all_countries_off(page: Page, live_server_url: str) -> None:
    """Load / with every country pref pre-set to off and the SW stripped.

    Both writes go in via ``add_init_script`` so they are in place before
    map.js reads them on boot — the state a user reaches by unticking each
    country in the layers menu.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.add_init_script(
        "try {"
        + "".join(
            f"localStorage.setItem('snowdesk.map.overlay.country.{code}', 'false');"
            for code in _COUNTRY_KEYS
        )
        + "} catch (e) {}"
    )
    # SNOW-524: the layers menu disables an uncached row while offline, and
    # the picker's click handler honours that. Headless Chromium in this
    # harness has no route to the internet and can report
    # ``navigator.onLine === false``, which would silently make every row
    # inert and any toggle below a no-op. Pin it true so the test controls
    # connectivity rather than inheriting the runner's.
    page.add_init_script(
        "Object.defineProperty(navigator, 'onLine', "
        "{ value: true, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_function("() => MAP.getLayer('regions-fill') !== undefined")
    # Let boot's fetches settle so a country toggle below is the only thing in
    # flight when its filter is asserted.
    page.wait_for_load_state("networkidle")


def _style_is_healthy(page: Page) -> bool:
    """True when MapLibre still has a style carrying layers.

    The failure this guards against leaves the map with NO style whatsoever —
    the invalid filter is rejected, the fallback ``setStyle`` aborts on top of
    it, and ``getStyle()`` returns nothing — so style-presence is the signal.

    Deliberately does not assert ``isStyleLoaded()``: that is transiently
    false whenever tiles are in flight (the same trap documented against the
    SNOW-492 error handler in map.js), and the harness cannot reach the
    basemap CDN, so gating on it makes the test flaky without testing
    anything the layer list doesn't already prove.
    """
    return bool(
        page.evaluate(
            """() => {
                const style = MAP.getStyle();
                return !!(style && style.layers && style.layers.length > 0);
            }"""
        )
    )


@pytest.mark.usefixtures("_load_test_data")
def test_map_still_renders_with_every_country_disabled(
    live_server: LiveServer, page: Page
) -> None:
    """Turning every country off must leave a valid style, not a blank map."""
    _navigate_all_countries_off(page, live_server.url)

    assert _style_is_healthy(page), "map lost its style with no countries enabled"

    country_filter = page.evaluate("() => MAP.getFilter('regions-fill')")
    assert country_filter is not None
    # Must be an expression, never the legacy ['==', <boolean>, ...] form
    # MapLibre rejects.
    assert not isinstance(country_filter[1], bool), (
        f"always-false filter is not a valid expression: {country_filter!r}"
    )
