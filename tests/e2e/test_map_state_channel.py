"""
tests/e2e/test_map_state_channel.py — ``window.snowdeskMapState`` (SNOW-610).

The one named channel to ``map.js``'s shared module state, and the
foundation the file's split is built on: once an IIFE moves into its own
file it can no longer close over ``MAP`` / ``FEATURE_BY_REGION_ID`` /
``COUNTRY_STATE``, so it has to reach them through here.

This has to be an e2e test. ``map.js`` cannot be imported under jsdom — its
boot IIFEs need MapLibre — which is the whole reason the ``*_core.js``
modules exist. The accessors are defined against live module bindings, so
only a real browser running the real file can show they resolve.

The failure this guards is silent, and it has already happened once:
``map_layer_sync_status.js`` read ``window.MAP`` for its entire life. A
top-level ``let`` in a classic script lands in the global *lexical* scope,
not on ``window``, so the read was always ``undefined`` and every guard
around it took the "no map yet" branch (finding M1 in
``docs/code-reviews/2026-08-03-js-review.md``). Nothing went red. If the
accessors here ever stop resolving, the same class of bug returns to every
consumer at once — so each assertion below is that a value is genuinely
populated, never merely that the property exists.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

# The map is up and the initial CH regions are loaded. `snowdeskMapState.map`
# is null until the style loads, so every assertion has to wait for this.
_MAP_READY = (
    "() => window.snowdeskMapState "
    "&& window.snowdeskMapState.map "
    "&& window.snowdeskMapState.map.loaded()"
)


@pytest.mark.usefixtures("_load_test_data")
def test_the_channel_exposes_the_live_map(live_server: LiveServer, page: Page) -> None:
    """``.map`` resolves to the real MapLibre instance, not undefined.

    The direct M1 regression: had the state been published as plain data
    at declaration time, this would read the ``null`` that ``MAP`` held
    before the style loaded and never update.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_function(_MAP_READY)

    same_instance = page.evaluate(
        "() => window.snowdeskMapState.map === MAP && typeof "
        "window.snowdeskMapState.map.getStyle === 'function'"
    )

    assert same_instance is True


@pytest.mark.usefixtures("_load_test_data")
def test_the_channel_exposes_the_populated_region_lookups(
    live_server: LiveServer, page: Page
) -> None:
    """``featureByRegionId`` is the same populated object the file fills.

    Asserts a non-empty count, not just identity: an accessor returning a
    fresh empty object each call would satisfy identity-free checks and
    break every consumer that reads a region out of it.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_function(_MAP_READY)
    page.wait_for_function(
        "() => Object.keys(window.snowdeskMapState.featureByRegionId).length > 0"
    )

    result = page.evaluate(
        """() => ({
            sameObject: window.snowdeskMapState.featureByRegionId === FEATURE_BY_REGION_ID,
            count: Object.keys(window.snowdeskMapState.featureByRegionId).length,
        })"""
    )

    assert result["sameObject"] is True
    assert result["count"] > 0


@pytest.mark.usefixtures("_load_test_data")
def test_the_channel_reflects_writes_back_into_module_state(
    live_server: LiveServer, page: Page
) -> None:
    """A setter writes through to the binding, in both directions.

    The reason the object is frozen but its values are not. An extracted
    IIFE that sets ``autozoom`` must move the same flag ``selectFeature``
    reads, or the two halves of the split silently disagree.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_function(_MAP_READY)

    result = page.evaluate(
        """() => {
            const before = AUTOZOOM;
            window.snowdeskMapState.autozoom = !before;
            const afterWrite = AUTOZOOM;
            AUTOZOOM = before;
            return {
                writeReachedBinding: afterWrite === !before,
                readReflectsBinding: window.snowdeskMapState.autozoom === before,
            };
        }"""
    )

    assert result["writeReachedBinding"] is True
    assert result["readReflectsBinding"] is True


@pytest.mark.usefixtures("_load_test_data")
def test_the_channel_surface_is_frozen(live_server: LiveServer, page: Page) -> None:
    """Nothing can add to or replace an accessor.

    The surface is the contract every extracted module will code against.
    A consumer that could bolt its own key on would be back to the
    incidental coupling the split exists to remove.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_function(_MAP_READY)

    result = page.evaluate(
        """() => {
            let added = false;
            try {
                window.snowdeskMapState.somethingNew = 1;
                added = 'somethingNew' in window.snowdeskMapState;
            } catch (_e) {
                added = false;
            }
            return { frozen: Object.isFrozen(window.snowdeskMapState), added };
        }"""
    )

    assert result["frozen"] is True
    assert result["added"] is False
