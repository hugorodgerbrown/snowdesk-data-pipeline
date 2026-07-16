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
4. ``pwa.forced_update.triggered`` via the 24h escalation path in
   ``pwa_version_check.js`` (the min-version-mismatch path also triggers
   a real reload via ``resetAndReload()`` and is not covered here to
   avoid a flaky race against that reload).
5. ``pwa.freshness.{fresh,stale,unsafe}`` via the
   ``templates/includes/_freshness_indicator.html`` inline script,
   rendered server-side with Django's template engine and injected into
   a live page (``innerHTML``-inserted ``<script>`` elements are inert,
   so scripts are individually recreated to force execution).
6. ``window.pwaMutationQueue`` stub methods (``static/js/mutation_queue.js``).
7. ``pwa.storage.evicted_probable`` heuristic in ``static/js/db.js``,
   forcing the sample-rate gate open by stubbing ``Math.random``.

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
from datetime import UTC, datetime
from typing import Any

import pytest
from django.template.loader import render_to_string
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    See ``tests/e2e/test_pwa_db.py`` / ``test_pwa_telemetry.py`` for the
    same fixture — kept per-file rather than in ``conftest.py`` so the
    escape hatch is scoped to the tests that actually need a bundled
    browser.
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
              req.onblocked = () => resolve();
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
# 4. pwa.forced_update.triggered — 24h escalation path (pwa_version_check.js)
# ---------------------------------------------------------------------------


def test_forced_update_escalation_emits(live_server: LiveServer, page: Page) -> None:
    """A soft-banner shown >24h ago escalates to the blocking modal on cold launch."""
    _load(page, live_server.url)
    _delete_db(page)

    page.evaluate(
        """() => {
            const shownAt = Date.now() - 25 * 60 * 60 * 1000;
            localStorage.setItem('pwa.update.first_shown_at', String(shownAt));
          }"""
    )
    page.reload()
    page.wait_for_function("() => typeof window.pwaTelemetry === 'object'")

    row = page.evaluate(
        """async () => {
            await new Promise((r) => setTimeout(r, 150));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.forced_update.triggered');
          }"""
    )
    assert row is not None
    assert row["properties"]["trigger"] == "escalation"
    # The modal itself should also be visible.
    assert "hidden" not in (
        page.eval_on_selector("#pwa-update-modal", "(el) => el.className") or ""
    )


# ---------------------------------------------------------------------------
# 5. pwa.freshness.{fresh,stale,unsafe} — _freshness_indicator.html
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["fresh", "stale", "unsafe"])
def test_freshness_indicator_emits(
    live_server: LiveServer, page: Page, state: str
) -> None:
    """The partial's inline script emits pwa.freshness.<state> on render.

    ``pwa.freshness.fresh`` and ``.stale`` are sampled at 10%
    (``telemetry.js`` ``SAMPLE_RATES``) — ``Math.random`` is stubbed via
    ``add_init_script`` (applied before any page script runs) to force
    the gate open deterministically. Overriding it later, inside a
    ``page.evaluate`` call, proved unreliable in practice — the
    reassignment doesn't consistently apply to code paths already
    scheduled by the time that call runs. ``.unsafe`` is unsampled
    (always 100%) but the stub is harmless for it too.
    """
    page.add_init_script("Math.random = () => 0;")
    _load(page, live_server.url)
    _delete_db(page)

    html = render_to_string(
        "includes/_freshness_indicator.html",
        {"state": state, "generated_at": datetime(2026, 7, 16, 10, 0, tzinfo=UTC)},
    )

    row = page.evaluate(
        """async (html) => {
            await window.pwaTelemetry.setOptIn(true);
            const container = document.createElement('div');
            document.body.appendChild(container);
            container.innerHTML = html;
            // innerHTML-parsed <script> elements are inert; recreate each
            // one so the browser actually executes it.
            container.querySelectorAll('script').forEach((old) => {
              const fresh = document.createElement('script');
              fresh.textContent = old.textContent;
              old.replaceWith(fresh);
            });
            await new Promise((r) => setTimeout(r, 100));
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event.startsWith('pwa.freshness.'));
          }""",
        html,
    )
    assert row is not None
    assert row["event"] == f"pwa.freshness.{state}"


# ---------------------------------------------------------------------------
# 6. window.pwaMutationQueue stub (static/js/mutation_queue.js)
# ---------------------------------------------------------------------------


def test_mutation_queue_stub_emits_enqueued(
    live_server: LiveServer, page: Page
) -> None:
    """enqueue() is a no-op but emits pwa.mutation.enqueued with the operation."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaMutationQueue.enqueue({ method: 'POST', url: '/subscribe/' });
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.mutation.enqueued');
          }"""
    )
    assert row is not None
    assert row["properties"] == {"method": "POST", "url": "/subscribe/"}


def test_mutation_queue_stub_emits_drained(live_server: LiveServer, page: Page) -> None:
    """drain() is a no-op but emits pwa.mutation.drained with count 0."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaMutationQueue.drain();
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.mutation.drained');
          }"""
    )
    assert row is not None
    assert row["properties"] == {"count": 0}


def test_mutation_queue_stub_emits_failed_permanent(
    live_server: LiveServer, page: Page
) -> None:
    """markFailed() is a critical event — fires even without opt-in."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(false);
            await window.pwaMutationQueue.markFailed(
              { method: 'POST', url: '/subscribe/' }, 'max_retries_exceeded',
            );
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.mutation.failed_permanent');
          }"""
    )
    assert row is not None
    assert row["properties"]["reason"] == "max_retries_exceeded"
    assert row["properties"]["url"] == "/subscribe/"


# ---------------------------------------------------------------------------
# 7. pwa.storage.evicted_probable — static/js/db.js cold-start heuristic
# ---------------------------------------------------------------------------


def test_storage_evicted_probable_on_low_quota(
    live_server: LiveServer, page: Page
) -> None:
    """An implausibly low navigator.storage.estimate() quota is flagged.

    ``Math.random`` is stubbed via ``add_init_script`` (before any page
    script runs) to force the 10% sample-rate gate open deterministically
    — see the note on ``test_freshness_indicator_emits`` for why a later
    runtime reassignment inside ``page.evaluate`` proved unreliable.
    """
    page.add_init_script(
        """
        Object.defineProperty(navigator, 'storage', {
          value: { estimate: () => Promise.resolve({ quota: 1024, usage: 0 }) },
          configurable: true,
        });
        Math.random = () => 0;
        """
    )
    _load(page, live_server.url)
    _delete_db(page)

    page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaDb.open();
          }"""
    )
    # Poll rather than a fixed sleep — _checkStorageEstimate() is
    # fire-and-forget inside db.js's onsuccess handler, chained behind
    # setOptIn()'s own IndexedDB round-trip above, so its completion time
    # isn't bounded tightly enough for a short fixed delay to be reliable.
    page.wait_for_function(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.some((r) => r.event === 'pwa.storage.evicted_probable');
          }""",
        timeout=5000,
    )
    row = page.evaluate(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.storage.evicted_probable');
          }"""
    )
    assert row is not None
    assert row["properties"]["quota"] == 1024
    assert row["properties"]["usage"] == 0


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
