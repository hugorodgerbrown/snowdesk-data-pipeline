"""
tests/e2e/test_community_reports_empty_cache.py — Playwright regression
test for SNOW-493 finding 8: a cached-but-empty community-reports
FeatureCollection must be treated as a successful offline read-back, not
an "unavailable offline" failure.

``ensureOverlayLoaded``'s community-reports offline branch
(``static/js/map.js``) used to treat any cached collection with zero
``features`` — no reports right now, or every cached report having since
aged past the 48h window — the same as no cache at all, revealing the
"unavailable offline" warning instead of installing an (empty, but
legitimate) map source. Only a genuinely malformed cached payload should
still warn.

Modelled on ``tests/e2e/test_offline_map.py``'s
``test_community_reports_toggle_drops_expired_features_from_cache``: SW
stripped, the basemap style URL routed to abort (forces the SNOW-483
inline fallback so ``MAP`` reaches ``load`` without depending on a live
CDN), the community-reports fetch itself routed to abort (forcing
``ensureOverlayLoaded`` into its offline read-back branch), and the cache
seeded directly via ``window.pwaMapOverlayCache.putOverlay`` (no prior
real fetch needed).
"""

from __future__ import annotations

from django.conf import settings
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def test_empty_cached_collection_installs_zero_markers_without_warning(
    live_server: LiveServer, page: Page
) -> None:
    """A cached, valid, empty FeatureCollection is a hit, not a miss.

    Regression coverage for SNOW-493 finding 8: seeds
    ``data:map_overlays`` with an empty (but well-formed)
    FeatureCollection, then forces the live community-reports fetch to
    fail so the offline read-back branch runs against it. The map must
    install the ``community-reports`` source (with zero features) and
    must NOT reveal ``#map-offline-toast-community_reports``.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.route(settings.BASEMAP_STYLE_URL, lambda route: route.abort())
    page.route("**/api/community-reports.geojson", lambda route: route.abort())

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")

    page.evaluate(
        """() => window.pwaMapOverlayCache.putOverlay('community_reports', {
            type: 'FeatureCollection',
            features: [],
        })"""
    )

    toggle.click()

    page.wait_for_function("() => !!MAP.getSource('community-reports')", timeout=10000)
    features = page.evaluate(
        """() => {
            const src = MAP.getSource('community-reports');
            const data = src.serialize().data;
            return (data.features || []).length;
        }"""
    )
    assert features == 0

    # The "unavailable offline" toast must never have been revealed for
    # this resource — give any (buggy) async reveal a moment to happen
    # before asserting its absence.
    page.wait_for_timeout(300)
    assert "hidden" in (
        page.locator("#map-offline-toast-community_reports").get_attribute("class")
        or ""
    )
