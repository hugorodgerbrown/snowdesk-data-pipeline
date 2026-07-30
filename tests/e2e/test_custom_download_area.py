"""
tests/e2e/test_custom_download_area.py — Playwright regression tests for the
"Download a custom area" basemap control (SNOW-522).

Companion to ``test_cache_this_area.py``'s per-region control: this one has
no fixed region to size ahead of time, so clicking
``#map-custom-download-control`` opens a framing overlay
(``#map-frame-overlay``) instead of downloading immediately — a
Google-Maps-style dim mask with a fixed, centred frame the user pans/zooms
the map underneath, with a live "up to N MB" readout (computed entirely
client-side — ``static/js/basemap_download_core.js``, no network round
trip) and a docked Cancel/Download CTA bar.

Reuses ``test_cache_this_area.py``'s helpers (``_reload_home``,
``_stub_warm_cache``, ``_stub_active_basemap_template``,
``_wait_for_map_ready``, ``_wait_for_state`` — the last now takes an
optional ``selector`` so both controls' ``data-download-state`` idiom can
share one waiter) rather than duplicating them, and the same rationale for
why none of these tests wait for a real MapLibre STYLE load: the real
basemap CDN is unreachable in this harness. Unlike the per-region control,
though, this one genuinely needs a working ``MAP`` transform (``unproject``,
``fitBounds``) — that is available synchronously off the ``bounds``
constructor option the moment ``new maplibregl.Map(...)`` returns, well
before the style network fetch resolves (confirmed by
``test_map_placement_focus.py`` and ``test_place_pin_clearance.py``, which
also call ``MAP.unproject``/``MAP.setCenter`` without waiting for
``MAP.loaded()``), so ``_wait_for_map_ready`` alone is enough here too.

The dim-mask assertion reads the frame rect's computed ``box-shadow``
rather than diffing a screenshot — matching this suite's established
"assert computed style, not pixels" convention (see
``test_map_placement_focus.py``'s module docstring) — a canvas/DOM
screenshot would depend on frame timing this harness deliberately avoids.

Covers: opening framing dims the map and shows the frame; the readout
tracks the frame as the map moves; a confirmed download warms with
``pinned: true``, reaches ``done``, and notifies the layers sync
dashboard; reload + click the (probed) green roundel re-opens framing at
the saved area; moving the frame then Cancelling leaves the saved area
untouched; moving the frame then confirming evicts the old area's tiles
from the pinned cache before warming the new set.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page, Worker as SWWorker

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _STUB_TEMPLATE,
    _reload_home,
    _stub_active_basemap_template,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

pytestmark = pytest.mark.usefixtures("_load_test_data")

_CONTROL = "#map-custom-download-control"
_PINNED_CACHE_PREFIX = "snowdesk-basemap-pinned-"


def _boot(pwa_page: PwaPage) -> tuple[Page, SWWorker]:
    """Reload, wait for the map + SW, and stub the active basemap template.

    Every test needs this same sequence; SW round trips need a genuine
    (activated) worker, hence the assertion below rather than a silent
    empty list.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    return page, worker


def _open_framing(page: Page) -> None:
    """Click the roundel and wait for the overlay to reveal itself."""
    page.click(_CONTROL)
    page.wait_for_selector("#map-frame-overlay:not([hidden])")


def _wait_for_overlay_closed(page: Page) -> None:
    page.wait_for_selector("#map-frame-overlay[hidden]", state="attached")


def _readout_text(page: Page) -> str:
    return page.locator("#map-frame-readout").inner_text()


def _move_the_frame(page: Page) -> None:
    """Change the ground area under the fixed-pixel frame.

    A zoom change (rather than a pan) is used deliberately: the frame's
    on-screen size never changes, so a pan alone can leave the framed
    area's SIZE (and so its tile count / mb) numerically unchanged even
    though its position did — a zoom change always changes the ground
    footprint under a fixed-pixel frame. Clamped short of ``maxZoom``
    (18, static/js/map.js) so the jump can never be rejected.
    """
    page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 3, 17))")
    # The 'move' handler recomputes the readout synchronously; poll for a
    # non-empty readout as a settle signal rather than a fixed sleep.
    page.wait_for_function(
        "() => document.getElementById('map-frame-readout').innerText.length > 0"
    )


def _confirm_download(
    page: Page, worker: SWWorker, *, ok: int = 1, failed: int = 0
) -> None:
    """Stub a warm-cache outcome and click Download, waiting for it to settle."""
    _stub_warm_cache(worker, ok=ok, failed=failed)
    page.click("#map-frame-confirm")
    _wait_for_state(page, "busy", selector=_CONTROL)
    expected = "done" if (ok > 0 and failed == 0) else "idle"
    _wait_for_state(page, expected, selector=_CONTROL, timeout=10000)


def _saved_area(page: Page) -> dict[str, Any] | None:
    """The persisted ``basemap.customArea`` meta:app row's value, or None."""
    row = page.evaluate("() => window.pwaDb.get('meta:app', 'basemap.customArea')")
    return cast("dict[str, Any] | None", row["value"] if row else None)


