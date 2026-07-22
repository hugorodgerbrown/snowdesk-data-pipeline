"""
tests/e2e/test_overlay_visibility_race.py — Playwright regression test for
SNOW-493 finding 4: a lazy overlay tier disabled again before its fetch
settles must not be silently re-shown.

``static/js/map.js``'s ``snowdesk:overlay-load`` completion handler used to
set every one of the overlay's layers to ``visibility: 'visible'``
unconditionally once ``ensureOverlayLoaded`` resolved. If the user toggled
the tier ON (dispatching the fetch) and then OFF again before that fetch
settled, the "toggle off" click found no layer yet to hide (``getLayer``
guards against a layer that doesn't exist yet) — so when the fetch finally
resolved and installed the layer, the completion handler forced it back
to ``visible``, silently reviving an overlay the user had already turned
off.

Strips the real service worker (same technique as
``test_offline_map.py``'s toast tests) — a real, activated SW intercepts
the resorts-geojson fetch inside its own ``fetch`` handler, which
``page.route()`` cannot see or delay (the documented "a service worker's
own fetch is invisible to Playwright" quirk in ``docs/client-side-tests.md``),
so without stripping it the deliberate route delay below never actually
applies and the race window never opens. Fires both toggle clicks via a
single ``page.evaluate()`` (rather than two separate
``Locator.click()`` calls) so no per-click actionability/round-trip
overhead can let the delayed fetch settle in between — confirmed via
timing instrumentation that a plain ``locator.click()`` pair can take over
half a second, comfortably longer than a short route delay.
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

# Long enough that the test's own synchronous double-click always fires
# well within the window, short enough not to slow the suite down.
_ROUTE_DELAY_S = 1.0


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map to boot."""
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _delay_then_continue(route: Route) -> None:
    """Hold a route open briefly before letting it through.

    Creates a deterministic window in which the test can fire both toggle
    clicks before the delayed fetch settles, rather than depending on real
    (unpredictable) network timing.
    """
    time.sleep(_ROUTE_DELAY_S)
    route.continue_()


@pytest.mark.django_db(transaction=True)
def test_overlay_disabled_before_fetch_settles_stays_hidden(
    live_server: LiveServer, page: Page
) -> None:
    """Toggling a lazy overlay off before its fetch resolves must stick.

    Regression coverage for SNOW-493 finding 4: enables the resorts tier
    (dispatching its lazy fetch), then immediately disables it again
    before the (deliberately delayed) fetch settles. Once the fetch does
    resolve and the layer installs, its visibility must be 'none' — not
    forced back to 'visible' by the completion handler ignoring the
    toggle that happened in between.
    """
    page.route(lambda url: "resorts.geojson" in url, _delay_then_continue)

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="resorts"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    # Enable then immediately disable, in one round trip — see module
    # docstring for why two separate Locator.click() calls aren't fast
    # enough to guarantee both land inside the route delay window.
    page.evaluate(
        """() => {
            const el = document.querySelector('[data-overlay-key="resorts"]');
            el.click();
            el.click();
        }"""
    )
    assert toggle.get_attribute("aria-checked") == "false"
    # The layer must not exist yet — otherwise the race window already
    # closed before the second click landed, and this test would be a
    # false negative rather than proving anything.
    assert page.evaluate("() => !!MAP.getLayer('resorts-pin')") is False

    # Wait for the delayed fetch to resolve and the layer to actually
    # install (the race the bug exploited), then assert its visibility
    # reflects the LAST toggle state (off), not "always visible on load".
    page.wait_for_function("() => !!MAP.getLayer('resorts-pin')", timeout=10000)
    assert (
        page.evaluate("() => MAP.getLayoutProperty('resorts-pin', 'visibility')")
        == "none"
    )
