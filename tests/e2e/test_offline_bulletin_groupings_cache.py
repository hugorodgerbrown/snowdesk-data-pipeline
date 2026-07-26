"""
tests/e2e/test_offline_bulletin_groupings_cache.py — SNOW-526 Playwright
coverage: the shell cache only persists a settled bulletin-groupings.geojson
response.

``public/api.py``'s ``bulletin_groupings_geojson`` sets a date-aware
``Cache-Control`` (branching on ``public.api._cached_earliest_mutable_date()``
— a memoised wrapper around ``bulletins.services.settled.
earliest_mutable_date()``) and ``static/js/sw.js``'s ``_staleWhileRevalidate``
only writes the shell cache when the response declares itself ``immutable``
(``shouldPersist()`` in ``static/js/basemap_cache_core.js``, gated by
``IMMUTABLE_ONLY_PATHS``). These tests drive the real endpoint and the real
SW end to end:

* ``test_settled_date_served_from_cache_while_offline`` — a settled date's
  response is written to the shell cache online and replayed from it
  offline (``X-SW-Cache: hit``, stamped by ``_stampCacheHit``).
* ``test_unsettled_date_not_served_while_offline`` — an unsettled (e.g.
  today's) date's response is never written, so the same online-then-offline
  sequence fails offline instead of replaying stale data.

``public.api.earliest_mutable_date`` is monkeypatched directly (rather than
seeding ``Bulletin``/``BulletinGrouping`` rows and letting the real fetcher
registry derive the threshold) so each test controls its settled/unsettled
outcome deterministically — the registry-derivation logic itself is covered
by ``tests/bulletins/services/test_settled.py`` and
``tests/public/test_map_api.py``; this file's job is proving the SW's
persist-gating behaves correctly given whatever the server decides.
``monkeypatch`` reaches the in-process ``live_server`` thread the same way
``conftest.py``'s ``_stub_elevation_lookup`` fixture does.

``django.core.cache.cache`` is cleared at the start of each test: unlike the
``_load_test_data`` fixture's tests, ``pwa_page`` doesn't clear it, and
``public.api._cached_earliest_mutable_date()`` memoises the threshold for
60s (SNOW-526) — without an explicit clear, one test's monkeypatched
threshold could still be cached when the next test's differently-patched
fetch runs.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.core.cache import cache
from playwright.sync_api import Page

from tests.e2e.conftest import PwaPage, _wait_for_sw_control


def _fetch(page: Page, url: str) -> dict[str, Any]:
    """Issue a real page-side ``fetch`` and report its outcome.

    Args:
        page: The Playwright page to run the fetch from.
        url: A same-origin path to request.

    Returns:
        ``{"ok": bool, "status": int, "swCache": str | None}`` on a
        resolved response, or ``{"ok": False, "error": str}`` if the fetch
        itself rejected (the offline/uncached case).

    """
    result: dict[str, Any] = page.evaluate(
        """async (url) => {
            try {
              const resp = await fetch(url);
              return {
                ok: resp.ok,
                status: resp.status,
                swCache: resp.headers.get('X-SW-Cache'),
              };
            } catch (err) {
              return { ok: false, error: String(err) };
            }
          }""",
        url,
    )
    return result


def test_settled_date_served_from_cache_while_offline(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled date's response is written to the shell cache and replayed offline."""
    cache.clear()
    monkeypatch.setattr("public.api.earliest_mutable_date", lambda: date(2099, 1, 1))

    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _wait_for_sw_control(page)

    url = "/api/bulletin-groupings.geojson?d=2025-12-01"
    online = _fetch(page, url)
    assert online["ok"], f"expected the online fetch to succeed: {online}"

    # _staleWhileRevalidate's cache.put() is fire-and-forget
    # (`.catch(() => {})`, not awaited by the response the page's fetch()
    # resolves on) — so the write can still be in flight when the first
    # fetch above resolves, and an immediate set_offline(True) could race
    # it into a 504. A second fetch, still online, proves the write landed
    # the moment it comes back stamped as a cache hit; a short poll absorbs
    # any residual scheduling delay without weakening what's being proved.
    warmed = None
    for _ in range(20):
        warmed = _fetch(page, url)
        if warmed.get("swCache") == "hit":
            break
        page.wait_for_timeout(100)
    assert warmed is not None and warmed["swCache"] == "hit", (
        f"expected a second ONLINE fetch to prove the write landed in the "
        f"shell cache before going offline: {warmed}"
    )

    page.context.set_offline(True)
    try:
        offline = _fetch(page, url)
    finally:
        page.context.set_offline(False)

    assert offline["ok"], (
        f"expected the settled-date response to be served from the shell "
        f"cache while offline: {offline}"
    )
    assert offline["swCache"] == "hit", (
        f"expected the offline response to be stamped as a cache replay: {offline}"
    )


def test_unsettled_date_not_served_while_offline(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsettled (e.g. today's) date's response is never written to the shell cache."""
    cache.clear()
    monkeypatch.setattr("public.api.earliest_mutable_date", lambda: date(2000, 1, 1))

    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _wait_for_sw_control(page)

    url = "/api/bulletin-groupings.geojson?d=2026-04-16"
    online = _fetch(page, url)
    assert online["ok"], f"expected the online fetch to succeed: {online}"

    page.context.set_offline(True)
    try:
        offline = _fetch(page, url)
    finally:
        page.context.set_offline(False)

    assert not offline["ok"], (
        f"expected the unsettled-date response to be ABSENT from the shell "
        f"cache while offline (a real network failure, not a cache replay): "
        f"{offline}"
    )
