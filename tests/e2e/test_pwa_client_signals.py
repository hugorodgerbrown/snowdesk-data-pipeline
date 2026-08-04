"""
tests/e2e/test_pwa_client_signals.py — Playwright tests for the SNOW-384
client-side telemetry wiring.

Covers the emit sites that don't require an actual installed service
worker (a real SW install/activate cycle isn't reliably drivable in this
harness — see ``docs/telemetry-pipeline.md`` for the manual-verification
notes on those). Specifically:

1. The generic SW → page ``pwa-telemetry`` message bridge in
   ``sw_register.js`` (used by ``sw.js`` for the ``pwa.sw.*`` / push
   events and by ``sw-kill.js`` for Mechanism B's
   ``pwa.kill_switch.activated``) — simulated by dispatching a
   ``MessageEvent`` directly on ``navigator.serviceWorker`` rather than
   requiring a real registered worker to post one.
2. ``pwa.kill_switch.activated`` (Mechanism A — the pre-register kill
   fetch in ``sw_register.js``) via an intercepted ``/api/sw-config``.
3. The install funnel in ``pwa_install.js``:
   ``pwa.install.prompted`` / ``.accepted`` / ``.dismissed`` / ``.completed``.
4. ``pwa.forced_update.triggered`` from ``pwa_version_check.js``'s single
   remaining caller — the server answering ``update_required: true``.
   SNOW-609 removed the 24h escalation this case used to drive, and made
   the blocking modal wait for the user's click, so the event can now be
   read off IndexedDB without racing an automatic reload.
5. (Moved, SNOW-496) ``window.pwaMutationQueue`` behaviour. SNOW-376
   replaced the no-op stub with a real queue whose ``enqueue`` performs an
   actual network drain — coverage moved entirely to
   ``tests/js/test_mutation_queue.js`` (Vitest, mocked ``fetch``) and the
   real-consumer journeys in ``tests/e2e/test_offline_favourite_submit.py``
   / ``test_offline_observation_submit.py``. Asserting stub semantics here
   fired an un-routed POST at the live server, which under the file-based
   e2e SQLite DB could lock the table and deadlock a later test.
6. ``X-Client-Version`` header injection (``static/js/pwa_client_version.js``,
   SNOW-388) on same-origin ``fetch`` and HTMX requests, and its absence on
   third-party requests — including the ``sw_register.js::fetchSwConfig()``
   pre-register fetch, which fires synchronously at IIFE-load and is the
   regression case for the original load-order bug (the wrapper must load
   BEFORE ``sw_register.js``, not after).

SNOW-496: two cases that tested telemetry.js's OWN sample-rate/enqueue
logic (rather than another module's wiring into it) moved to
``tests/js/test_telemetry.js`` as direct ``emit()`` calls, dropping the
page-rendering/live-page-open-quota parts (Django template rendering and
``navigator.storage`` aren't exercised by a jsdom unit test — see that
file's docstring):
``pwa.freshness.{fresh,stale,unsafe}`` (was: rendering
``templates/includes/_freshness_indicator.html`` inline script into a live
page) and ``pwa.storage.evicted_probable`` (was: stubbing
``navigator.storage.estimate()`` on a live page).

Not covered here (documented as manual-only in
``docs/telemetry-pipeline.md``): ``pwa.sw.installed/.activated/
.activation_failed/.update_available/.update_applied/.fetch_undefined``
and ``pwa.push.received/.shown/.opened`` — all require a real installed
+ activated service worker, which this test harness cannot reliably
drive (no HTTPS, and Playwright's SW lifecycle timing is not exposed to
the Python test process in a way this repo's other e2e tests rely on).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    Kept per-file rather than in ``conftest.py`` so the escape hatch is
    scoped to the tests that actually need a bundled browser.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


def _load(page: Page, live_server_url: str) -> None:
    """Load / and wait for window.pwaDb + window.pwaTelemetry to appear."""
    page.goto(live_server_url)
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.wait_for_function("() => typeof window.pwaTelemetry === 'object'")


def _delete_db(page: Page) -> None:
    """Delete the PWA DB so each test starts from a clean slate."""
    page.evaluate(
        """(name) => new Promise((resolve) => {
            try {
              const req = indexedDB.deleteDatabase(name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              // No onblocked handler: it fires while the delete is still
              // pending (blocked by db.js's live, self-yielding connection),
              // not done — resolving there returns before the DB is gone. See
              // tests/js/test_db.js's deleteDb() for the full rationale.
            } catch (_e) { resolve(); }
          })""",
        "snowdesk-pwa-v1",
    )


def _disable_real_sw(page: Page) -> None:
    """Force the Mechanism-A pre-register kill path so no real SW installs.

    localhost is a secure context, so ``sw_register.js`` genuinely
    registers ``/sw.js`` in this harness and the real worker's own
    lifecycle events (``pwa.sw.installed`` / ``.activated``) land in
    ``queue:events`` at unpredictable real-world timing. Tests that only
    care about the message-bridge mechanism itself (not the real SW)
    call this — before ``_load`` / ``page.reload`` — to remove that
    pollution source. Must be set up before the navigation that first
    loads ``sw_register.js``.
    """
    page.route(
        "**/api/sw-config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sw_url": "/sw.js", "kill": True}),
        ),
    )


# ---------------------------------------------------------------------------
# 1. SW → page telemetry message bridge (sw_register.js)
# ---------------------------------------------------------------------------


def test_sw_message_bridge_forwards_to_pwatelemetry(
    live_server: LiveServer, page: Page
) -> None:
    """A ``pwa-telemetry`` message on navigator.serviceWorker reaches emit().

    ``_disable_real_sw`` prevents the real ``sw.js`` from installing —
    localhost is a secure context, so it would otherwise genuinely
    register and post its own ``pwa.sw.installed`` message at
    unpredictable timing, polluting this assertion.
    """
    _disable_real_sw(page)
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            navigator.serviceWorker.dispatchEvent(new MessageEvent('message', {
              data: {
                type: 'pwa-telemetry',
                event: 'pwa.sw.installed',
                properties: { cache_version: 'test-marker' },
              },
            }));
            await new Promise((r) => setTimeout(r, 100));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.properties?.cache_version === 'test-marker');
          }"""
    )
    assert row is not None
    assert row["event"] == "pwa.sw.installed"


