"""
tests/e2e/test_download_uncontrolled_page.py — Playwright regression tests
for a basemap download started on a page no service worker controls
(SNOW-605).

The bug: ``warmCache()`` in ``static/js/sw_register.js`` read
``navigator.serviceWorker.controller`` once and resolved ``null`` when it was
absent. ``map.js``'s ``finish()`` cannot tell that ``null`` from a run that
fetched nothing, so it painted the error roundel and raised the FALLBACK
toast — "Download failed. Check your connection and try again." — for a
failure that never touched the network and made no request at all. The
network panel stayed empty, so the message pointed at the one component that
was demonstrably fine.

A page is uncontrolled in two ordinary situations, neither of them a fault:

* the activation window of a service-worker update, which staging enters on
  essentially every deploy (the shell hash, and so ``CACHE_VERSION``, changes
  whenever the shell does — SNOW-590); and
* for the entire life of a shift-reloaded document, which Chrome deliberately
  loads without a controller. No controller is ever coming, so retrying on
  that same document reproduces the failure indefinitely — only a reload
  cures it.

These tests mask ``navigator.serviceWorker.controller`` to reproduce that
state deterministically, rather than racing a real activation window. They
assert the download reports the state accurately: the dedicated
``#map-download-error-toast-no-worker`` toast (which says to reload), NOT the
connection-blaming fallback, and no warm-cache dispatch.

Reuses ``test_cache_this_area.py``'s helpers — see that module's docstring
for why the synthetic region injection and template stub are needed.
"""

from __future__ import annotations

from playwright.sync_api import Page

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _MICRO_SUMMARY,
    _reload_home,
    _select_region,
    _stub_active_basemap_template,
    _stub_region_basemap_tiles,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

# Masks the controller for the life of the document, which is exactly what a
# shift-reloaded page looks like to `warmCache()`. Defined as a getter so the
# live read in `_activeWorker()` sees null every time, not just once.
_DROP_CONTROLLER = """
() => {
  Object.defineProperty(navigator.serviceWorker, 'controller', {
    configurable: true,
    get: () => null,
  });
}
"""


def _toast_visible(page: Page, toast_id: str) -> bool:
    """Whether ``toast_id`` is revealed.

    Visibility is the ``hidden`` CLASS, not the ``hidden`` attribute — an
    ``el.hidden`` check reports every one of these toasts as visible.
    """
    return bool(
        page.evaluate(
            "(id) => { const el = document.getElementById(id);"
            " return !!el && !el.classList.contains('hidden'); }",
            toast_id,
        )
    )


def test_uncontrolled_page_raises_the_reload_toast_not_the_connection_one(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """An uncontrolled page names the real problem and tells the user to reload."""
    _reload_home(pwa_page)
    page = pwa_page.page
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#map-download-control")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    page.evaluate(_DROP_CONTROLLER)
    icon.click()
    _wait_for_state(page, "error", timeout=15000)

    assert _toast_visible(page, "map-download-error-toast-no-worker"), (
        "expected the reload toast for a page with no controller"
    )
    # The regression: this fallback toast blames the connection for a failure
    # that made no request, and its "try again" cannot succeed on a document
    # that will never gain a controller.
    assert not _toast_visible(page, "map-download-error-toast"), (
        "the connection-blaming fallback toast must not be used for this failure"
    )
    assert not _toast_visible(page, "map-download-error-toast-quota")
    assert not _toast_visible(page, "map-download-error-toast-budget")


def test_uncontrolled_page_dispatches_no_warm_cache_run(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """The failure is reported without any request being made."""
    _reload_home(pwa_page)
    page = pwa_page.page
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#map-download-control")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    tile_requests: list[str] = []
    page.context.on(
        "request",
        lambda r: tile_requests.append(r.url) if ".mvt" in r.url else None,
    )

    page.evaluate(_DROP_CONTROLLER)
    icon.click()
    _wait_for_state(page, "error", timeout=15000)

    assert tile_requests == [], f"expected no tile requests, saw {len(tile_requests)}"


def test_controller_arriving_late_still_downloads(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """A momentarily-uncontrolled page waits, then downloads normally.

    This is the update-activation case, as opposed to the shift-reload case
    above: the controller is absent at the moment of the click and arrives a
    beat later. The old code had already given up by then.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    # The dispatch is what's under test, not the fetching — stub the worker's
    # warm-cache the same way the other download tests do, so 'done' depends
    # only on the message arriving.
    _stub_warm_cache(
        page.context.service_workers[0], ok=1, failed=0, progress_steps=[(1, 1)]
    )

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#map-download-control")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    # Hide the controller, then restore it and fire controllerchange — the
    # shape of a new worker claiming the page mid-click.
    page.evaluate(
        """
        () => {
          const real = navigator.serviceWorker.controller;
          Object.defineProperty(navigator.serviceWorker, 'controller', {
            configurable: true,
            get: () => window.__snow605Restored ? real : null,
          });
        }
        """
    )
    icon.click()
    page.evaluate(
        """
        () => {
          window.__snow605Restored = true;
          navigator.serviceWorker.dispatchEvent(new Event('controllerchange'));
        }
        """
    )

    _wait_for_state(page, "done", timeout=20000)
    assert not _toast_visible(page, "map-download-error-toast-no-worker")
    assert not _toast_visible(page, "map-download-error-toast")