def _centre_tile_url(page: Page, centre_tile: dict[str, Any]) -> str:
    """The done-probe URL for `centre_tile`, under the stubbed template."""
    return cast(
        str,
        page.evaluate(
            """({ template, centreTile }) =>
                self.pwaBasemapDownloadCore.centreTileURL(template, { centre_tile: centreTile })
            """,
            {"template": _STUB_TEMPLATE, "centreTile": centre_tile},
        ),
    )


def _pinned_cache_has(page: Page, url: str) -> bool:
    """Whether `url` is present in the (single) prefix-matched pinned cache."""
    return bool(
        page.evaluate(
            """async ({ url, prefix }) => {
                const names = await caches.keys();
                const name = names.find((n) => n.startsWith(prefix));
                if (!name) return false;
                const cache = await caches.open(name);
                return !!(await cache.match(url));
            }""",
            {"url": url, "prefix": _PINNED_CACHE_PREFIX},
        )
    )


def test_opening_framing_dims_the_map_and_shows_the_frame(pwa_page: PwaPage) -> None:
    """Clicking the roundel reveals the dim mask, the frame, and a size readout."""
    page, _worker = _boot(pwa_page)
    overlay = page.locator("#map-frame-overlay")
    assert overlay.get_attribute("hidden") is not None

    _open_framing(page)

    assert overlay.get_attribute("hidden") is None
    frame_box = page.locator("#map-frame-rect").bounding_box()
    assert frame_box is not None
    assert frame_box["width"] > 0
    assert frame_box["height"] > 0
    box_shadow = page.evaluate(
        "() => getComputedStyle(document.getElementById('map-frame-rect')).boxShadow"
    )
    assert "9999px" in box_shadow, "the dim mask is a 9999px box-shadow spread"
    assert "MB" in _readout_text(page)


def test_readout_tracks_the_frame_as_the_map_moves(pwa_page: PwaPage) -> None:
    """The "up to N MB" readout updates live as the framed area changes."""
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    before = _readout_text(page)

    page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 3, 17))")
    page.wait_for_function(
        """(before) => document.getElementById('map-frame-readout').innerText !== before""",
        arg=before,
    )

    after = _readout_text(page)
    assert after != before
    assert "MB" in after


def test_download_warms_pinned_and_notifies_the_sync_dashboard(
    pwa_page: PwaPage,
) -> None:
    """Confirming warms with pinned:true, reaches done, and closes the overlay.

    Also covers the "notify the layers sync dashboard" invariant —
    ``window.pwaLayerSyncStatus?.refresh()`` must run after every completed
    download, region or custom-area alike.
    """
    page, worker = _boot(pwa_page)
    page.evaluate(
        "() => { window.__snow522Refreshed = false; "
        "window.pwaLayerSyncStatus = { refresh: () => { window.__snow522Refreshed = true; } }; }"
    )
    _open_framing(page)

    _confirm_download(page, worker)

    _wait_for_overlay_closed(page)
    urls = cast("list[str]", worker.evaluate("() => self.__snow521Urls || []"))
    assert any(url.startswith(_STUB_TEMPLATE.split("{")[0]) for url in urls)
    assert any("country=ch" in url for url in urls)
    assert worker.evaluate("() => self.__snow521Pinned") is True
    assert page.evaluate("() => window.__snow522Refreshed") is True

    area = _saved_area(page)
    assert area is not None
    assert "bbox" in area and "band" in area and "centre_tile" in area


def test_reload_and_click_done_reopens_at_the_saved_area(pwa_page: PwaPage) -> None:
    """A completed download survives reload; clicking the green roundel
    re-opens framing with the map recentred on the saved area.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area = _saved_area(page)
    assert area is not None

    page, worker = _boot(pwa_page)
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)

    _open_framing(page)

    west, south, east, north = area["bbox"]
    centre = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lng, c.lat]; }"
    )
    # fitBounds is not pixel-exact, but the map's centre after re-opening
    # at the saved area must land inside (or very near) its footprint.
    assert west - 0.01 <= centre[0] <= east + 0.01
    assert south - 0.01 <= centre[1] <= north + 0.01


def test_move_then_cancel_leaves_the_saved_area_intact(pwa_page: PwaPage) -> None:
    """Moving the frame and Cancelling touches neither the saved row nor the cache."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area_before = _saved_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _open_framing(page)
    _move_the_frame(page)
    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)

    assert _saved_area(page) == area_before
    assert _pinned_cache_has(page, url_before)


def test_move_then_confirm_evicts_the_old_areas_tiles(pwa_page: PwaPage) -> None:
    """Confirming a moved frame evicts the previous area's pinned tiles first."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area_before = _saved_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _open_framing(page)
    _move_the_frame(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)

    area_after = _saved_area(page)
    assert area_after is not None
    assert area_after != area_before
    url_after = _centre_tile_url(page, area_after["centre_tile"])

    assert not _pinned_cache_has(page, url_before), (
        "the old area's tiles should have been evicted before the new set warmed"
    )
    assert _pinned_cache_has(page, url_after)