def test_sw_message_bridge_ignores_non_telemetry_messages(
    live_server: LiveServer, page: Page
) -> None:
    """A message without the pwa-telemetry envelope shape is a no-op.

    ``_disable_real_sw`` removes the real-SW pollution source (see the
    note on ``test_sw_message_bridge_forwards_to_pwatelemetry``) so the
    before/after depth comparison is deterministic.
    """
    _disable_real_sw(page)
    _load(page, live_server.url)
    _delete_db(page)

    before, after = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            const before = await window.pwaDb.count('queue:events');
            navigator.serviceWorker.dispatchEvent(
              new MessageEvent('message', { data: { type: 'version', version: 'x' } }),
            );
            await new Promise((r) => setTimeout(r, 50));
            const after = await window.pwaDb.count('queue:events');
            return [before, after];
          }"""
    )
    assert after == before


# ---------------------------------------------------------------------------
# 2. pwa.kill_switch.activated — Mechanism A (sw_register.js pre-register fetch)
# ---------------------------------------------------------------------------


def test_kill_switch_mechanism_a_emits(live_server: LiveServer, page: Page) -> None:
    """/api/sw-config returning kill=true fires pwa.kill_switch.activated."""
    _load(page, live_server.url)
    _delete_db(page)

    page.route(
        "**/api/sw-config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sw_url": "/sw.js", "kill": True}),
        ),
    )
    page.reload()
    page.wait_for_function("() => typeof window.pwaTelemetry === 'object'")

    row = page.evaluate(
        """async () => {
            await new Promise((r) => setTimeout(r, 150));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.kill_switch.activated');
          }"""
    )
    assert row is not None
    assert row["properties"]["mechanism"] == "a"


# ---------------------------------------------------------------------------
# 3. Install funnel (pwa_install.js)
# ---------------------------------------------------------------------------


def test_install_prompted_and_accepted_emit(
    live_server: LiveServer, page: Page
) -> None:
    """beforeinstallprompt + accepted userChoice emits prompted then accepted."""
    _load(page, live_server.url)
    _delete_db(page)

    events = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            localStorage.setItem('pwa.install.time_engaged_ms', '31000');
            const evt = new Event('beforeinstallprompt', { cancelable: true });
            evt.prompt = () => {};
            evt.userChoice = Promise.resolve({ outcome: 'accepted' });
            window.dispatchEvent(evt);
            document.getElementById('pwa-install-accept').click();
            await new Promise((r) => setTimeout(r, 150));
            return (await window.pwaDb.getAll('queue:events')).map((r) => r.event);
          }"""
    )
    assert "pwa.install.prompted" in events
    assert "pwa.install.accepted" in events


