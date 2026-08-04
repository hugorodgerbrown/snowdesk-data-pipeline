"""
tests/e2e/test_pwa_lifecycle_update.py — SNOW-389 real-SW update journeys.

Covers Scenarios P4, P5, P6 (docs/testing-scenarios.md §"PWA Shell"): the
three ways a client learns it's out of date — a new ``sw.js`` (SW-driven),
a server ``X-App-Version`` drift with an unchanged ``sw.js`` (header path),
and a server ``update_required`` verdict naming this build as blocked
(forced update, SNOW-609). All three assert the central SNOW-389
invariant: the service worker converges to a single, clean, active
registration — never a lingering ``waiting`` worker, never two active
workers, never orphaned.

P5b covers the inverse of P5 — the staging stuck-banner regression: a
header drift replayed from a stale cache, which the authoritative
``/api/version`` body disowns, must reveal nothing at all.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from playwright.sync_api import Route

from apps.public import views as public_views
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

    Since the stale-cache fix, a drifted header alone no longer reveals
    the banner — ``pwa_version_check.js`` confirms against the
    ``/api/version`` BODY first — so the route drifts both the header and
    the body's ``current`` field to model a genuinely newer server.
    """
    page = pwa_page.page

    def _drift_version(route: Route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["current"] = "test-newer-build"
        headers = {**response.headers, "x-app-version": "test-newer-build"}
        route.fulfill(response=response, headers=headers, json=payload)

    page.route("**/api/version", _drift_version)
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
# P5b — stale cached header (the staging stuck-banner bug)
# ---------------------------------------------------------------------------


def test_stale_cached_header_does_not_show_banner(pwa_page: PwaPage) -> None:
    """A drifted header whose authoritative body matches the shell reveals nothing.

    Models a response replayed from the browser HTTP cache right after a
    deploy: it still carries the previous build's ``X-App-Version``, but
    the server has NOT actually moved past the shell. Pre-fix, that header
    alone revealed the update banner — and because Reload only clears
    Cache Storage (never the browser HTTP cache), the stale header came
    straight back after the reload and the banner could never be cleared.
    The fix verifies drift against the ``/api/version`` body before
    showing anything.
    """
    page = pwa_page.page

    def _stale_header_only(route: Route) -> None:
        response = route.fetch()
        headers = {**response.headers, "x-app-version": "stale-old-build"}
        route.fulfill(response=response, headers=headers)

    page.route("**/api/version", _stale_header_only)

    # The wrapped fetch observes the drifted header; the verification it
    # schedules reads the (truthful) body of the same routed endpoint.
    page.evaluate("async () => { await fetch('/api/version'); }")
    page.wait_for_timeout(1000)

    assert "hidden" in (
        page.eval_on_selector("#sw-update-banner", "(el) => el.className") or ""
    )
    # The modal stays shut too — a phantom drift must never block the app.
    # (SNOW-609 removed the 24h escalation these tests also used to assert
    # against; there is no longer a localStorage stamp for a phantom banner
    # to arm. tests/js/test_pwa_version_check.js pins that it stays unwritten.)
    assert "hidden" in (
        page.eval_on_selector("#pwa-update-modal", "(el) => el.className") or ""
    )


# ---------------------------------------------------------------------------
# P6 — forced update (the server says this build is blocked)
# ---------------------------------------------------------------------------


def test_blocked_build_shows_modal_and_waits_for_the_click(
    pwa_page: PwaPage,
) -> None:
    """``update_required`` opens the blocking modal, and nothing moves until clicked.

    Both halves are SNOW-609 behaviour changes and both are the point of
    the ticket.

    The modal used to be decorative: ``pwa_version_check.js`` reset and
    reloaded the moment the verdict came back, so the page was gone before
    the copy explaining what was about to happen could be read. It now
    reveals and waits.

    The wipe used to take everything — SNOW-615 had routed it through
    ``window.pwaResetLocalData(true)``, which deletes IndexedDB and every
    Cache Storage bucket including the user's pinned basemaps. It is now
    scoped to the shell caches, so a downloaded area survives a code
    update. Both markers below are planted before the click; only the
    shell one is expected to be gone afterwards.

    Note the planted basemap bucket is asserted against by name rather
    than by stubbing ``window.pwaClearShellCachesAndReload`` — the PWA
    globals are defined non-writable, so a page-side stub silently no-ops.
    """
    page = pwa_page.page

    # Two markers: one in a shell cache (must be wiped), one in a pinned
    # basemap bucket standing in for a downloaded region (must survive).
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const shell = await caches.open(shellKey);
            await shell.put('/__blocked-build-shell-marker__', new Response('x'));
            const basemap = await caches.open('snowdesk-basemap-e2e-marker');
            await basemap.put('/__blocked-build-basemap-marker__', new Response('x'));
          }"""
    )

    def _block_this_build(route: Route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["update_required"] = True
        # The header drift is what schedules the verification round trip
        # at all; the body is what decides the outcome.
        payload["current"] = "test-newer-build"
        headers = {**response.headers, "x-app-version": "test-newer-build"}
        route.fulfill(response=response, headers=headers, json=payload)

    page.route("**/api/version", _block_this_build)
    page.evaluate("async () => { await fetch('/api/version'); }")

    page.wait_for_selector("#pwa-update-modal:not(.hidden)", timeout=5000)
    assert page.evaluate("() => document.documentElement.style.overflow") == "hidden"

    # The reveal is inert. Give the old auto-reset path time to have run,
    # then assert it did not: the shell marker is still there and no
    # navigation happened.
    page.wait_for_timeout(1000)
    assert page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            return !!(await cache.match('/__blocked-build-shell-marker__'));
          }"""
    )
    assert "hidden" not in (
        page.eval_on_selector("#pwa-update-modal", "(el) => el.className") or ""
    )

    with page.expect_navigation(timeout=10000):
        page.click("#pwa-update-modal-reload")
    page.wait_for_load_state("load")

    # SW invariant: converges cleanly on a single active registration.
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    registration_count = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations()).length"
    )
    assert registration_count == 1

    markers = page.evaluate(
        """async () => {
            const found = { shell: false, basemap: false };
            for (const k of await caches.keys()) {
              const cache = await caches.open(k);
              if (await cache.match('/__blocked-build-shell-marker__')) {
                found.shell = true;
              }
              if (await cache.match('/__blocked-build-basemap-marker__')) {
                found.basemap = true;
              }
            }
            return found;
          }"""
    )
    assert markers == {"shell": False, "basemap": True}
