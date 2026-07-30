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
    # str(), not cast(): Playwright's own stubs already type inner_text()
    # as str (unlike evaluate() below, which is genuinely Any in Playwright's
    # own stubs regardless of environment) — a cast is "redundant" whenever
    # those stubs are resolvable and only needed when they aren't (e.g. the
    # tox mypy env's `type` dependency group deliberately excludes
    # playwright — see pyproject.toml). str() satisfies both consistently.
    return str(page.locator("#map-frame-readout").inner_text())


_SYNC_SENTINEL = "__snow522_not_refreshed__"


def _arm_sync_dashboard_probe(page: Page) -> None:
    """Stamp a sentinel on every sync dot so a later ``refresh()`` is observable.

    ``window.pwaLayerSyncStatus`` cannot be spied on: it is installed with
    ``Object.defineProperty(window, …, {writable: false, configurable:
    false})`` over an ``Object.freeze``d value
    (``static/js/map_layer_sync_status.js``), so assigning a stub over it
    silently no-ops in sloppy mode and the assertion can never pass.

    ``refresh()`` rewrites ``data-sync-state`` on every dot it probes, so
    stamping a sentinel first and asserting it is gone afterwards observes
    the real call rather than a stub of it.
    """
    page.evaluate(
        """(sentinel) => {
            document
                .querySelectorAll('#basemap-menu .sync-dot')
                .forEach((dot) => { dot.dataset.syncState = sentinel; });
        }""",
        _SYNC_SENTINEL,
    )


def _assert_sync_dashboard_refreshed(page: Page) -> None:
    """Wait for ``refresh()`` to clear the sentinel stamped by the arm step."""
    page.wait_for_function(
        """(sentinel) => {
            const dots = document.querySelectorAll('#basemap-menu .sync-dot');
            return (
                dots.length > 0 &&
                [...dots].some((dot) => dot.dataset.syncState !== sentinel)
            );
        }""",
        arg=_SYNC_SENTINEL,
        timeout=10000,
    )


def _force_done_reprobe(page: Page) -> None:
    """Re-run the control's done-probe now that the tile template is stubbed.

    The probe keys off the ACTIVE basemap's tile template, which ``_boot``
    only stubs *after* the map is ready — by which point the control has
    already painted from a probe that had no template to look up. The real
    app re-probes on ``snowdesk:basemap-changed`` (the download is
    per-basemap), and the control listens for it, so dispatching it is the
    supported way to ask for a fresh probe. ``test_cache_this_area.py``
    gets the same effect for the region control by re-selecting the region.
    """
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'))"
    )


def _frame_a_downloadable_area(page: Page) -> None:
    """Zoom in until the framed area is under the download ceiling.

    The homepage's default view frames most of Switzerland, which across
    the z10–z14 band is comfortably over ``DOWNLOAD_CEILING_MB`` (200) —
    so framing mode legitimately opens with ``#map-frame-confirm``
    disabled and the readout reading "Area too large to download". Every
    confirm-path test therefore has to zoom in first; without this the
    click just times out against a disabled button.

    Steps until the button enables rather than jumping to a hardcoded
    zoom, so this stays correct if the frame's CSS size, the default
    viewport, or the ceiling constant changes.
    """
    confirm = page.locator("#map-frame-confirm")
    for _ in range(10):
        if not confirm.is_disabled():
            return
        page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 1, 16))")
        # The 'move' handler recomputes the readout synchronously; poll for
        # a non-empty readout as a settle signal rather than a fixed sleep.
        page.wait_for_function(
            "() => document.getElementById('map-frame-readout').innerText.length > 0"
        )
    raise AssertionError(
        "the framed area never dropped under the download ceiling — "
        f"readout still reads {_readout_text(page)!r}"
    )


def _move_the_frame(page: Page) -> None:
    """Move the frame onto ground that does not overlap the previous area.

    Deliberately a PAN, not a zoom. Zooming in leaves the new framed area
    a strict *subset* of the old one, so the old area's tiles — including
    its centre tile — are legitimately members of the new set too: the
    evict-then-warm sequence deletes them and immediately re-adds them,
    and an eviction assertion keyed on the old centre tile reads as a
    failure when the behaviour is in fact correct. Panning clear of the
    old footprint keeps the two tile sets disjoint, so eviction is
    observable.

    Panning by the frame's own width guarantees no overlap; the map's
    ``maxBounds`` (roughly the Alps, static/js/map.js) comfortably
    accommodates a frame-width step from the default view.
    """
    page.evaluate(
        """() => {
            const rect = document.getElementById('map-frame-rect').getBoundingClientRect();
            const centre = MAP.project(MAP.getCenter());
            // A full frame-width step east, in map-container pixels.
            MAP.setCenter(MAP.unproject([centre.x + rect.width, centre.y]));
        }"""
    )
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


def _display(page: Page, selector: str) -> str:
    """The computed ``display`` of `selector`, or ``"absent"`` if not in the DOM."""
    return cast(
        str,
        page.evaluate(
            """(selector) => {
                const el = document.querySelector(selector);
                return el ? getComputedStyle(el).display : 'absent';
            }""",
            selector,
        ),
    )


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


def test_framing_strips_the_map_furniture_and_cancel_restores_it(
    pwa_page: PwaPage,
) -> None:
    """Framing hides every control/ribbon/legend; Cancel puts them all back.

    Framing is a modal act with two answers, both on the CTA sheet, so the
    controls that steer the map for other purposes are hidden — otherwise
    they sit lit inside the cutout and read as part of the area being
    chosen. Asserted on computed style rather than the body class so this
    fails if the class stops driving the CSS, not just if it stops being
    set.
    """
    page, _worker = _boot(pwa_page)
    furniture = [
        "#season-ribbon",
        "#map-date-ribbon",
        "#map-utility-cluster",
        "#map-controls-br",
        "#map-legend",
    ]
    before = {sel: _display(page, sel) for sel in furniture}
    assert all(value != "none" for value in before.values()), before

    _open_framing(page)

    assert all(_display(page, sel) == "none" for sel in furniture), {
        sel: _display(page, sel) for sel in furniture
    }

    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)

    assert {sel: _display(page, sel) for sel in furniture} == before


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
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _arm_sync_dashboard_probe(page)

    _confirm_download(page, worker)

    _wait_for_overlay_closed(page)
    urls = cast("list[str]", worker.evaluate("() => self.__snow521Urls || []"))
    assert any(url.startswith(_STUB_TEMPLATE.split("{")[0]) for url in urls)
    assert any("country=ch" in url for url in urls)
    assert worker.evaluate("() => self.__snow521Pinned") is True
    _assert_sync_dashboard_refreshed(page)

    area = _saved_area(page)
    assert area is not None
    assert "bbox" in area and "band" in area and "centre_tile" in area


def test_reload_and_click_done_reopens_at_the_saved_area(pwa_page: PwaPage) -> None:
    """A completed download survives reload; clicking the green roundel
    re-opens framing with the map recentred on the saved area.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area = _saved_area(page)
    assert area is not None

    page, worker = _boot(pwa_page)
    _force_done_reprobe(page)
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
    _frame_a_downloadable_area(page)
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
    _frame_a_downloadable_area(page)
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
