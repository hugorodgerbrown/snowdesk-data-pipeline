"""
tests/e2e/test_cache_this_area.py — Playwright regression tests for
SNOW-493 findings 6 and 7: "Cache this area for offline"'s URL coverage
and its completion toast.

Finding 6: ``cacheNowInit()`` (``static/js/map.js``) used to build its
warm-cache URL list from a hardcoded ``?country=ch``, so a user who'd
also enabled a foreign country would have that country's regions vanish
on the next offline reload despite the control reporting success. It
must now iterate every currently-enabled country (``COUNTRY_STATE``).

Finding 7: the completion toast used to be a single "available offline"
message shown for any non-null result, even one where every URL failed
to cache. It must now branch on the ``sw.js``-reported ``{ok, failed}``
counts into three distinct toasts — complete / partial / failed — and
"failed" (``ok === 0``) must never claim "available offline".

Both need full, deterministic control over what the SW's warm-cache
handler reports back, without depending on a live basemap CDN (documented
elsewhere — ``test_offline_basemap_cache.py``, ``test_offline_map.py`` — as
unreachable/flaky in this harness) and without being able to intercept the
requests via ``page.route()`` (a service worker's own ``fetch()`` calls
inside its message handler are invisible to it — confirmed empirically
while building this test). ``sw.js`` declares ``_warmCache`` as a
top-level classic-script function (no enclosing IIFE), so
``worker.evaluate()`` (the same technique
``test_offline_basemap_cache.py`` uses to seed SW-internal state) can
directly reassign ``self._warmCache`` to a stub that returns a chosen
``{ok, failed}`` and records the URL list it was called with — driving
the REAL page-side event dispatch, the REAL ``cacheNowInit()`` URL
assembly and toast-selection logic, and the REAL
``sw_register.js``/``sw.js`` message round trip (SNOW-493 finding 9),
while only the actual network/cache-write step is substituted.
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page, Worker as SWWorker

from tests.e2e.conftest import PwaPage


def _stub_warm_cache(worker: SWWorker, *, ok: int, failed: int) -> None:
    """Replace ``self._warmCache`` with a stub reporting a fixed outcome.

    Records the URL list it's called with on ``self.__snow493Urls`` for
    finding 6's coverage assertions, and resolves with the caller-chosen
    ``{ok, failed}`` for finding 7's toast-selection assertions — the real
    ``sw.js``/``sw_register.js`` message round trip (including the
    SNOW-493 finding 9 requestId echo) still carries this value back to
    the page exactly as it would a genuine ``_warmCache`` result.
    """
    worker.evaluate(
        """({ ok, failed }) => {
            self._warmCache = async (urls) => {
                self.__snow493Urls = urls;
                return { ok, failed };
            };
        }""",
        {"ok": ok, "failed": failed},
    )


def _recorded_urls(worker: SWWorker) -> list[str]:
    return cast(list[str], worker.evaluate("() => self.__snow493Urls || []"))


def _wait_for_map_ready(page: Page) -> None:
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && "
        "typeof window.pwaWarmCache === 'function'"
    )


def _open_menu_and_click_cache_now(page: Page) -> None:
    page.click("#basemap-toggle")
    button = page.locator("#cache-now-toggle")
    button.wait_for(state="visible")
    button.click()


def _toast_class(page: Page, toast_id: str) -> str:
    return page.locator(f"#{toast_id}").get_attribute("class") or ""


def test_cache_this_area_covers_every_enabled_country(pwa_page: PwaPage) -> None:
    """Finding 6: the warm-cache URL list covers every enabled country.

    Enables France in the basemap picker before invoking "Cache this
    area", then asserts the SW actually received at least one
    ``?country=fr`` URL (regions/major/sub/ratings), not just the
    hardcoded ``?country=ch`` the pre-fix code always sent.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=0)

    _wait_for_map_ready(page)

    page.click("#basemap-toggle")
    country_toggle = page.locator('[data-overlay-key="country.fr"]')
    country_toggle.wait_for(state="visible")
    country_toggle.click()
    assert country_toggle.get_attribute("aria-checked") == "true"
    page.keyboard.press("Escape")  # close the menu the country click left open

    _open_menu_and_click_cache_now(page)
    page.wait_for_function(
        "() => document.getElementById('cache-now-toggle').getAttribute('aria-disabled') === null"
    )

    urls = _recorded_urls(worker)
    assert any("country=fr" in url for url in urls), (
        f"expected at least one country=fr URL among the warmed list; got {urls!r}"
    )
    assert any("country=ch" in url for url in urls), (
        f"expected country=ch to still be covered too; got {urls!r}"
    )


def test_toast_complete_when_failed_is_zero(pwa_page: PwaPage) -> None:
    """Finding 7: ``failed === 0`` shows the "complete" toast only."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=5, failed=0)

    _wait_for_map_ready(page)
    _open_menu_and_click_cache_now(page)

    page.wait_for_selector("#map-cache-now-toast-complete:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-partial")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-failed")


def test_toast_partial_when_some_urls_fail(pwa_page: PwaPage) -> None:
    """Finding 7: ``ok > 0 and failed > 0`` shows the "partial" toast only."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=3, failed=2)

    _wait_for_map_ready(page)
    _open_menu_and_click_cache_now(page)

    page.wait_for_selector("#map-cache-now-toast-partial:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-complete")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-failed")


def test_toast_failed_when_ok_is_zero(pwa_page: PwaPage) -> None:
    """Finding 7: ``ok === 0`` shows "failed", never "available offline"."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=4)

    _wait_for_map_ready(page)
    _open_menu_and_click_cache_now(page)

    page.wait_for_selector("#map-cache-now-toast-failed:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-complete")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-partial")
