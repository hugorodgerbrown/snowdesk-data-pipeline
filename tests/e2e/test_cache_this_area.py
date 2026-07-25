"""
tests/e2e/test_cache_this_area.py — Playwright regression tests for the
"Download basemap" control (SNOW-492, SNOW-493, SNOW-521).

SNOW-521 reframed the old fire-on-click "Cache this area for offline"
control as "Download basemap": clicking the menu item (``#cache-now-toggle``,
id unchanged) no longer fetches immediately — it opens a docked confirm bar
(``#basemap-download-bar``) with a live "up to N MB" estimate
(``#basemap-download-estimate``) that tracks the viewport via a ``moveend``
listener (pure arithmetic, no network), and only the bar's Download button
(``#basemap-download-confirm``) starts the actual warm-cache run. This file
covers that confirm step, the live estimate, Cancel (zero fetches), n/total
progress surfacing, and the reworded completion toasts (same three ids —
``map-cache-now-toast-{complete,partial,failed}`` — new copy). The still-
current SNOW-493 findings (every enabled country covered; toast branches on
the SW's real ``{ok, failed}`` counts) are folded in here too rather than
kept as a stale pre-SNOW-521 baseline.

Both need full, deterministic control over what the SW's warm-cache handler
reports back, without depending on a live basemap CDN (documented
elsewhere — ``test_offline_basemap_cache.py``, ``test_offline_map.py`` — as
unreachable/flaky in this harness) and without being able to intercept the
requests via ``page.route()`` (a service worker's own ``fetch()`` calls
inside its message handler are invisible to it — confirmed empirically
while building this test). ``sw.js`` declares ``_warmCache`` as a
top-level classic-script function (no enclosing IIFE), so
``worker.evaluate()`` (the same technique ``test_offline_basemap_cache.py``
uses to seed SW-internal state) can directly reassign ``self._warmCache``
to a stub that returns a chosen ``{ok, failed}`` (and, new for SNOW-521,
optionally drives ``opts.onProgress``) and records the URL list and
``pinned`` flag it was called with — driving the REAL page-side event
dispatch, the REAL ``cacheNowInit()`` URL assembly and toast-selection
logic, and the REAL ``sw_register.js``/``sw.js`` message round trip
(SNOW-493 finding 9, SNOW-521 progress messages), while only the actual
network/cache-write step is substituted.

``computeBasemapTileURLs`` is similarly stubbed at the page level
(``map.js`` is a classic, non-module script, so its top-level function
declarations are plain, reassignable ``window`` properties) — the live
"up to N MB" estimate needs a controllable tile count, and the real
basemap CDNs this function would otherwise read resolved tile templates
from are the same unreachable-in-CI origins ``test_offline_basemap_cache.py``
documents avoiding.
"""

from __future__ import annotations

from typing import Any, cast

from playwright.sync_api import Page, Worker as SWWorker

from tests.e2e.conftest import PwaPage


def _stub_warm_cache(
    worker: SWWorker,
    *,
    ok: int,
    failed: int,
    progress_steps: list[tuple[int, int]] | None = None,
    step_delay_ms: int = 0,
) -> None:
    """Replace ``self._warmCache`` with a stub reporting a fixed outcome.

    Records the URL list and the ``pinned`` flag it's called with on
    ``self.__snow493Urls`` / ``self.__snow521Pinned`` for the coverage/
    partition assertions, and resolves with the caller-chosen
    ``{ok, failed}`` for the toast-selection assertions — the real
    ``sw.js``/``sw_register.js`` message round trip (including the
    SNOW-493 finding 9 requestId echo) still carries this value back to
    the page exactly as it would a genuine ``_warmCache`` result.

    ``progress_steps``, if given, drives ``options.onProgress(done,
    total)`` (the real callback ``sw.js``'s 'warm-cache' handler passes,
    which posts a genuine 'warm-cache-progress' message) once per tuple,
    spaced ``step_delay_ms`` apart so a test can reliably observe an
    intermediate n/total state via ``page.wait_for_function`` before the
    run resolves.
    """
    worker.evaluate(
        """({ ok, failed, progressSteps, stepDelayMs }) => {
            self._warmCache = async (urls, options) => {
                self.__snow493Urls = urls;
                self.__snow521Pinned = !!(options && options.pinned);
                const onProgress = options && options.onProgress;
                for (const [done, total] of progressSteps) {
                    if (typeof onProgress === 'function') onProgress(done, total);
                    if (stepDelayMs > 0) {
                        await new Promise((resolve) => setTimeout(resolve, stepDelayMs));
                    }
                }
                return { ok, failed };
            };
        }""",
        {
            "ok": ok,
            "failed": failed,
            "progressSteps": progress_steps or [],
            "stepDelayMs": step_delay_ms,
        },
    )


