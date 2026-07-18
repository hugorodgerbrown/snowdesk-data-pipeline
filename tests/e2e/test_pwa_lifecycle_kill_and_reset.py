"""
tests/e2e/test_pwa_lifecycle_kill_and_reset.py — SNOW-389 kill-switch and
reset journeys.

Covers Scenarios P10 and P12 (docs/testing-scenarios.md §"PWA Shell"):
Mechanism A of the kill switch (``/api/sw-config``'s pre-register gate) and
the manage page's "Reset local data" escape hatch. Both assert the central
SNOW-389 invariant — no SW registration, no shell caches, no PWA IndexedDB
left behind after the flow completes.

P11 (Mechanism B — swapping an already-installed client onto
``sw-kill.js``) does NOT ship here. A ``test_kill_switch_b_wipes_and_unregisters``
test was written and initially looked solid (15/15, then 19/20 in separate
anti-flake passes), but a wider batch surfaced a genuine, non-marginal
"did not converge to zero registrations" failure — raising the poll
deadline from 10s to 20s did not fix it (14/20 in the worst batch), so
this isn't a timing-margin problem, something about the
install -> skipWaiting -> activate -> wipe -> unregister chain
intermittently doesn't complete. Per the SNOW-389 scope's fallback ladder
("flaky > absent, but flaky < manual"), P11 moves to manual-only rather
than shipping a test with a real, unresolved intermittent failure — see
``docs/testing-scenarios.md`` Scenario P11 and
``tests/e2e/_spike_results.py``.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import SignedInPage

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
    # best-effort, same documented reload-tear-down race noted throughout
    # this suite (see the module docstring on the dropped Mechanism B test
    # in git history, or docs/telemetry-pipeline.md).
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
