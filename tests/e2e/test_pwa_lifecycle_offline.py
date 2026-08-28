"""tests/e2e/test_pwa_lifecycle_offline.py — A user reloads a page they have already visited, with no connection.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: P7
"""

from __future__ import annotations

from tests.e2e.conftest import PwaPage

# ---------------------------------------------------------------------------
# P7 — offline reload of a cached page, including /?d= variants
# ---------------------------------------------------------------------------


def test_offline_reload_of_visited_date_url(pwa_page: PwaPage) -> None:
    """A /?d= URL fetched online is served from its own exact cache entry offline.

    Navigating to the dated URL directly (rather than driving the season
    scrubber, which map.js's own commitDate() does via history.replaceState
    with no fetch — see test_offline_reload_of_never_visited_date_url below)
    means the SW's network-first navigate strategy caches this exact URL,
    including its query string.
    """
    page = pwa_page.page
    dated_url = pwa_page.live_server_url + "/?d=2026-04-08"
    page.goto(dated_url)
    page.wait_for_load_state("load")
    page.wait_for_selector("#season-scrubber")

    page.context.set_offline(True)
    try:
        page.wait_for_selector(
            '[data-network-indicator][data-network-state="offline"]', timeout=5000
        )
        page.reload()
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)

    assert page.url == dated_url
    page.wait_for_selector("#season-scrubber")
    assert page.locator("h1", has_text="This page isn't available offline").count() == 0


# ---------------------------------------------------------------------------
# P8 — connectivity symbol, freshness toast, and network-required controls
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P9 — offline navigation to a URL never visited
# ---------------------------------------------------------------------------
