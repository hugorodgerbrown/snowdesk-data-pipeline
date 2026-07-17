"""
tests/e2e/test_pwa_lifecycle_update.py — SNOW-389 real-SW update journeys.

Covers Scenarios P4, P5, P6 (docs/testing-scenarios.md §"PWA Shell"): the
three ways a client learns it's out of date — a new ``sw.js`` (SW-driven),
a server ``X-App-Version`` drift with an unchanged ``sw.js`` (header path),
and a server ``X-App-Min-Version`` floor the shell doesn't meet (forced
update). All three assert the central SNOW-389 invariant: the service
worker converges to a single, clean, active registration — never a
lingering ``waiting`` worker, never two active workers, never orphaned.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from playwright.sync_api import Route

from public import views as public_views
from tests.e2e.conftest import PwaPage

# ---------------------------------------------------------------------------
# P4 — SW-driven update (a new sw.js is deployed)
# ---------------------------------------------------------------------------


def test_update_banner_appears_on_new_sw_bytes(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A byte-different sw.js triggers the banner and Reload lands cleanly.

    Uses the server-side monkeypatch technique the SNOW-389 spike found
    necessary — Playwright's routing never observes a service worker's own
    script fetch, so the live server itself must return different bytes
    (see ``tests/e2e/_spike_results.py``).
    """
    page = pwa_page.page
    original_serve = public_views._serve_sw_file

    def _serve_modified(static_relative_path: str) -> HttpResponse:
        response = original_serve(static_relative_path)
        if static_relative_path == "js/sw.js":
            response.content = response.content + b"\n// lifecycle-test-marker\n"
        return response

    monkeypatch.setattr(public_views, "_serve_sw_file", _serve_modified)

    page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            await reg.update();
          }"""
    )
    page.wait_for_selector("#sw-update-banner:not(.hidden)", timeout=5000)
    pwa_page.wait_for_event("pwa.sw.update_available")

    # SW invariant, pre-reload: exactly one waiting worker parked alongside
    # the still-active one — not stuck installing, not two actives.
    pre_reload = page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            return { waiting: reg.waiting?.state ?? null, active: reg.active?.state ?? null };
          }"""
    )
    assert pre_reload == {"waiting": "installed", "active": "activated"}

    with page.expect_navigation(timeout=8000):
        page.click("#sw-update-banner-reload")

    # SW invariant, post-reload: the waiting worker activated and now
    # controls the page; no lingering waiting worker, exactly one
    # registration — the update landed in a single reload, not a loop.
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    post_reload = page.evaluate(
        """async () => {
            const regs = await navigator.serviceWorker.getRegistrations();
            const reg = await navigator.serviceWorker.getRegistration();
            return {
              count: regs.length,
              waiting: reg.waiting,
              active: reg.active?.state ?? null,
            };
          }"""
    )
    assert post_reload == {"count": 1, "waiting": None, "active": "activated"}

    # Reloading again shows no banner — already on the latest version.
    # (pwa.sw.update_applied is not asserted here: sw_register.js's own
    # comment documents it as a best-effort emit that can lose the race
    # against the reload tearing the page down before the IndexedDB write
    # settles — asserting on it would encode a known flake, not catch one.)
    page.reload()
    page.wait_for_load_state("load")
    assert "hidden" in (
        page.eval_on_selector("#sw-update-banner", "(el) => el.className") or ""
    )


# ---------------------------------------------------------------------------
# P5 — header-drift update (X-App-Version changed, sw.js did not)
# ---------------------------------------------------------------------------


