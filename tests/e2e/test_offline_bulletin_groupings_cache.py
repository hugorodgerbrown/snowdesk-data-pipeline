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

* ``test_settled_date_second_fetch_is_served_from_shell_cache`` — a settled
  date's response is written to Cache Storage and a later fetch replays it
  (``X-SW-Cache: hit``, stamped by ``_stampCacheHit``).
* ``test_unsettled_date_is_not_persisted_to_the_shell_cache`` — an unsettled
  (e.g. today's) date's response is never written, probed directly against
  Cache Storage, with a settled-date positive control in the same session.

**Neither test uses ``page.context.set_offline``.** It only blocks *page*
-side network — it does not govern fetches the service worker issues from
its own fetch handler. ``_staleWhileRevalidate`` on a cache MISS awaits its
own ``fetch(request)`` from inside the worker, and that reaches the real
live server regardless of the browser context's offline flag; an earlier
revision of this file relied on ``set_offline`` to prove the unsettled case
failed offline and got a false failure in CI (`{'ok': True, 'status': 200,
'swCache': None}`) — the "offline" fetch simply went straight to the
network. What SNOW-526 actually changed is which responses get *written* to
Cache Storage, so these tests probe Cache Storage directly (``caches.match``
— the same read path ``static/js/map_layer_sync_status.js``'s
``_probeExact`` uses) rather than inferring persistence from a failed
network call.

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
        itself rejected.

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


def _cache_contains_within(
    page: Page, url: str, timeout_ms: int = 1000, poll_ms: int = 100
) -> bool:
    """Poll Cache Storage for ``url``, returning ``True`` the first time it's found.

    Runs entirely inside one ``page.evaluate`` call (a single round trip)
    rather than a Python-side poll loop calling back into the page —
    matches the ``wait_for_event``/``wait_for_...`` one-round-trip pattern
    already established in ``tests/e2e/conftest.py``.

    Only ever returns early on a HIT. A caller checking for the ABSENCE of
    an entry gets the full ``timeout_ms`` window polled before concluding
    ``False`` — ``cache.put()`` in ``_staleWhileRevalidate`` is
    fire-and-forget, so a bare immediate check could pass simply by running
    before a write that was always going to land; polling the whole window
    is what makes a negative result meaningful.

    Args:
        page: The Playwright page to probe Cache Storage from.
        url: A same-origin path (relative to ``location.origin``).
        timeout_ms: Total window to poll before giving up.
        poll_ms: Interval between polls.

    Returns:
        ``True`` if any poll within the window found a matching Cache
        Storage entry, ``False`` if the window elapsed with none found.

    """
    result: bool = page.evaluate(
        """async ({ url, timeoutMs, pollMs }) => {
            const request = new Request(new URL(url, location.origin));
            const deadline = Date.now() + timeoutMs;
            while (Date.now() < deadline) {
              const hit = await caches.match(request);
              if (hit) return true;
              await new Promise((resolve) => setTimeout(resolve, pollMs));
            }
            return false;
          }""",
        {"url": url, "timeoutMs": timeout_ms, "pollMs": poll_ms},
    )
    return result


def test_settled_date_second_fetch_is_served_from_shell_cache(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled date's response, once written, is replayed from Cache Storage.

    Deliberately not gated on ``page.context.set_offline`` — see the module
    docstring for why that can't prove anything here. What DOES prove a
    cache replay is ``_stampCacheHit``'s ``X-SW-Cache: hit`` header:
    ``_staleWhileRevalidate`` only sets it when the response resolves from
    a ``cache.match`` hit, which it returns immediately — before its
    background revalidate ``fetch()`` is even awaited. A second fetch
    coming back stamped ``'hit'`` is therefore direct evidence the first
    fetch's write landed and the SW is now serving this response from
    Cache Storage without needing the network — the same code path that
    makes the resource usable offline.
    """
    cache.clear()
    monkeypatch.setattr("public.api.earliest_mutable_date", lambda: date(2099, 1, 1))

    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _wait_for_sw_control(page)

    url = "/api/bulletin-groupings.geojson?d=2025-12-01"
    online = _fetch(page, url)
    assert online["ok"], f"expected the online fetch to succeed: {online}"

    # cache.put() is fire-and-forget (`.catch(() => {})`, not awaited by the
    # response the first fetch() above resolved on), so the write can still
    # be in flight. Poll a second fetch until it comes back stamped as a
    # cache replay.
    replay = None
    for _ in range(20):
        replay = _fetch(page, url)
        if replay.get("swCache") == "hit":
            break
        page.wait_for_timeout(100)
    assert replay is not None and replay["swCache"] == "hit", (
        f"expected a second fetch of the settled date to be served from "
        f"Cache Storage: {replay}"
    )


def test_unsettled_date_is_not_persisted_to_the_shell_cache(
    pwa_page: PwaPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsettled date's response is never written to Cache Storage.

    Probes Cache Storage directly (``caches.match``) rather than inferring
    absence from a failed "offline" fetch — see the module docstring for
    why that approach doesn't work here (Playwright's ``set_offline``
    doesn't block the service worker's own outbound fetches).

    Includes a **positive control** in the same browser session: a settled
    date is fetched first and asserted to land in Cache Storage, proving
    the probe, the URL, and the SW's persist path all work before the
    negative assertion below is trusted. Without the control, a broken
    probe, a typo'd URL, or a SW that had simply stopped caching entirely
    would all make the negative assertion pass for the wrong reason.

    Both halves run against a SINGLE patched threshold, with the two dates
    straddling it, rather than re-patching between them.
    ``public.api._cached_earliest_mutable_date()`` memoises the threshold
    for 60s (SNOW-526), so the first request warms the memo and a later
    re-patch would be ignored — which is exactly how an earlier version of
    this test failed on CI: the negative half was still being classified
    against the positive half's threshold, so the "unsettled" date came
    back ``immutable`` and was cached. One threshold also mirrors
    production, where a single boundary classifies every date.
    """
    cache.clear()
    monkeypatch.setattr("public.api.earliest_mutable_date", lambda: date(2026, 1, 1))

    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _wait_for_sw_control(page)

    # Positive control — a date before the threshold is settled, so it persists.
    settled_url = "/api/bulletin-groupings.geojson?d=2025-12-01"
    settled_online = _fetch(page, settled_url)
    assert settled_online["ok"], (
        f"expected the settled date's online fetch to succeed: {settled_online}"
    )
    assert _cache_contains_within(page, settled_url, timeout_ms=2000), (
        "positive control failed: expected the settled date's response to "
        "land in Cache Storage — if this fails, the probe/URL/SW caching "
        "path itself is broken and the negative assertion below would "
        "prove nothing"
    )

    # Negative — a date on/after the threshold is unsettled, so it never persists.
    unsettled_url = "/api/bulletin-groupings.geojson?d=2026-04-16"
    unsettled_online = _fetch(page, unsettled_url)
    assert unsettled_online["ok"], (
        f"expected the unsettled date's online fetch to succeed: {unsettled_online}"
    )
    assert not _cache_contains_within(page, unsettled_url, timeout_ms=1000), (
        "expected the unsettled date's response to NEVER appear in Cache "
        "Storage within the poll window"
    )