def _stub_tile_urls(page: Page, *, tile_count: int) -> None:
    """Replace ``window.computeBasemapTileURLs`` with a deterministic stub.

    ``map.js`` is loaded as a classic (non-module) ``<script>``, so its
    top-level ``function computeBasemapTileURLs(...)`` declaration is a
    plain, reassignable ``window`` property — the same technique
    ``_stub_warm_cache`` above uses on the service worker's global. Real
    tile enumeration needs a resolved basemap-origin tile template, which
    (per this module's docstring) is unreachable in this harness; this
    stub returns ``tile_count`` synthetic URLs (capped at ``maxTiles`` when
    supplied — mirroring the real function's contract) so the docked bar's
    live estimate and over-ceiling behaviour are exercisable without one.
    """
    page.evaluate(
        """(tileCount) => {
            window.computeBasemapTileURLs = (_map, maxTiles) => {
                const n = typeof maxTiles === 'number' ? Math.min(tileCount, maxTiles) : tileCount;
                return Array.from({ length: Math.max(0, n) }, (_, i) => (
                    'https://tiles.example.invalid/' + i
                ));
            };
        }""",
        tile_count,
    )


def _recorded_urls(worker: SWWorker) -> list[str]:
    return cast(list[str], worker.evaluate("() => self.__snow493Urls || []"))


def _recorded_pinned(worker: SWWorker) -> Any:
    return worker.evaluate("() => self.__snow521Pinned")


def _wait_for_map_ready(page: Page) -> None:
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && "
        "typeof window.pwaWarmCache === 'function' && "
        "typeof window.pwaBasemapDownloadCore !== 'undefined'"
    )


def _download_ceiling_tiles(page: Page) -> int:
    return cast(
        int,
        page.evaluate(
            "() => window.pwaBasemapDownloadCore.BASEMAP_DOWNLOAD_CEILING_TILES"
        ),
    )


def _open_download_bar(page: Page) -> None:
    """Open the layers menu and click "Download basemap" (framing phase).

    SNOW-521: unlike the pre-SNOW-521 control, this no longer fetches —
    it only reveals ``#basemap-download-bar`` and computes the initial
    estimate. Callers that want to actually run the download must
    separately click ``#basemap-download-confirm``.
    """
    page.click("#basemap-toggle")
    button = page.locator("#cache-now-toggle")
    button.wait_for(state="visible")
    button.click()
    page.locator("#basemap-download-bar").wait_for(state="visible")


def _estimate_text(page: Page) -> str:
    return str(page.locator("#basemap-download-estimate").inner_text())


def _toast_class(page: Page, toast_id: str) -> str:
    return page.locator(f"#{toast_id}").get_attribute("class") or ""


def test_download_bar_shows_live_estimate_on_open(pwa_page: PwaPage) -> None:
    """Framing phase: opening the bar shows an "up to N MB" estimate.

    5 tiles * 100 KB worst-case-per-tile = 500,000 bytes, which rounds up
    to 1 MB — ``formatUpToMB``'s ceiling behaviour, exercised end to end
    through the docked bar rather than just the core unit tests.
    """
    page = pwa_page.page
    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)

    _open_download_bar(page)

    page.wait_for_function(
        "() => document.getElementById('basemap-download-estimate').textContent.includes('up to 1 MB')"
    )
    assert "Download basemap" in _estimate_text(page)
    confirm = page.locator("#basemap-download-confirm")
    assert confirm.get_attribute("aria-disabled") is None


def test_download_bar_estimate_updates_on_moveend(pwa_page: PwaPage) -> None:
    """The estimate stays live as the viewport changes — no network.

    Opens the bar at a small tile count, then swaps the stub to a larger
    count and pans the real map (a genuine ``moveend``, not a synthetic
    dispatch) — cacheNowInit's ``recompute()`` must re-read the (stubbed)
    tile count and update the span's text purely from that listener, with
    zero fetches involved either way (arithmetic only).
    """
    page = pwa_page.page
    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)

    _open_download_bar(page)
    page.wait_for_function(
        "() => document.getElementById('basemap-download-estimate').textContent.includes('up to 1 MB')"
    )

    # 50 tiles * 100 KB = 5,000,000 bytes -> ceil to 5 MB.
    _stub_tile_urls(page, tile_count=50)
    page.evaluate("() => MAP.panBy([80, 0], { duration: 0 })")

    page.wait_for_function(
        "() => document.getElementById('basemap-download-estimate').textContent.includes('up to 5 MB')"
    )


def test_download_bar_disables_confirm_over_ceiling(pwa_page: PwaPage) -> None:
    """Over the download ceiling: refuse rather than start an unbounded run."""
    page = pwa_page.page
    _wait_for_map_ready(page)
    ceiling = _download_ceiling_tiles(page)
    _stub_tile_urls(page, tile_count=ceiling + 1)

    _open_download_bar(page)

    page.wait_for_function(
        "() => document.getElementById('basemap-download-estimate').textContent.includes("
        "'Area too large — zoom in to download')"
    )
    confirm = page.locator("#basemap-download-confirm")
    assert confirm.get_attribute("aria-disabled") == "true"
    assert confirm.is_disabled()


