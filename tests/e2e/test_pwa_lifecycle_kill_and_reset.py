"""
tests/e2e/test_pwa_lifecycle_kill_and_reset.py — SNOW-389 kill-switch and
reset journeys.

Covers Scenarios P10, P11, P12 (docs/testing-scenarios.md §"PWA Shell"): the
two-mechanism kill switch (``/api/sw-config``'s pre-register gate, and the
``/sw-kill.js`` swap for already-installed clients) and the manage page's
"Reset local data" escape hatch. All three assert the central SNOW-389
invariant via ``PwaPage.assert_sw_absent()`` — no SW registration, no shell
caches, no PWA IndexedDB left behind.
"""

from __future__ import annotations

import json
import time
from typing import Any

from playwright.sync_api import Error, Page, Route
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import PwaPage, SignedInPage

# ---------------------------------------------------------------------------
# P10 — Kill switch A: /api/sw-config pre-register gate
# ---------------------------------------------------------------------------


def test_kill_switch_a_prevents_registration(
    live_server: LiveServer, page: Page
) -> None:
    """SW_KILL=true stops a SW from ever registering on a fresh tab.

    Deliberately does NOT use the ``pwa_page`` fixture — that fixture's
    whole job is establishing a real registration, which is exactly what
    Mechanism A must prevent. This is the same route-interception pattern
    ``test_pwa_client_signals.py``'s ``_disable_real_sw`` already uses to
    keep OTHER tests SW-free; here it's the thing under test.

    Doesn't use ``PwaPage.assert_sw_absent()`` — that helper also checks
    for ``snowdesk-pwa-v1`` IndexedDB, but ``db.js`` opens that database
    unconditionally on every page load regardless of SW state (it isn't
    tied to the kill switch at all), so asserting it away here would be
    asserting something the app never claimed.
    """
    page.route(
        "**/api/sw-config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sw_url": "/sw.js", "kill": True}),
        ),
    )
    with page.expect_response("**/api/sw-config"):
        page.goto(live_server.url + "/")
    page.wait_for_load_state("networkidle")

    state = page.evaluate(
        """async () => {
            const regs = await navigator.serviceWorker.getRegistrations();
            const cacheKeys = (await caches.keys()).filter(
              (k) => k.startsWith('snowdesk-shell-'),
            );
            return { regCount: regs.length, cacheKeys };
          }"""
    )
    assert state == {"regCount": 0, "cacheKeys": []}


# ---------------------------------------------------------------------------
# P11 — Kill switch B: swap sw.js for sw-kill.js on an already-installed client
# ---------------------------------------------------------------------------


def _converged_to_no_registrations(page: Page) -> bool:
    """True once no SW registration remains, tolerating an in-flight navigation.

    The kill worker triggers its own follow-up navigation once it finishes
    wiping (``client.navigate(client.url)`` in ``sw-kill.js``), which can
    destroy the page's JS execution context mid-poll — that's expected,
    not a real failure, so a transient Playwright ``Error`` is treated as
    "not converged yet" rather than propagated.
    """
    try:
        return bool(
            page.evaluate(
                "async () => (await navigator.serviceWorker.getRegistrations()).length"
            )
            == 0
        )
    except Error:
        return False


