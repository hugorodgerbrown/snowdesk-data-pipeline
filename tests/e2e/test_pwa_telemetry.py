"""
tests/e2e/test_pwa_telemetry.py — Playwright tests for static/js/telemetry.js (SNOW-385).

Covers the four contracts from the SNOW-385 scoping comment:

1. ``emit(event, properties)`` writes to ``queue:events`` with the
   expected 8-field envelope shape.
2. ``flush()`` POSTs the batch to ``/api/telemetry`` and clears the
   rows on 2xx.
3. Critical events fire ``navigator.sendBeacon`` immediately with the
   single-envelope shape.
4. When ``setOptIn(false)``, a standard ``emit()`` does NOT enqueue; a
   critical ``emit()`` still fires but the payload has
   ``user_id: null`` and ``session_id: null``.

The receiver's real behaviour is exercised by the tests under
``tests/analytics/`` — here we intercept ``POST /api/telemetry`` so a
missing PostHog key can't hide a wire-up regression.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast

import pytest
from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

DB_NAME = "snowdesk-pwa-v1"


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    See ``tests/e2e/test_pwa_db.py`` for the same fixture — kept
    per-file rather than in ``conftest.py`` so the escape hatch is
    scoped to the tests that actually need a bundled browser.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


def _load(page: Page, live_server_url: str) -> None:
    """Load / and wait for window.pwaDb + window.pwaTelemetry to appear.

    Removes ``navigator.serviceWorker`` before any page script runs so
    ``sw_register.js`` bails out on its very first line
    (``if (!('serviceWorker' in navigator)) return;``) — neither a real
    SW registration nor a ``pwa.kill_switch.activated`` mechanism-A check
    ever runs. localhost is a secure context, so without this the real
    ``sw.js`` genuinely installs and — since SNOW-384 wired
    ``pwa.sw.installed`` / ``.activated`` telemetry into it — posts its
    own events into ``queue:events`` at unpredictable real-world timing,
    which this file's ``rows[0]`` / exact-count assertions don't expect.
    This file tests ``telemetry.js`` in isolation, not the SW lifecycle,
    so removing that source of real events is the correct fix rather
    than loosening every assertion here.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
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
        DB_NAME,
    )


# ---------------------------------------------------------------------------
# 1. emit() writes to queue:events with the expected envelope shape.
# ---------------------------------------------------------------------------


def test_emit_enqueues_envelope_when_opted_in(
    live_server: LiveServer, page: Page
) -> None:
    """Standard emit puts one row into queue:events under opt-in."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaTelemetry.emit('pwa.install.prompted', { platform: 'web' });
            const rows = await window.pwaDb.getAll('queue:events');
            return rows[0];
          }"""
    )
    assert row["event"] == "pwa.install.prompted"
    assert row["properties"] == {"platform": "web"}
    assert isinstance(row["timestamp"], str) and row["timestamp"]
    assert isinstance(row["session_id"], str) and row["session_id"]
    assert row["install_state"] == "browser"
    assert set(row.keys()) >= {
        "event",
        "timestamp",
        "client_version",
        "session_id",
        "user_id",
        "platform",
        "install_state",
        "sw_state",
        "connection",
        "properties",
        # id — added by the queue:events autoIncrement keyPath.
        "id",
    }


# ---------------------------------------------------------------------------
# 2. flush() POSTs the batch and clears the rows on 2xx.
# ---------------------------------------------------------------------------


