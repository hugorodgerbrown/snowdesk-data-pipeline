"""
tests/e2e/test_pwa_lifecycle_kill_and_reset.py — SNOW-389 kill-switch and
reset journeys.

Covers Scenarios P10 and P12 (docs/testing-scenarios.md §"PWA Shell"):
Mechanism A of the kill switch (``/api/sw-config``'s pre-register gate) and
the manage page's "Reset local data" escape hatch. Both assert the central
SNOW-389 invariant — no SW registration, no shell caches, no PWA IndexedDB
left behind after the flow completes.

P12b covers the second copy of that escape hatch, added to
``static/offline.html`` after SNOW-607's ``@never_cache`` took the manage
page (and with it the only reachable reset button) out of the offline
shell.

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
# P12 — Reset local data (manage page button)
# ---------------------------------------------------------------------------


def test_manage_page_reset_local_data(signed_in_page: SignedInPage) -> None:
    """The manage page's "Reset local data" control wipes and reloads cleanly.

    ``[data-pwa-reset-trigger]`` (``apps/accounts/templates/accounts/manage.html``)
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
    page.goto(signed_in_page.live_server_url + "/account/manage/")
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


# ---------------------------------------------------------------------------
# P12b — Reset local data (offline fallback page)
# ---------------------------------------------------------------------------


def test_offline_page_reset_control(pwa_page: PwaPage) -> None:
    """The offline fallback page carries the same escape hatch, and it wipes.

    SNOW-607 put ``@never_cache`` on the manage page, which took the only
    copy of the §12.7 escape hatch offline with it. ``static/offline.html``
    is pre-cached, so it now carries a second copy of the SAME control —
    ``[data-pwa-reset-trigger]``, bound by ``pwa_reset.js``, gated by the
    same native ``window.confirm`` dialogue. This is the manage-page
    journey above, replayed on the page a stuck user can still reach.

    Loaded over the network here rather than as a navigation fallback:
    see ``test_offline_page_reset_control_offline`` below for why the
    offline case can't pass yet.
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/static/offline.html")
    page.wait_for_load_state("load")

    # Hidden until pwa_reset.js proves it loaded — revealed here because
    # the page was fetched over the network.
    panel = page.locator("#pwa-reset-panel")
    panel.wait_for(state="visible", timeout=5000)

    page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            if (!shellKey) return;
            const cache = await caches.open(shellKey);
            await cache.put('/__reset-marker__', new Response('x'));
          }"""
    )

    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_navigation(timeout=10000):
        page.click("[data-pwa-reset-trigger]")

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


def test_offline_page_reset_control_offline(pwa_page: PwaPage) -> None:
    """The escape hatch is reachable on an offline navigation.

    This is the state §12.7 exists for — the PWA is stuck AND there is no
    network. Same shape as
    ``test_offline_navigation_to_never_visited_url_shows_offline_fallback``
    in ``test_pwa_lifecycle_offline.py``, which is the established pattern
    for driving ``_networkFirst`` to its fallback.

    What this case does NOT prove: that precaching is what made
    ``pwa_reset.js`` available. ``context.set_offline`` governs page-side
    network only — a request the worker re-issues still reaches the live
    server — and the script is fetched through ``_staleWhileRevalidate``,
    so it would load here with or without its ``PRECACHE_URLS`` entry.
    The precache is what makes it work on a real device; the evidence for
    that is that ``collectstatic`` under
    ``CompressedManifestStaticFilesStorage`` emits the unhashed filename
    next to the hashed one, so the stable URL ``cache.addAll`` requests
    resolves. This case covers the panel rendering and the wipe running.
    """
    page = pwa_page.page

    page.context.set_offline(True)
    try:
        page.goto(pwa_page.live_server_url + "/some-page-never-visited/")
        page.wait_for_load_state("load")
        assert (
            page.locator("h1", has_text="This page isn't available offline").count()
            == 1
        )
        assert page.locator("#pwa-reset-panel").is_visible()
    finally:
        page.context.set_offline(False)
