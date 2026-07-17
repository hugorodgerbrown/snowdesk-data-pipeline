"""
tests/e2e/_spike_push_dispatch.py — SNOW-389 spike Q2: push via new PushEvent.

Throwaway harness answering the plan's Q2: can Playwright dispatch a
synthetic ``push`` event directly on a real, activated service worker (no
actual push subscription / push service round-trip needed — spec §12.5's
"push received while backgrounded" journey is really about the SW's own
``push`` listener, which we can drive directly) and observe the resulting
``pwa.push.received`` / ``pwa.push.shown`` telemetry land in ``queue:events``?

Also folds in Q4 (the ``notifications`` permission grant) — this file's
``browser_context_args`` override is the thing Q4 asks about.

Run 10x back-to-back via::

    for i in $(seq 1 10); do uv run tox -e e2e -- tests/e2e/_spike_push_dispatch.py || break; done

If ``showNotification`` rejects in headless Chromium or the ``push`` event
never invokes ``sw.js``'s listener, the push trio stays manual and
``test_pwa_push_journey.py`` never ships.

Deleted once the outcome is captured in ``tests/e2e/_spike_results.py`` —
see the SNOW-389 plan, step 1.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    See ``tests/e2e/test_pwa_db.py`` for the identical fixture and rationale.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


@pytest.fixture()
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Q4: grant ``notifications`` on top of the repo-wide clipboard grants.

    Without this, ``Notification.permission`` stays ``'default'`` in
    headless Chromium and ``self.registration.showNotification()`` inside
    ``sw.js``'s ``push`` handler would reject.
    """
    return {
        **browser_context_args,
        "permissions": ["clipboard-read", "clipboard-write", "notifications"],
    }


def _delete_db(page: Page) -> None:
    """Delete the PWA DB so the test starts from a clean slate."""
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


def test_synthetic_push_event_emits_received_and_shown(
    live_server: LiveServer, page: Page
) -> None:
    """Dispatch ``new PushEvent`` on the real SW and read back telemetry.

    Four steps, matching the SNOW-389 plan's Q2 spike:

    1. (``browser_context_args`` above) grant ``notifications``.
    2. Register the real SW, wait for activation.
    3. Grab the SW ``Worker`` object via ``context.service_workers`` and
       dispatch a synthetic ``push`` event on it from within the worker's
       own context (``worker.evaluate()``).
    4. Read back ``pwa.push.received`` / ``.shown`` from ``queue:events``
       on the page (the SW → page message bridge in ``sw_register.js``
       forwards them there).
    """
    page.goto(live_server.url)
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    _delete_db(page)
    page.evaluate("async () => { await window.pwaTelemetry.setOptIn(true); }")

    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    worker.evaluate(
        """() => {
            self.dispatchEvent(new PushEvent('push', {
              data: JSON.stringify({ url: '/', title: 'spike', body: 'spike body' }),
            }));
          }"""
    )

    page.wait_for_function(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.some((r) => r.event === 'pwa.push.received')
              && rows.some((r) => r.event === 'pwa.push.shown');
          }""",
        timeout=5000,
    )
