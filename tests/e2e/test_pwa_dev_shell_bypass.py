"""
tests/e2e/test_pwa_dev_shell_bypass.py — SNOW-585 dev shell-cache bypass.

Covers the two claims the bypass makes:

  1. With ``DEV_SHELL_BYPASS`` baked into the controlling worker,
     ``_staleWhileRevalidate`` never reads or writes the shell cache — a
     ``static``-classified request always goes straight to the network,
     even against a cache already primed with stale content (models the
     "old worker still in control after a git pull" failure mode the
     bypass exists to fix).
  2. With ``settings.SW_DEV_SHELL_BYPASS`` on, the update banner (and its
     ``pwa.sw.update_available`` telemetry) never appears for a
     byte-different ``sw.js`` — showing it would tell the developer to do
     something the bypass has already done.

As with ``tests/e2e/test_pwa_lifecycle_update.py``, byte-changing
``sw.js`` must go through ``monkeypatch.setattr(public_views,
"_serve_sw_file", …)`` — Playwright cannot intercept a service worker's
own script fetch.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import override_settings

from apps.public import views as public_views
from tests.e2e.conftest import PwaPage

# ---------------------------------------------------------------------------
# Bypass on — the shell cache is skipped entirely (no read, no write)
# ---------------------------------------------------------------------------


def test_dev_shell_bypass_serves_fresh_bytes_without_touching_the_cache(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bypass-enabled worker never reads or writes the shell cache.

    Forces ``DEV_SHELL_BYPASS = true`` into the served ``sw.js`` body
    (independent of ``settings.SW_DEV_SHELL_BYPASS`` — this test only
    cares about the worker-side behaviour) and drives the standard
    install → waiting → SKIP_WAITING → activate dance so that worker
    actually takes control. A stale entry is then planted directly in the
    shell cache for a ``STATIC_PATHS`` URL — modelling a resource cached
    before the underlying data changed — and a fresh fetch of the same URL
    must return the live (routed) content, never the stale cache entry,
    and must not overwrite the cache entry either ("off means off").
    """
    page = pwa_page.page
    original_serve = public_views._serve_sw_file

    def _serve_bypass_on(static_relative_path: str) -> HttpResponse:
        response = original_serve(static_relative_path)
        if static_relative_path == "js/sw.js":
            response.content = response.content.replace(
                b"const DEV_SHELL_BYPASS = false;",
                b"const DEV_SHELL_BYPASS = true;",
            )
        return response

    monkeypatch.setattr(public_views, "_serve_sw_file", _serve_bypass_on)

    # Standard SW update dance (mirrors test_pwa_lifecycle_update.py's P4):
    # install the bypass-enabled worker, then activate it via the banner's
    # own Reload button so the real production code path drives the swap.
    page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            await reg.update();
          }"""
    )
    page.wait_for_selector("#sw-update-banner:not(.hidden)", timeout=5000)
    with page.expect_navigation(timeout=8000):
        page.click("#sw-update-banner-reload")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )

    # Plant a stale entry directly in the shell cache for a STATIC_PATHS
    # URL — nothing in the app repopulates this specific cache key on its
    # own, so its survival (or replacement) is entirely down to whether
    # _staleWhileRevalidate touched it.
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            await cache.put('/api/regions.geojson', new Response('stale-marker'));
          }"""
    )

    fresh_body = page.evaluate(
        "async () => (await fetch('/api/regions.geojson')).text()"
    )
    assert "stale-marker" not in fresh_body

    cached_body = page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            const cached = await cache.match('/api/regions.geojson');
            return cached ? await cached.text() : null;
          }"""
    )
    assert cached_body == "stale-marker"


# ---------------------------------------------------------------------------
# Bypass on — the update banner is suppressed
# ---------------------------------------------------------------------------


@override_settings(SW_DEV_SHELL_BYPASS=True)
def test_dev_shell_bypass_suppresses_the_update_banner(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The update banner never appears while ``SW_DEV_SHELL_BYPASS`` is on.

    A byte-different ``sw.js`` still triggers an install — that half of
    the lifecycle is unchanged — but ``sw_register.js::showUpdateBanner``
    returns before revealing the DOM or emitting
    ``pwa.sw.update_available`` once the ``pwa-dev-shell-bypass`` meta tag
    is present. The already-registered page is reloaded once, inside the
    ``override_settings`` context, so the reloaded HTML carries the meta
    tag (``pwa_page`` navigates during fixture setup, before this
    decorator takes effect).
    """
    page = pwa_page.page
    page.reload()
    page.wait_for_load_state("load")

    original_serve = public_views._serve_sw_file

    def _serve_modified(static_relative_path: str) -> HttpResponse:
        response = original_serve(static_relative_path)
        if static_relative_path == "js/sw.js":
            response.content = response.content + b"\n// dev-shell-bypass-test-marker\n"
        return response

    monkeypatch.setattr(public_views, "_serve_sw_file", _serve_modified)

    # Capture emitted telemetry event names so we can assert
    # pwa.sw.update_available never fires alongside the banner staying hidden.
    page.evaluate(
        """() => {
            window.__recordedEvents = [];
            const emit = window.pwaTelemetry && window.pwaTelemetry.emit;
            if (window.pwaTelemetry && emit) {
              window.pwaTelemetry.emit = (name, props) => {
                window.__recordedEvents.push(name);
                return emit(name, props);
              };
            }
          }"""
    )

    page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            await reg.update();
          }"""
    )
    # No selector to wait for (the banner must never appear) — a fixed
    # settle window covers the install/statechange sequence that would
    # otherwise reveal it.
    page.wait_for_timeout(2000)

    assert "hidden" in (
        page.eval_on_selector("#sw-update-banner", "(el) => el.className") or ""
    )
    recorded_events = page.evaluate("() => window.__recordedEvents || []")
    assert "pwa.sw.update_available" not in recorded_events