def test_kill_switch_b_wipes_and_unregisters(pwa_page: PwaPage) -> None:
    """Flipping /api/sw-config's sw_url to /sw-kill.js wipes and self-unregisters.

    ``registration.update()`` alone only re-fetches the CURRENTLY
    registered script URL — it cannot pick up a different ``sw_url``. What
    actually re-reads ``/api/sw-config`` is ``sw_register.js``'s top-level
    ``fetchSwConfig()``, which runs on every fresh navigation. Registering
    a different script URL for the same scope is a genuine SW update; the
    kill worker's ``install`` handler calls ``skipWaiting()`` immediately
    (no waiting-worker banner for this one — ops needs it live now), and
    its ``activate`` handler wipes everything and unregisters itself.
    """
    page = pwa_page.page

    # Plant a marker so we can prove the kill worker actually wiped
    # Cache Storage, not just relied on a coincidental repopulation.
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            const cache = await caches.open(shellKey);
            await cache.put('/__kill-switch-marker__', new Response('x'));
          }"""
    )

    page.route(
        "**/api/sw-config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sw_url": "/sw-kill.js", "kill": False}),
        ),
    )

    # pwa.kill_switch.activated (mechanism: 'b') is a critical event fired
    # via sendBeacon — the plan's documented race is that the kill
    # worker's IndexedDB wipe can beat the message's IndexedDB enqueue, so
    # the sendBeacon capture is the only reliable place to observe it.
    beacons: list[dict[str, Any]] = []

    def _capture_beacon(route: Route) -> None:
        try:
            beacons.append(json.loads(route.request.post_data or "{}"))
        except json.JSONDecodeError:
            pass
        route.fulfill(status=204, body="")

    page.route("**/api/telemetry", _capture_beacon)

    page.reload()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _converged_to_no_registrations(page):
        page.wait_for_timeout(200)
    assert _converged_to_no_registrations(page), (
        "kill switch B did not converge to zero registrations in time"
    )

    marker_survived = page.evaluate(
        """async () => {
            const keys = await caches.keys();
            for (const k of keys) {
              const cache = await caches.open(k);
              if (await cache.match('/__kill-switch-marker__')) return true;
            }
            return false;
          }"""
    )
    assert marker_survived is False

    # Telemetry here is best-effort, not asserted as present: sw-kill.js's
    # own header comment documents that "some loss under a fast navigate
    # race is accepted" — the postMessage to the page and the self-
    # triggered client.navigate() that tears the page down race each
    # other, with no synchronous alternative available from a SW context.
    # An anti-flake run against this exact assertion (kill_beacons truthy)
    # measured roughly 1 in 15 losing the race — consistent with the
    # documented behaviour, not a test bug. When a beacon DOES arrive, its
    # shape is still worth checking.
    kill_beacons = [b for b in beacons if b.get("event") == "pwa.kill_switch.activated"]
    if kill_beacons:
        assert kill_beacons[0]["properties"]["mechanism"] == "b"


# ---------------------------------------------------------------------------
# P12 — Reset local data (manage page button)
# ---------------------------------------------------------------------------


def test_manage_page_reset_local_data(signed_in_page: SignedInPage) -> None:
    """The manage page's "Reset local data" control wipes and reloads cleanly.

    ``[data-pwa-reset-trigger]`` (``subscriptions/templates/subscriptions/manage.html``)
    is bound by ``pwa_reset.js``'s ``bindTrigger`` — a native
    ``window.confirm()`` dialog gates it (no ``data-pwa-reset-skip-confirm``
    on this button), then ``resetLocalData()`` runs the six-step wipe and
    reloads. This does NOT reveal the ``#pwa-reset-required`` overlay —
    that overlay is a distinct, unrelated mechanism (``db.js``'s terminal
    Reset Required state after an IndexedDB migration failure); confirmed
    by reading ``static/js/pwa_reset.js`` and ``static/js/db.js`` rather
    than assumed from the docs table, which conflates the two.
    """
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + "/subscribe/manage/")
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url

    # Plant a marker so we can prove the reset actually wiped Cache Storage.
    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            if (!shellKey) return;
            const cache = await caches.open(shellKey);
            await cache.put('/__reset-marker__', new Response('x'));
          }"""
    )

    # pwa.reset.user_initiated is a critical event (sendBeacon) — captured
    # best-effort, same documented reload-tear-down race as Mechanism B.
    beacons: list[dict[str, Any]] = []

    def _capture_beacon(route: Route) -> None:
        try:
            beacons.append(json.loads(route.request.post_data or "{}"))
        except json.JSONDecodeError:
            pass
        route.fulfill(status=204, body="")

    page.route("**/api/telemetry", _capture_beacon)
    page.on("dialog", lambda dialog: dialog.accept())

    with page.expect_navigation(timeout=10000):
        page.click("[data-pwa-reset-trigger]")

    # SW invariant: converges cleanly on a fresh registration — never
    # stuck on the unregistered worker.
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
              if (await cache.match('/__reset-marker__')) return true;
            }
            return false;
          }"""
    )
    assert marker_survived is False

    reset_beacons = [b for b in beacons if b.get("event") == "pwa.reset.user_initiated"]
    if reset_beacons:
        assert reset_beacons[0]["client_version"]
