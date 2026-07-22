"""
tests/e2e/test_warm_cache_correlation.py — Playwright regression test for
SNOW-493 finding 9: correlate ``warm-cache-done`` replies by request id so
a stale reply can't resolve the wrong call.

``sw_register.js``'s ``warmCache()`` used to keep a single
``_warmCacheResolve`` function with no way to tell which in-flight call a
``warm-cache-done`` message was actually answering. If a reply for an
earlier call arrived after that call had already settled (or timed out),
it would still resolve whatever the CURRENT ``_warmCacheResolve`` was
pointing at — a subsequent, unrelated call — handing that caller the
wrong ``{ok, failed}`` numbers.

The fix mints a ``requestId`` per ``warmCache()`` call, posts it alongside
the URL list, and ``sw.js`` echoes it back in ``warm-cache-done``;
``sw_register.js``'s message handler only resolves when the reply's
``requestId`` matches the CURRENT in-flight slot's.

This test drives the real page-side bridge (``window.pwaWarmCache``) and
a real, activated service worker for two genuine calls, capturing the
``requestId`` each ``postMessage`` carries (by wrapping
``navigator.serviceWorker.controller.postMessage`` from the page side —
no service worker internals need touching for this one, unlike the
other SNOW-493 findings that needed ``worker.evaluate()``). It then fires
a synthetic, stale ``warm-cache-done`` for the FIRST call's requestId
while the SECOND call is still in flight, and asserts the second call
still resolves with its own real numbers rather than the injected stale
ones.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.conftest import PwaPage

_URLS = ["/api/version", "/api/resorts.geojson"]

_STALE_SENTINEL_OK = 999
_STALE_SENTINEL_FAILED = 999


def test_stale_reply_does_not_resolve_a_later_call(pwa_page: PwaPage) -> None:
    """A stale ``warm-cache-done`` for an earlier call must not resolve a later one.

    Calls ``window.pwaWarmCache`` twice — the first awaited to completion
    (capturing its requestId, now stale), the second left in flight —
    then dispatches a synthetic ``warm-cache-done`` message carrying the
    FIRST call's requestId and obviously-wrong sentinel ``{ok, failed}``
    values. The second call must resolve with its own genuine result, not
    the sentinel.
    """
    page = pwa_page.page
    page.wait_for_function("() => typeof window.pwaWarmCache === 'function'")

    result: dict[str, Any] = page.evaluate(
        """async (urls) => {
            const recordedIds = [];
            const controller = navigator.serviceWorker.controller;
            const originalPostMessage = controller.postMessage.bind(controller);
            controller.postMessage = (msg) => {
                if (msg && msg.type === 'warm-cache') recordedIds.push(msg.requestId);
                return originalPostMessage(msg);
            };

            const r1 = await window.pwaWarmCache(urls);

            // Second call — deliberately not awaited yet, so it's still
            // in flight when the stale reply below fires.
            const p2 = window.pwaWarmCache(urls);

            // A reply for the FIRST (already-settled) call's requestId,
            // arriving late — simulates a duplicate/delayed worker
            // postMessage the fix must ignore.
            navigator.serviceWorker.dispatchEvent(new MessageEvent('message', {
                data: {
                    type: 'warm-cache-done',
                    ok: 999,
                    failed: 999,
                    requestId: recordedIds[0],
                },
            }));

            const r2 = await p2;
            return { r1, r2, recordedIds };
        }""",
        _URLS,
    )

    assert result["r1"] is not None
    assert result["r2"] is not None
    assert len(result["recordedIds"]) == 2
    assert result["recordedIds"][0] != result["recordedIds"][1], (
        "each warmCache() call must mint its own distinct requestId"
    )
    assert result["r2"]["ok"] != _STALE_SENTINEL_OK, (
        f"the second call resolved with the stale sentinel: {result['r2']!r}"
    )
    assert result["r2"]["failed"] != _STALE_SENTINEL_FAILED, (
        f"the second call resolved with the stale sentinel: {result['r2']!r}"
    )
    # The second call's real result should match the first's — both warm
    # the same two genuinely-reachable same-origin URLs.
    assert result["r2"] == result["r1"], (
        f"expected the second call's genuine result to match the first's; "
        f"got r1={result['r1']!r} r2={result['r2']!r}"
    )