def test_install_dismissed_emits(live_server: LiveServer, page: Page) -> None:
    """Clicking the banner's dismiss control emits pwa.install.dismissed."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            document.getElementById('pwa-install-banner').classList.remove('hidden');
            document
              .querySelector('#pwa-install-banner [data-action="dismiss"]')
              .click();
            await new Promise((r) => setTimeout(r, 100));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.install.dismissed');
          }"""
    )
    assert row is not None
    assert row["properties"]["platform"] in {"ios", "android", "desktop"}


def test_install_completed_emits_on_appinstalled(
    live_server: LiveServer, page: Page
) -> None:
    """The appinstalled event emits pwa.install.completed."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            window.dispatchEvent(new Event('appinstalled'));
            await new Promise((r) => setTimeout(r, 100));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.install.completed');
          }"""
    )
    assert row is not None


# ---------------------------------------------------------------------------
# 4. pwa.forced_update.triggered — blocked-build path (pwa_version_check.js)
# ---------------------------------------------------------------------------


def test_forced_update_blocked_build_emits(live_server: LiveServer, page: Page) -> None:
    """A server ``update_required`` verdict opens the modal and emits the event.

    SNOW-609: the only remaining trigger, and the only remaining value of
    the ``trigger`` property.

    The route drifts the ``X-App-Version`` HEADER as well as the body.
    Only a header drift schedules the authoritative round trip — the body
    then decides the outcome — so a body-only route reveals nothing at
    all. The escalation test this replaced did not need the header,
    because ``maybeEscalateOnColdLaunch`` fetched ``/api/version``
    directly at import time; that entry point is gone with it.
    """
    _load(page, live_server.url)
    _delete_db(page)

    def _block_this_build(route: Route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["current"] = "test-newer-build"
        payload["update_required"] = True
        headers = {**response.headers, "x-app-version": "test-newer-build"}
        route.fulfill(response=response, headers=headers, json=payload)

    page.route("**/api/version", _block_this_build)

    page.evaluate("async () => { await fetch('/api/version'); }")
    # The modal reveal is async (one /api/version round trip) — wait for
    # it rather than sampling a fixed delay. Nothing reloads on its own,
    # so reading the emitted row afterwards is race-free.
    page.wait_for_selector("#pwa-update-modal:not(.hidden)", timeout=5000)

    row = page.evaluate(
        """async () => {
            await new Promise((r) => setTimeout(r, 150));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.forced_update.triggered');
          }"""
    )
    assert row is not None
    assert row["properties"]["trigger"] == "blocked_build"


# ---------------------------------------------------------------------------
# 5. window.pwaMutationQueue — moved to tests/js/test_mutation_queue.js
# ---------------------------------------------------------------------------
# The three former stub tests here asserted no-op semantics that SNOW-376
# made obsolete: the real enqueue() now performs a network drain, and
# markFailed() writes a real IndexedDB row and reveals a toast. Exercising
# those against the live server here fired an un-routed POST /account/ (no
# page.route), which under the file-based e2e SQLite DB could lock the
# table and deadlock a later test. The real behaviour — with the mutation
# endpoint mocked — is now covered entirely by tests/js/test_mutation_queue.js
# (SNOW-496; the retired tests/e2e/test_mutation_queue.py was a "simulated
# SW" suite throughout, so nothing here needed a real-SW home).

# SNOW-496: pwa.freshness.{fresh,stale,unsafe} (_freshness_indicator.html)
# and pwa.storage.evicted_probable (static/js/db.js's cold-start heuristic)
# moved to tests/js/test_telemetry.js — see this file's module docstring.


# ---------------------------------------------------------------------------
# 6. X-Client-Version on same-origin fetch / HTMX requests (SNOW-388)
# ---------------------------------------------------------------------------


def _route_capturing_headers(captured: dict[str, str]) -> Callable[[Route], None]:
    """Return a ``page.route`` handler that records headers then fulfils 200.

    Shared by the tests below so each just supplies its own ``captured``
    dict and reads it back after triggering the request.

    ``Access-Control-Allow-Origin: *`` is stamped on the fulfilled response
    so cross-origin fetches (e.g. ``https://tiles.example.com/**``) don't
    hit a browser CORS refusal and throw "TypeError: Failed to fetch"
    before the test can inspect ``captured``. Harmless for same-origin
    routes — the header is ignored when the browser already accepts the
    response (SNOW-397).
    """

    def _handler(route: Route) -> None:
        captured.update(route.request.headers)
        route.fulfill(
            status=200,
            content_type="application/json",
            body="{}",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    return _handler


def test_fetch_stamps_client_version_on_same_origin_request(
    live_server: LiveServer, page: Page
) -> None:
    """A same-origin fetch() call carries X-Client-Version matching the meta tag."""
    _load(page, live_server.url)

    captured: dict[str, str] = {}
    page.route("**/api/version", _route_capturing_headers(captured))

    page.evaluate("async () => { await fetch('/api/version'); }")

    expected = page.eval_on_selector(
        'meta[name="pwa-app-version"]', "(el) => el.content"
    )
    assert expected  # sanity: the meta tag must carry a real build id
    assert captured.get("x-client-version") == expected


def test_fetch_omits_client_version_on_third_party_request(
    live_server: LiveServer, page: Page
) -> None:
    """A cross-origin fetch() call does NOT carry X-Client-Version.

    ``_disable_real_sw`` is essential here, not just tidy: localhost is a
    secure context, so without it the real ``sw.js`` registers and — once
    it claims the page at non-deterministic timing — its ``fetch`` handler
    routes this cross-origin GET through ``_classifyCrossOriginGet``. That
    origin isn't a registered basemap, so the SW re-issues the request with
    its own ``fetch(request)``, which Playwright's ``page.route`` cannot see
    (a service worker's own script fetches bypass page/context interception)
    — it escapes to the real network, ``tiles.example.com`` doesn't resolve,
    and the page's ``fetch()`` rejects, failing the ``page.evaluate`` before
    the header assertion is ever reached. Disabling the SW keeps the request
    native, so the route interceptor fulfils it deterministically.
    """
    _disable_real_sw(page)
    _load(page, live_server.url)

    captured: dict[str, str] = {}
    page.route("https://tiles.example.com/**", _route_capturing_headers(captured))

    page.evaluate(
        "async () => { await fetch('https://tiles.example.com/style.json'); }"
    )

    assert "x-client-version" not in captured


def test_sw_config_fetch_carries_client_version(
    live_server: LiveServer, page: Page
) -> None:
    """sw_register.js's pre-register fetch('/api/sw-config') carries the header.

    Regression guard for the original SNOW-388 bug: ``pwa_client_version.js``
    initially loaded LAST in ``base.html``, so ``sw_register.js``'s
    ``fetchSwConfig()`` — dispatched SYNCHRONOUSLY at its own IIFE-load,
    before any user interaction — went out through an unwrapped
    ``window.fetch`` and never carried the header. The route interceptor
    must be armed BEFORE navigation to observe that load-time request.
    """
    captured: dict[str, str] = {}

    def _handler(route: Route) -> None:
        captured.update(route.request.headers)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sw_url": "/sw.js", "kill": False}),
        )

    page.route("**/api/sw-config", _handler)
    page.goto(live_server.url)
    page.wait_for_load_state("load")

    expected = page.eval_on_selector(
        'meta[name="pwa-app-version"]', "(el) => el.content"
    )
    assert expected  # sanity: the meta tag must carry a real build id
    assert captured.get("x-client-version") == expected


def test_htmx_config_request_stamps_client_version(
    live_server: LiveServer, page: Page, _load_test_data: None
) -> None:
    """A same-origin HTMX request carries X-Client-Version via htmx:configRequest.

    Uses the canonical bulletin page (which loads htmx.min.js) rather than
    ``/`` — the home page does not load HTMX. A synthetic ``hx-get`` element
    is injected and processed manually since the page has no pre-existing
    element that issues a GET to a stable, easily-routed URL.
    """
    page.goto(f"{live_server.url}/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("load")

    captured: dict[str, str] = {}
    page.route("**/api/version", _route_capturing_headers(captured))

    page.evaluate(
        """() => {
            const el = document.createElement('div');
            el.setAttribute('hx-get', '/api/version');
            document.body.appendChild(el);
            window.htmx.process(el);
            el.click();
          }"""
    )
    page.wait_for_timeout(200)

    expected = page.eval_on_selector(
        'meta[name="pwa-app-version"]', "(el) => el.content"
    )
    assert captured.get("x-client-version") == expected


def test_storage_evicted_probable_not_flagged_on_healthy_quota(
    live_server: LiveServer, page: Page
) -> None:
    """A plausible quota with no prior-install marker does not emit."""
    page.add_init_script(
        """
        Object.defineProperty(navigator, 'storage', {
          value: {
            estimate: () => Promise.resolve({ quota: 1024 * 1024 * 1024, usage: 0 }),
          },
          configurable: true,
        });
        Math.random = () => 0;
        """
    )
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaDb.open();
            await new Promise((r) => setTimeout(r, 150));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.storage.evicted_probable');
          }"""
    )
    assert row is None