def test_header_drift_shows_banner_and_clears_shell_caches(pwa_page: PwaPage) -> None:
    """An X-App-Version mismatch reveals the banner without touching the SW.

    Reload clears the shell caches before navigating (the SNOW-343 fix
    that prevents the reload-loop bug) — proven by planting a marker cache
    entry and observing it gone afterwards, since the marker cannot
    reappear on its own (nothing repopulates that specific URL).
    """
    page = pwa_page.page

    def _drift_version_header(route: Route) -> None:
        response = route.fetch()
        headers = {**response.headers, "x-app-version": "test-newer-build"}
        route.fulfill(response=response, headers=headers)

    page.route("**/api/version", _drift_version_header)
    page.evaluate("async () => { await fetch('/api/version'); }")

    page.wait_for_selector("#sw-update-banner:not(.hidden)", timeout=5000)

    # SW invariant: no waiting worker — this path never touched the SW,
    # only the version header differed.
    reg_state = page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            return { hasWaiting: !!reg.waiting, activeState: reg.active?.state ?? null };
          }"""
    )
    assert reg_state == {"hasWaiting": False, "activeState": "activated"}

    # Plant a marker in the shell cache so we can observe it being wiped —
    # nothing else in the app writes this URL back, so if it survives the
    # reload the cache was never actually cleared.
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            await cache.put('/__lifecycle-test-marker__', new Response('x'));
          }"""
    )

    with page.expect_navigation(timeout=5000):
        page.click("#sw-update-banner-reload")
    page.wait_for_load_state("load")

    # Banner is hidden again — the reloaded shell's <meta> now matches the
    # (unfaked, real) header, so pwa_version_check.js doesn't re-trigger.
    assert "hidden" in (
        page.eval_on_selector("#sw-update-banner", "(el) => el.className") or ""
    )
    assert (
        page.evaluate("() => localStorage.getItem('pwa.update.first_shown_at')") is None
    )

    marker_survived = page.evaluate(
        """async () => {
            const keys = await caches.keys();
            for (const k of keys) {
              const cache = await caches.open(k);
              if (await cache.match('/__lifecycle-test-marker__')) return true;
            }
            return false;
          }"""
    )
    assert marker_survived is False


# ---------------------------------------------------------------------------
# P6 — forced update (X-App-Min-Version floor not met)
# ---------------------------------------------------------------------------


def test_min_version_shows_modal_and_resets_cleanly(pwa_page: PwaPage) -> None:
    """An X-App-Min-Version mismatch opens the blocking modal and resets.

    ``pwa_version_check.js``'s ``inspectHeaders`` calls ``resetAndReload()``
    automatically the moment the mismatch is detected — it does not wait
    for the modal's own "Reload now" click (that handler is a redundant,
    idempotent fallback for whenever the automatic path didn't run). This
    test asserts what the shipped code actually does: the modal becomes
    visible and the page scroll locks, then the automatic reset (SW
    unregister + full cache wipe) and reload land the client cleanly —
    never stuck on the old worker.
    """
    page = pwa_page.page

    # Plant a marker so we can prove the automatic reset actually wiped
    # Cache Storage, not just relied on a coincidental repopulation.
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            await cache.put('/__min-version-marker__', new Response('x'));
          }"""
    )

    def _drift_min_version(route: Route) -> None:
        response = route.fetch()
        headers = {**response.headers, "x-app-min-version": "test-force-update"}
        route.fulfill(response=response, headers=headers)

    page.route("**/api/version", _drift_min_version)

    # inspectHeaders() reveals the modal synchronously and then kicks off
    # resetAndReload() — which can complete (and navigate) within
    # milliseconds when there's little to wipe. Capturing the modal state
    # in the same round trip as the triggering fetch, rather than in a
    # separate Python-side call afterwards, is the only way to observe it
    # reliably before the reload tears the page down.
    with page.expect_navigation(timeout=10000):
        modal_state = page.evaluate(
            """async () => {
                await fetch('/api/version');
                const modal = document.getElementById('pwa-update-modal');
                return {
                  hidden: modal.classList.contains('hidden'),
                  overflow: document.documentElement.style.overflow,
                };
              }"""
        )
    assert modal_state == {"hidden": False, "overflow": "hidden"}

    # SW invariant: converges cleanly on a fresh registration — never
    # stuck on the old (now-unregistered) worker.
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    registration_count = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations()).length"
    )
    assert registration_count == 1

    marker_survived = page.evaluate(
        """async () => {
            const keys = await caches.keys();
            for (const k of keys) {
              const cache = await caches.open(k);
              if (await cache.match('/__min-version-marker__')) return true;
            }
            return false;
          }"""
    )
    assert marker_survived is False