def test_cancel_makes_zero_fetches(pwa_page: PwaPage) -> None:
    """Cancel tears the bar down without ever posting a warm-cache call."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=0)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)

    page.click("#basemap-download-cancel")
    page.locator("#basemap-download-bar").wait_for(state="hidden")

    # Give any (unexpected) in-flight postMessage round trip a moment to
    # land before asserting on it — the stub would have recorded the URL
    # list synchronously once _warmCache was actually invoked.
    page.wait_for_timeout(200)
    assert _recorded_urls(worker) == []


def test_download_confirm_covers_every_enabled_country_and_pins_the_run(
    pwa_page: PwaPage,
) -> None:
    """SNOW-493 finding 6 + SNOW-521 pinning: both still hold post-rework.

    Enables France in the basemap picker before opening the bar, then
    confirms the download and asserts the SW actually received at least
    one ``?country=fr`` URL (regions/major/sub/ratings) alongside the
    existing ``?country=ch`` coverage, AND that the call was made with
    ``pinned: true`` (routing basemap-origin writes to the dedicated,
    LRU-exempt BASEMAP_PINNED_CACHE).
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=0)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)

    page.click("#basemap-toggle")
    country_toggle = page.locator('[data-overlay-key="country.fr"]')
    country_toggle.wait_for(state="visible")
    country_toggle.click()
    assert country_toggle.get_attribute("aria-checked") == "true"
    page.keyboard.press("Escape")  # close the menu the country click left open

    _open_download_bar(page)
    page.click("#basemap-download-confirm")
    page.locator("#basemap-download-bar").wait_for(state="hidden")

    urls = _recorded_urls(worker)
    assert any("country=fr" in url for url in urls), (
        f"expected at least one country=fr URL among the warmed list; got {urls!r}"
    )
    assert any("country=ch" in url for url in urls), (
        f"expected country=ch to still be covered too; got {urls!r}"
    )
    assert _recorded_pinned(worker) is True


def test_download_confirm_surfaces_progress(pwa_page: PwaPage) -> None:
    """n/total progress reaches the page mid-run, before the final toast."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(
        worker,
        ok=4,
        failed=0,
        progress_steps=[(1, 4), (2, 4), (3, 4), (4, 4)],
        step_delay_ms=150,
    )

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)
    page.click("#basemap-download-confirm")

    page.wait_for_function(
        "() => document.getElementById('basemap-download-estimate').textContent.includes("
        "'Downloading basemap — 1/4')",
        timeout=5000,
    )
    page.wait_for_selector("#map-cache-now-toast-complete:not(.hidden)", timeout=10000)


def test_toast_complete_when_failed_is_zero(pwa_page: PwaPage) -> None:
    """``failed === 0`` shows the "complete" toast, with the reworded copy."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=5, failed=0)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)
    page.click("#basemap-download-confirm")

    toast = page.locator("#map-cache-now-toast-complete")
    toast.wait_for(state="visible", timeout=10000)
    assert "now available offline" in toast.inner_text()
    assert "hidden" in _toast_class(page, "map-cache-now-toast-partial")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-failed")


def test_toast_partial_when_some_urls_fail(pwa_page: PwaPage) -> None:
    """``ok > 0 and failed > 0`` shows the "partial" toast only."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=3, failed=2)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)
    page.click("#basemap-download-confirm")

    page.wait_for_selector("#map-cache-now-toast-partial:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-complete")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-failed")


def test_toast_failed_when_ok_is_zero(pwa_page: PwaPage) -> None:
    """``ok === 0`` shows "failed", never "available offline"."""
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=4)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)
    page.click("#basemap-download-confirm")

    page.wait_for_selector("#map-cache-now-toast-failed:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-complete")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-partial")


def test_toast_never_complete_for_vacuous_run(pwa_page: PwaPage) -> None:
    """``ok === 0`` never claims "available offline", even with no failures.

    A vacuous run — ``{ok: 0, failed: 0}``, nothing cached — has no
    successes, so the "complete" toast must stay hidden even though there
    were no failures. "Complete" requires ``ok > 0`` as well as
    ``failed === 0``.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=0)

    _wait_for_map_ready(page)
    _stub_tile_urls(page, tile_count=5)
    _open_download_bar(page)
    page.click("#basemap-download-confirm")

    page.wait_for_selector("#map-cache-now-toast-failed:not(.hidden)", timeout=10000)
    assert "hidden" in _toast_class(page, "map-cache-now-toast-complete")
    assert "hidden" in _toast_class(page, "map-cache-now-toast-partial")
