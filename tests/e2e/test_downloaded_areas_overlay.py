"""
tests/e2e/test_downloaded_areas_overlay.py — Playwright regression tests for
the "Downloaded areas" layers-menu overlay (SNOW-570).

The overlay outlines every area currently held in ``BASEMAP_PINNED_CACHE``:
each loaded micro-region whose download is present (a ``downloaded``
feature-state driving ``regions-line-downloaded``'s ``line-opacity``, the
same paint-driven-visibility trick ``regions-line-selected`` uses because
MapLibre rejects feature-state inside a filter), plus the single saved
custom area (its own ``downloaded-area`` source). It is off by default.

Two properties are what these tests exist to pin, because both are easy to
break in ways nothing else notices:

1. **Probed, never stored.** The overlay is re-derived from real Cache
   Storage on every refresh. A "the user downloaded this" flag written at
   download time would pass a naive test and then lie after an eviction, a
   basemap swap, or Clear Site Data. ``test_outline_follows_the_cache_not_
   the_click`` deletes the tile behind the app's back and asserts the ring
   goes with it — a stored flag cannot pass it.
2. **Per-basemap.** The probe keys off the ACTIVE basemap's tile template,
   so the same cache answers differently per basemap.

Assertions read feature-state and source data rather than pixels, matching
this suite's convention (see ``test_map_placement_focus.py``'s module
docstring). Helpers come from ``test_cache_this_area.py`` — see
``test_download_progress_fill.py``'s docstring for why the warm-cache stub
has to live on the service worker's own ``self._warmCache``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _MICRO_SUMMARY,
    _STUB_TEMPLATE,
    _reload_home,
    _select_region,
    _stub_active_basemap_template,
    _stub_region_basemap_tiles,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

pytestmark = pytest.mark.usefixtures("_load_test_data")

_CONTROL = "#map-download-control"
_TOGGLE = '#basemap-menu [data-overlay-key="downloaded"]'
_REGION_LAYER = "regions-line-downloaded"
_AREA_LAYER = "downloaded-area-line"

# The centre tile _STUB_BLOB/_MICRO_SUMMARY both point at, as a URL against
# the stubbed template — the one entry a successful stubbed download writes
# into the pinned cache, and so the one the overlay's probe looks for.
_CENTRE_TILE_URL = (
    _STUB_TEMPLATE.replace("{z}", "14").replace("{x}", "100").replace("{y}", "100")
)


def _boot(pwa_page: PwaPage) -> Page:
    """Reload, stub the warm-cache run, the basemap template and the blob."""
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _stub_warm_cache(page.context.service_workers[0], ok=1, failed=0)
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    page.wait_for_function(
        "() => !!MAP.getSource('regions') && MAP.isStyleLoaded() "
        "&& !!MAP.getLayer('regions-line-downloaded')"
    )
    return page


def _open_layers_menu(page: Page) -> None:
    """Open the layers popover, if it isn't already open."""
    if not page.locator(_TOGGLE).is_visible():
        page.click("#basemap-toggle")
    page.locator(_TOGGLE).wait_for(state="visible")


def _toggle_overlay(page: Page, *, on: bool) -> None:
    """Set the "Downloaded areas" checkbox to `on` and wait for the flip."""
    _open_layers_menu(page)
    toggle = page.locator(_TOGGLE)
    if (toggle.get_attribute("aria-checked") == "true") != on:
        toggle.click()
    page.wait_for_function(
        """({ layer, expected }) => {
            if (!MAP.getLayer(layer)) return false;
            const visibility = MAP.getLayoutProperty(layer, 'visibility');
            return (visibility !== 'none') === expected;
        }""",
        arg={"layer": _REGION_LAYER, "expected": on},
    )


def _layer_visibility(page: Page, layer: str) -> str:
    return cast(
        str,
        page.evaluate(
            "(layer) => MAP.getLayer(layer)"
            " ? (MAP.getLayoutProperty(layer, 'visibility') || 'visible')"
            " : 'absent'",
            layer,
        ),
    )


def _is_outlined(page: Page, region_id: str) -> bool:
    """Whether `region_id` currently carries the ``downloaded`` state."""
    return cast(
        bool,
        page.evaluate(
            """(regionId) => {
                const feature = FEATURE_BY_REGION_ID[regionId];
                if (!feature || feature.id === undefined) return false;
                const state = MAP.getFeatureState({ source: 'regions', id: feature.id });
                return !!(state && state.downloaded);
            }""",
            region_id,
        ),
    )


def _wait_until_outlined(page: Page, region_id: str, *, outlined: bool) -> None:
    page.wait_for_function(
        """({ regionId, outlined }) => {
            const feature = FEATURE_BY_REGION_ID[regionId];
            if (!feature || feature.id === undefined) return false;
            const state = MAP.getFeatureState({ source: 'regions', id: feature.id });
            return !!(state && state.downloaded) === outlined;
        }""",
        arg={"regionId": region_id, "outlined": outlined},
        timeout=10000,
    )


