"""
tests/e2e/test_pwa_lifecycle_install.py — SNOW-389 real-SW install journey.

Covers Scenario P1 (docs/testing-scenarios.md §"PWA Shell"): first visit
registers ``/sw.js``, caches the shell, and controls the second load.

Uses the ``pwa_page`` fixture (tests/e2e/conftest.py) — unlike every other
e2e file, this one drives a real, undisabled service worker.
"""

from __future__ import annotations

from playwright.sync_api import Response

from tests.e2e.conftest import PwaPage


def test_first_install_registers_and_caches_shell(pwa_page: PwaPage) -> None:
    """First visit installs a real SW, caches the shell, and serves reload from it.

    Three assertions, matching the plan:

    1. User-observable: a ``snowdesk-shell-*`` Cache Storage entry exists
       and contains the offline fallback (pre-cached on ``install``).
    2. SW invariant: reloading the page is served by the service worker
       (``response.from_service_worker``), not a fresh network fetch.
    3. Telemetry: ``pwa.sw.installed`` and ``pwa.sw.activated`` both land
       in ``queue:events`` (deterministic — see the ``Math.random`` stub
       docstring on ``pwa_page``).
    """
    page = pwa_page.page

    # 1. Shell cache populated.
    cache_state = page.evaluate(
        """async () => {
            const keys = await caches.keys();
            const shellKey = keys.find((k) => k.startsWith('snowdesk-shell-'));
            if (!shellKey) return { shellKey: null, entries: [] };
            const cache = await caches.open(shellKey);
            const requests = await cache.keys();
            return { shellKey, entries: requests.map((r) => new URL(r.url).pathname) };
          }"""
    )
    assert cache_state["shellKey"] is not None, "expected a snowdesk-shell-* cache"
    assert "/static/offline.html" in cache_state["entries"]

    # 2. Reload is served from the SW, not the network.
    responses: list[Response] = []
    page.on("response", lambda response: responses.append(response))
    page.reload()
    page.wait_for_load_state("load")
    root_responses = [
        r for r in responses if r.url.rstrip("/") == pwa_page.live_server_url
    ]
    assert root_responses, "expected at least one response for /"
    assert any(r.from_service_worker for r in root_responses), (
        "expected the reload to be served by the service worker"
    )

    # 3. Telemetry.
    # SNOW-427: the ``pwa_page`` fixture guarantees both rows were in
    # ``queue:events`` before its SW-controlled reload, and blocks the
    # buffer's ``/api/telemetry`` drain so they cannot have been deleted
    # since — these waits resolve on their first poll. The extended
    # timeouts an earlier flake fix added here are gone: a row missing
    # now means "never emitted", which no wait length recovers.
    pwa_page.wait_for_event("pwa.sw.installed")
    pwa_page.wait_for_event("pwa.sw.activated")
