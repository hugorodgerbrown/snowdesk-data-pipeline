"""
tests/e2e/test_map_overlay_cache_isolation.py — Playwright regression test
for SNOW-493 finding 1: partition the map's offline overlay read cache
(``static/js/map_overlay_offline_cache.js``, ``data:map_overlays`` in
IndexedDB) by signed-in account.

Headline regression: user A's favourites overlay is write-through cached
while A is signed in; user B signs in afterwards on the same browser
(IndexedDB persists across the session switch) and their own live
favourites fetch is forced to fail. Without the SNOW-493 fix (``principal``
stamped on every cached row, checked on read-back), ``getOverlay`` would
happily hand B's ``ensureOverlayLoaded`` A's cached GeoJSON — B would see
A's saved pins. This test proves the offline read-back for B comes back
empty (the "unavailable offline" toast fires, no favourites source
installs) rather than serving A's cached data.

Modelled on ``tests/e2e/test_mutation_queue_account_change.py``'s
principal-switch pattern (same-browser session swap via ``_session_login``,
polling helper) and ``tests/e2e/test_offline_map.py``'s favourites-cache
tests (SW stripped + ``page.route`` abort to force the offline-fetch-failed
branch deterministically, without a real offline browser context).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import _session_login
from tests.factories import FavouriteFactory, SubscriberFactory


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map to boot.

    Same technique as ``test_mutation_queue_account_change.py``'s helper of
    the same name — duplicated here (rather than imported) so this module
    has no dependency on that file's internals.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )


def _poll(
    page: Page,
    predicate_js: str,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> Any:
    """Poll ``page.evaluate(predicate_js)`` until it returns a truthy value.

    See ``test_mutation_queue_account_change.py``'s module docstring for
    why this is used instead of ``page.wait_for_function`` with an async
    predicate.
    """
    deadline = time.monotonic() + timeout_s
    result = None
    while time.monotonic() < deadline:
        result = page.evaluate(predicate_js)
        if result:
            return result
        time.sleep(interval_s)
    raise AssertionError(
        f"condition never became true: {predicate_js!r} (last={result!r})"
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_offline_favourites_cache_never_leaks_across_account_switch(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """B's offline favourites read-back never serves A's cached pins.

    User A signs in and the boot-time favourites fetch (default-on,
    SNOW-414) succeeds for real, write-through caching the payload into
    ``data:map_overlays`` stamped with A's principal. User B then signs in
    on the SAME browser — IndexedDB is per-origin, not per-session, so
    A's cached row is still physically present — and B's own favourites
    fetch is forced to fail (routed to abort). Before the SNOW-493 fix,
    ``getOverlay`` had no notion of "whose" cache entry this was and would
    install A's pins for B; after the fix, the principal mismatch makes
    the read-back a miss, so B sees the "unavailable offline" toast and no
    favourites source at all.
    """
    with django_db_blocker.unblock():
        subscriber_a = SubscriberFactory.create()
        FavouriteFactory.create(user=subscriber_a.user, name="A's Peak")
        subscriber_b = SubscriberFactory.create()

    _session_login(page.context, live_server.url, subscriber_a.user)
    _navigate_home_with_sw_stripped(page, live_server.url)

    row = _poll(
        page,
        "async () => window.pwaDb.get('data:map_overlays', 'favourites')",
    )
    assert row["principal"] == str(subscriber_a.user.pk), row

    # User B signs in on the same browser context and their own favourites
    # fetch is forced to fail, driving ensureOverlayLoaded into the
    # offline read-back branch against the cache A's session populated.
    _session_login(page.context, live_server.url, subscriber_b.user)
    page.route("**/favourites/favourites.geojson", lambda route: route.abort())
    _navigate_home_with_sw_stripped(page, live_server.url)

    page.wait_for_selector("#map-offline-toast-favourites:not(.hidden)", timeout=10000)
    assert page.evaluate("() => !!MAP.getSource('favourites')") is False, (
        "B's map must not install a favourites source built from A's cached pins"
    )


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_offline_community_reports_cache_survives_account_switch(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Public community-reports cache is NOT partitioned by account (P2).

    Community reports are public data with no account binding. A report
    collection cached while user A is signed in must still read back after
    user B signs in on the same browser — tying it to a principal (as the
    favourites cache above deliberately is) would wrongly hide public data
    across an account change: a report cached signed-in would vanish on
    logout, and an anonymous one on login. Regression coverage for
    SNOW-493 P2 — before the fix, ``putOverlay`` / ``getOverlay`` applied
    principal isolation to every resource, so B's read-back of A's cached
    reports missed and the "unavailable offline" toast fired.
    """
    with django_db_blocker.unblock():
        subscriber_a = SubscriberFactory.create()
        subscriber_b = SubscriberFactory.create()

    # A signs in and seeds the community-reports cache directly (no prior
    # real fetch needed). Pre-fix this row was stamped with A's principal.
    _session_login(page.context, live_server.url, subscriber_a.user)
    _navigate_home_with_sw_stripped(page, live_server.url)
    page.evaluate(
        """() => window.pwaMapOverlayCache.putOverlay('community_reports', {
            type: 'FeatureCollection',
            features: [{
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [7.5, 46.5] },
                properties: { id: 'cr-1' },
            }],
        })"""
    )
    _poll(
        page,
        "async () => window.pwaDb.get('data:map_overlays', 'community_reports')",
    )

    # B signs in on the same browser; the live community-reports fetch is
    # forced to fail so the offline read-back branch runs against A's row.
    _session_login(page.context, live_server.url, subscriber_b.user)
    page.route("**/api/community-reports.geojson", lambda route: route.abort())
    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    toggle.click()

    # Post-fix: the public cache reads back for B, so the source installs
    # with A's cached report and the unavailable toast never fires.
    page.wait_for_function("() => !!MAP.getSource('community-reports')", timeout=10000)
    features = page.evaluate(
        """() => {
            const src = MAP.getSource('community-reports');
            return (src.serialize().data.features || []).length;
        }"""
    )
    assert features == 1, "B must read back A's public community-reports cache"

    page.wait_for_timeout(300)
    assert "hidden" in (
        page.locator("#map-offline-toast-community_reports").get_attribute("class")
        or ""
    )