def _select_region_with_id(page: Page, region_id: str, numeric_id: int = 4115) -> None:
    """Inject a synthetic region carrying a source feature id.

    ``test_cache_this_area._select_region`` doesn't set ``feature.id``, and
    the overlay writes feature-state keyed on exactly that (as every other
    feature-state write on the regions source does). Adds it, then reuses
    that helper's event dispatch by calling it afterwards.
    """
    _select_region(page, region_id, _MICRO_SUMMARY)
    page.evaluate(
        "({ regionId, numericId }) => { FEATURE_BY_REGION_ID[regionId].id = numericId; }",
        {"regionId": region_id, "numericId": numeric_id},
    )


def _delete_from_pinned_cache(page: Page, url: str) -> None:
    """Delete one URL from the pinned basemap cache, behind the app's back.

    Simulates an eviction — the thing that makes a stored "downloaded" flag
    wrong and a probe right.
    """
    page.evaluate(
        """async (url) => {
            const names = await caches.keys();
            const name = names.find((n) => n.startsWith('snowdesk-basemap-pinned-'));
            if (name) {
                const cache = await caches.open(name);
                await cache.delete(url);
            }
        }""",
        url,
    )


def test_overlay_is_off_by_default(pwa_page: PwaPage) -> None:
    """The row is unchecked and both layers are hidden on a fresh session."""
    page = _boot(pwa_page)
    _open_layers_menu(page)

    assert page.locator(_TOGGLE).get_attribute("aria-checked") == "false"
    assert _layer_visibility(page, _REGION_LAYER) == "none"
    assert _layer_visibility(page, _AREA_LAYER) == "none"


def test_row_carries_no_sync_dot(pwa_page: PwaPage) -> None:
    """The row has no dot — it has no data of its own to be cached.

    Every other overlay row's dot reports whether THAT row's feed is
    available offline. This row is derived from the cache those dots
    describe, so a dot here would be a second, subtly different claim about
    the same thing.
    """
    page = _boot(pwa_page)
    _open_layers_menu(page)

    assert page.locator(f"{_TOGGLE} .sync-dot").count() == 0


def test_toggling_on_outlines_a_downloaded_region(pwa_page: PwaPage) -> None:
    """A region downloaded this session gains its ring when the overlay is on."""
    page = _boot(pwa_page)
    _select_region_with_id(page, "CH-4115")
    _wait_for_state(page, "idle")

    # Nothing downloaded yet: the overlay is on but has nothing to show.
    _toggle_overlay(page, on=True)
    assert not _is_outlined(page, "CH-4115")

    page.locator(_CONTROL).click()
    _wait_for_state(page, "done", timeout=10000)

    # The download's own finish handler refreshes the overlay, so the ring
    # arrives without the user reopening the menu.
    _wait_until_outlined(page, "CH-4115", outlined=True)


def test_toggling_off_hides_both_layers(pwa_page: PwaPage) -> None:
    """Switching the overlay off hides the region and custom-area layers."""
    page = _boot(pwa_page)
    _toggle_overlay(page, on=True)
    _toggle_overlay(page, on=False)

    assert _layer_visibility(page, _REGION_LAYER) == "none"
    assert _layer_visibility(page, _AREA_LAYER) == "none"


def test_outline_follows_the_cache_not_the_click(pwa_page: PwaPage) -> None:
    """An evicted download loses its ring on the next refresh.

    The load-bearing test for "probed, never stored": the tile is deleted
    without the app ever being told, so only a real Cache Storage read can
    get this right. A flag set when the user clicked Download would still
    say "downloaded" here.
    """
    page = _boot(pwa_page)
    _select_region_with_id(page, "CH-4115")
    _wait_for_state(page, "idle")
    _toggle_overlay(page, on=True)

    page.locator(_CONTROL).click()
    _wait_for_state(page, "done", timeout=10000)
    _wait_until_outlined(page, "CH-4115", outlined=True)

    _delete_from_pinned_cache(page, _CENTRE_TILE_URL)
    page.evaluate("() => window.pwaDownloadedOverlay.refresh()")

    _wait_until_outlined(page, "CH-4115", outlined=False)


def test_outline_is_per_basemap(pwa_page: PwaPage) -> None:
    """Tiles cached for one basemap say nothing about another.

    Mirrors the roundel's own per-basemap behaviour — download on one
    basemap, switch, and the region is no longer available offline on the
    basemap you are now looking at.
    """
    page = _boot(pwa_page)
    _select_region_with_id(page, "CH-4115")
    _wait_for_state(page, "idle")
    _toggle_overlay(page, on=True)

    page.locator(_CONTROL).click()
    _wait_for_state(page, "done", timeout=10000)
    _wait_until_outlined(page, "CH-4115", outlined=True)

    # Re-point the tile template at a different origin and re-probe, which
    # is what a basemap swap amounts to for this overlay.
    _stub_active_basemap_template(page, "https://other.example.invalid/{z}/{x}/{y}.pbf")
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'))"
    )

    _wait_until_outlined(page, "CH-4115", outlined=False)


def test_custom_area_source_starts_empty(pwa_page: PwaPage) -> None:
    """With no saved custom area the area source holds no features."""
    page = _boot(pwa_page)
    _toggle_overlay(page, on=True)

    data = cast(
        dict[str, Any],
        page.evaluate(
            "() => MAP.getSource('downloaded-area').serialize().data",
        ),
    )
    assert data.get("features") == []