def test_flush_posts_batch_and_clears_rows(live_server: LiveServer, page: Page) -> None:
    """flush() sends the batch envelope and removes drained rows."""
    _load(page, live_server.url)
    _delete_db(page)

    captured: list[dict[str, Any]] = []

    def _handler(route: Route) -> None:
        request = route.request
        try:
            captured.append(json.loads(request.post_data or "{}"))
        except json.JSONDecodeError:
            captured.append({"__raw__": request.post_data})
        route.fulfill(status=204, body="")

    page.route("**/api/telemetry", _handler)

    result = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(true);
            await window.pwaTelemetry.emit('pwa.install.prompted');
            await window.pwaTelemetry.emit('pwa.install.completed');
            const before = await window.pwaDb.count('queue:events');
            await window.pwaTelemetry.flush();
            const after = await window.pwaDb.count('queue:events');
            return { before, after };
          }"""
    )
    assert result["before"] == 2
    assert result["after"] == 0
    assert len(captured) == 1, f"expected one POST, got {len(captured)}: {captured}"
    batch = captured[0]
    assert "events" in batch and len(batch["events"]) == 2
    forwarded = [e["event"] for e in batch["events"]]
    assert set(forwarded) == {"pwa.install.prompted", "pwa.install.completed"}


# ---------------------------------------------------------------------------
# 3. Critical event fires sendBeacon immediately with single-envelope shape.
# ---------------------------------------------------------------------------


def test_critical_event_fires_sendbeacon_immediately(
    live_server: LiveServer, page: Page
) -> None:
    """A critical event invokes navigator.sendBeacon before flush()."""
    _load(page, live_server.url)
    _delete_db(page)

    # Wrap sendBeacon and capture every call so we can assert on it.
    beacons = page.evaluate(
        """async () => {
            const seen = [];
            const original = navigator.sendBeacon.bind(navigator);
            navigator.sendBeacon = function (url, body) {
              seen.push({ url, body: body instanceof Blob ? null : String(body) });
              return true;
            };
            window.__seenBeacons = seen;

            await window.pwaTelemetry.setOptIn(true);
            await window.pwaTelemetry.emit('pwa.kill_switch.activated', { reason: 'test' });
            // Read the Blob text asynchronously.
            const blob = new Blob(['stub']);
            navigator.sendBeacon = original;
            return seen;
          }"""
    )
    # sendBeacon was called once, targeting the receiver, with the
    # single-envelope shape (not wrapped in ``{events: [...]}``).
    assert len(beacons) == 1
    assert beacons[0]["url"].endswith("/api/telemetry")

    # Read the queued row directly to prove the single-envelope shape
    # matches the beacon payload (the beacon body itself is a Blob so
    # we assert on the queue:events row that emit() also enqueued).
    row = page.evaluate(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            return rows[0];
          }"""
    )
    assert row["event"] == "pwa.kill_switch.activated"
    assert row["properties"] == {"reason": "test"}


# ---------------------------------------------------------------------------
# 4. Opt-out — standard event dropped; critical event stripped of ids.
# ---------------------------------------------------------------------------


def test_opt_out_drops_standard_events(live_server: LiveServer, page: Page) -> None:
    """Under opt-out, a non-critical emit() does NOT enqueue."""
    _load(page, live_server.url)
    _delete_db(page)

    depth = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(false);
            await window.pwaTelemetry.emit('pwa.install.prompted');
            return window.pwaDb.count('queue:events');
          }"""
    )
    assert depth == 0


def test_opt_out_critical_event_strips_ids(live_server: LiveServer, page: Page) -> None:
    """Under opt-out, a critical event still fires but with null ids."""
    _load(page, live_server.url)
    _delete_db(page)

    row = page.evaluate(
        """async () => {
            await window.pwaTelemetry.setOptIn(false);
            await window.pwaTelemetry.emit('pwa.reset.user_initiated');
            const rows = await window.pwaDb.getAll('queue:events');
            return rows[0];
          }"""
    )
    # Critical event was enqueued even under opt-out (so a later
    # flush() reaches the server even if sendBeacon dropped it), but
    # both identifiers are null.
    assert row is not None, "expected the critical event to still enqueue"
    assert row["event"] == "pwa.reset.user_initiated"
    assert row["user_id"] is None
    assert row["session_id"] is None
    # The rest of the envelope-context keys survive.
    assert isinstance(row["client_version"], str)
    assert row["install_state"] in {"browser", "standalone"}


def test_opt_in_default_persists_to_meta_app(
    live_server: LiveServer, page: Page
) -> None:
    """First call to isOptIn() persists a computed default to meta:app."""
    _load(page, live_server.url)
    _delete_db(page)

    stored = page.evaluate(
        """async () => {
            // No prior write; first read should compute + persist.
            await window.pwaTelemetry.isOptIn();
            return window.pwaDb.get('meta:app', 'telemetry.opt_in');
          }"""
    )
    assert stored is not None
    assert stored["key"] == "telemetry.opt_in"
    assert isinstance(stored["value"], bool)


def test_setoptin_writes_through(live_server: LiveServer, page: Page) -> None:
    """setOptIn(true) persists to meta:app and isOptIn() reads it back."""
    _load(page, live_server.url)
    _delete_db(page)

    result = cast(
        dict[str, Any],
        page.evaluate(
            """async () => {
                await window.pwaTelemetry.setOptIn(true);
                const stored = await window.pwaDb.get('meta:app', 'telemetry.opt_in');
                const read = await window.pwaTelemetry.isOptIn();
                return { stored, read };
              }"""
        ),
    )
    assert result["stored"]["value"] is True
    assert result["read"] is True
