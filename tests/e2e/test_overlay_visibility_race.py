"""
tests/e2e/test_overlay_visibility_race.py — Playwright regression tests for
two lazy-overlay toggle races in ``static/js/map.js``, both exposed by
toggling a tier faster than its first fetch can settle.

SNOW-493 finding 4 — a lazy overlay tier disabled again before its fetch
settles must not be silently re-shown. The ``snowdesk:overlay-load``
completion handler used to set every one of the overlay's layers to
``visibility: 'visible'`` unconditionally once ``ensureOverlayLoaded``
resolved. If the user toggled the tier ON (dispatching the fetch) and then
OFF again before that fetch settled, the "toggle off" click found no layer
yet to hide (``getLayer`` guards against a layer that doesn't exist yet) —
so when the fetch finally resolved and installed the layer, the completion
handler forced it back to ``visible``, silently reviving an overlay the
user had already turned off.

SNOW-493 P1 — an on → off → on before the first fetch settles must not
duplicate the tier's features. ``overlayLoaded[key]`` only flips true after
the fetch resolves, so the second enable used to pass the guard and start a
SECOND fetch; both responses then concatenated into
``majorGeojsonCache`` / ``subGeojsonCache``, doubling every L1/L2 feature in
memory (visible after the next basemap swap reinstalls the source from that
cache). ``ensureOverlayLoaded`` now reuses the in-flight load promise, so
the merge happens exactly once regardless of toggle churn.

Both tests strip the real service worker (same technique as
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
from typing import cast

import pytest
from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

# Long enough that the test's own synchronous double-click always fires
# well within the window, short enough not to slow the suite down.
_ROUTE_DELAY_S = 1.0

# Explicit, named ceiling for the shared page-boot wait (SNOW-516) — makes
# the timeout self-documenting rather than relying on Playwright's implicit
# default, and gives a slow CI runner headroom for the map/scrubber boot
# sequence, which is unrelated to the actual race being tested.
_BOOT_TIMEOUT_MS = 30_000
# Same rationale as _BOOT_TIMEOUT_MS, for the post-settle wait after the
# deliberately delayed fetch resolves (SNOW-516).
_SETTLE_TIMEOUT_MS = 30_000


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map to boot."""
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector(
        '#season-scrubber[data-state="ready"]', timeout=_BOOT_TIMEOUT_MS
    )
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()",
        timeout=_BOOT_TIMEOUT_MS,
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
    page.wait_for_function(
        "() => !!MAP.getLayer('resorts-pin')", timeout=_SETTLE_TIMEOUT_MS
    )
    assert (
        page.evaluate("() => MAP.getLayoutProperty('resorts-pin', 'visibility')")
        == "none"
    )


def _major_region_feature_stats(page: Page) -> dict[str, int]:
    """Return the total and distinct L1 feature counts from the live source.

    Major-region features carry a unique ``properties.prefix`` (EAWS region
    code prefix), so ``total`` diverging from ``distinct`` means the same
    feature was merged into ``majorGeojsonCache`` more than once.
    """
    return cast(
        "dict[str, int]",
        page.evaluate(
            """() => {
            const src = MAP.getSource('major-regions');
            if (!src) return { total: 0, distinct: 0 };
            const feats = (src.serialize().data.features) || [];
            const prefixes = new Set(
                feats.map((f) => f.properties && f.properties.prefix),
            );
            return { total: feats.length, distinct: prefixes.size };
        }"""
        ),
    )


@pytest.mark.django_db(transaction=True)
def test_overlay_reenabled_before_fetch_settles_does_not_duplicate(
    live_server: LiveServer, page: Page, _load_test_data: None
) -> None:
    """An on → off → on before the L1 fetch settles must not duplicate features.

    Regression coverage for SNOW-493 P1: enables the L1 (major regions)
    tier — dispatching its lazy fetch — then disables and re-enables it, all
    before the deliberately delayed fetch resolves. The old code left
    ``overlayLoaded.l1`` false until the first fetch settled, so the second
    enable started a second fetch and both responses concatenated into
    ``majorGeojsonCache``, doubling every feature. After the fix the tier's
    feature count must equal its distinct-region count (no duplication).
    """
    page.route(lambda url: "major-regions.geojson" in url, _delay_then_continue)

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="l1"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    # Enable → disable → re-enable in one round trip, so all three land
    # inside the route-delay window (see module docstring on why separate
    # Locator.click() calls are too slow to guarantee that).
    page.evaluate(
        """() => {
            const el = document.querySelector('[data-overlay-key="l1"]');
            el.click();
            el.click();
            el.click();
        }"""
    )
    assert toggle.get_attribute("aria-checked") == "true"
    # The source must not exist yet — otherwise the race window closed before
    # the clicks landed and this test would prove nothing.
    assert page.evaluate("() => !!MAP.getSource('major-regions')") is False

    # Let the delayed fetch(es) settle: with the pre-fix code two requests
    # were in flight and both would merge, so wait past the route delay to
    # give any second merge time to land before asserting.
    page.wait_for_function(
        "() => !!MAP.getSource('major-regions')", timeout=_SETTLE_TIMEOUT_MS
    )
    page.wait_for_timeout(int(_ROUTE_DELAY_S * 1000))

    stats = _major_region_feature_stats(page)
    assert stats["total"] > 0, "expected the L1 source to hold major-region features"
    assert stats["total"] == stats["distinct"], (
        "L1 features must not be duplicated by a re-enable before the first "
        f"fetch settled; got total={stats['total']} distinct={stats['distinct']}"
    )
