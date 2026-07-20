"""
tests/e2e/test_map_marker_exclusion.py — Playwright coverage for the SNOW-445
map marker "exclusion zone".

A tap on a favourite / community-observation marker must activate that marker
and must NOT also select (and pop the tooltip of) the region polygon beneath
it. The click dispatch was consolidated into a single generic
``map.on('click')`` handler that decides marker-vs-region by point-testing the
marker glyph under the tap (``markerUnderPoint`` in ``static/js/map.js`` — an
exact-point ``queryRenderedFeatures``, the same hit-test that drives the
pointer cursor); these tests exercise both branches of that decision.

Testing notes:

- The favourite branch injects the marker via a ``MAP.queryRenderedFeatures``
  stub rather than relying on the ★ symbol layer compositing a frame in
  headless Chromium (the same WebGL-timing limitation ``test_favourites.py``
  calls out). The stub returns the favourite only for the marker-layer query
  ``markerUnderPoint`` issues, delegating every other query to the real
  implementation, so the production routing code runs unchanged — only the
  glyph-hit-test input is made deterministic.
- The region-baseline branch needs no stub: ``regions-fill`` reliably renders
  under ``_load_test_data``, so a synthetic click at a region point drives the
  real ``queryRenderedFeatures`` → ``selectFeature`` path.
- Both drive the click via ``MAP.fire('click', …)`` — the consolidated handler
  is a plain (non-layer-scoped) listener, which is the only kind a synthetic
  fire reaches (layer-scoped handlers need a real DOM event's internal
  delegation), so the synthetic path and the real tap path are the same code.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.e2e.conftest import FavouritesPage
from tests.factories import FavouriteFactory, FieldObservationFactory

# Marker layers that must always render above every other layer (SNOW-445).
_MARKER_LAYERS = {
    "favourites-pin",
    "favourites-label",
    "community-reports-clusters",
    "community-reports-cluster-count",
    "community-reports-point",
}

# A point inside CH-4115 (Martigny/Verbier), for which ``_load_test_data``
# seeds a region boundary — so ``regions-fill`` renders a feature here and the
# exclusion guard is genuinely exercised rather than trivially passing.
_LAT = 46.10
_LON = 7.10


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_favourite_tap_does_not_select_region_underneath(
    favourites_page: FavouritesPage,
    django_db_blocker: Any,
    _load_test_data: None,
) -> None:
    """Tapping a favourite opens its sheet and leaves the region unselected.

    Regression coverage for the jarring double-action the exclusion zone
    fixes: before SNOW-445 a favourite tap fired BOTH the favourites-pin
    handler (open the detail sheet) and the regions-fill handler (select the
    region + pop its tooltip), because MapLibre has no stopPropagation between
    layer-scoped click handlers.
    """
    page = favourites_page.page
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user,
            name="Exclusion Peak",
            latitude=_LAT,
            longitude=_LON,
        )
    fav_uuid = str(favourite.uuid)

    _navigate_home(page, favourites_page.live_server_url)

    # The favourites overlay is eager + default-on for eligible users, so the
    # pin layer installs at boot once the boot fetch returns this favourite.
    # markerUnderPoint only queries layers that are actually present, so the
    # layer must exist for the stubbed feature to be considered.
    page.wait_for_function("() => !!MAP.getLayer('favourites-pin')")

    # Make the glyph hit-test deterministic: return the favourite for the
    # marker-layer query markerUnderPoint issues, delegate everything else.
    page.evaluate(
        """(uuid) => {
            const orig = MAP.queryRenderedFeatures.bind(MAP);
            MAP.queryRenderedFeatures = (geometry, options) => {
                const opts = options || geometry || {};
                const layers = opts.layers || [];
                if (layers.indexOf('favourites-pin') !== -1) {
                    return [{
                        layer: { id: 'favourites-pin' },
                        geometry: { type: 'Point', coordinates: [7.10, 46.10] },
                        properties: { uuid, name: 'Exclusion Peak' },
                    }];
                }
                return orig(geometry, options);
            };
        }""",
        fav_uuid,
    )

    # Record any region selection the click dispatches. The homepage may carry
    # a default readout selection from page load; installing the listener AFTER
    # navigation isolates the click's own effect from that pre-existing state.
    page.evaluate(
        """() => {
            window.__regionSelectedByClick = null;
            document.addEventListener('snowdesk:region-selected', (e) => {
                if (e.detail && e.detail.region_id) {
                    window.__regionSelectedByClick = e.detail.region_id;
                }
            });
        }"""
    )

    point = page.evaluate(
        "() => { const p = MAP.project([7.10, 46.10]); return { x: p.x, y: p.y }; }"
    )
    page.evaluate(
        "({ x, y }) => MAP.fire('click', {"
        " lngLat: { lng: 7.10, lat: 46.10 },"
        " point: { x, y } })",
        point,
    )

    # The favourite's own detail sheet opens (marker activated) ...
    page.wait_for_selector(f"#favourite-{fav_uuid}")
    # ... and the tap neither opened a region tooltip (selectFeature ->
    # openRegionPopup renders a .region-popup) ...
    assert page.locator(".region-popup").count() == 0
    # ... nor dispatched a region selection. Give a real region click a moment
    # to have fired the event, then assert it never did.
    page.wait_for_timeout(250)
    assert page.evaluate("() => window.__regionSelectedByClick") is None


@pytest.mark.django_db(transaction=True)
def test_region_tap_still_selects_when_no_marker_near(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """With no marker under the tap, a region click still selects the region.

    Guards the other half of the consolidated dispatcher: the exclusion carve-
    out must not swallow ordinary region taps. No favourites/observations are
    present, so markerUnderPoint returns null and the click falls through to
    selectFeature.
    """
    _navigate_home(page, live_server.url)

    # The regions geojson source is added asynchronously after style load — wait
    # for its features before reading a real id from it.
    page.wait_for_function(
        "() => !!MAP.getSource('regions') && "
        "(MAP.getSource('regions').serialize().data.features || [])"
        ".some((f) => f.id != null)"
    )

    # Pick a real region feature from the loaded source, then make the fill-layer
    # hit-test deterministic by returning it for the region query the handler
    # issues — headless Chromium doesn't reliably composite the fill layer for a
    # genuine pixel hit-test (the WebGL-timing limitation test_favourites.py
    # documents). No marker overlays are present, so markerUnderPoint returns
    # null without querying and the click falls through to the region branch.
    region_id = page.evaluate(
        """() => {
            const data = MAP.getSource('regions').serialize().data;
            const f = data.features.find((x) => x.id != null);
            const orig = MAP.queryRenderedFeatures.bind(MAP);
            MAP.queryRenderedFeatures = (geometry, options) => {
                const opts = options || geometry || {};
                const layers = opts.layers || [];
                if (layers.indexOf('regions-fill') !== -1) {
                    return [{ layer: { id: 'regions-fill' }, id: f.id }];
                }
                return orig(geometry, options);
            };
            window.__lastRegionSelected = undefined;
            document.addEventListener('snowdesk:region-selected', (e) => {
                window.__lastRegionSelected = e.detail;
            });
            return f.id;
        }"""
    )
    assert region_id is not None

    point = page.evaluate(
        "() => { const p = MAP.project([7.10, 46.10]); return { x: p.x, y: p.y }; }"
    )
    page.evaluate(
        "({ x, y }) => MAP.fire('click', {"
        " lngLat: { lng: 7.10, lat: 46.10 },"
        " point: { x, y } })",
        point,
    )

    # selectFeature dispatches snowdesk:region-selected with a real region id.
    page.wait_for_function(
        "() => window.__lastRegionSelected && window.__lastRegionSelected.region_id"
    )
    detail = page.evaluate("() => window.__lastRegionSelected")
    assert detail["region_id"]


@override_flag("favourites", active=True)
@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_pin_markers_stay_above_a_later_installed_overlay(
    favourites_page: FavouritesPage,
    django_db_blocker: Any,
    _load_test_data: None,
) -> None:
    """Point markers always render above every other layer, never behind it.

    SNOW-445: MapLibre paints layers in insertion order, so an overlay
    installed *after* the pins stacks on top and buries them. Here the
    favourites pin exists from boot, then the resorts overlay (a non-marker
    layer that appends on top) and the community-reports overlay are toggled
    on. Every install path calls ``raiseMarkerLayers()``, which lifts the pin
    layers back to the top — so after the later installs, every marker layer
    must sit above every non-marker layer in the style order. The resorts
    toggle is the load-bearing case: without the raise, ``resorts-label``
    (appended after the favourites pin) would sit above it and this assertion
    would fail.
    """
    page = favourites_page.page
    with django_db_blocker.unblock():
        FavouriteFactory.create(
            user=favourites_page.subscriber.user,
            name="Summit Pin",
            latitude=_LAT,
            longitude=_LON,
        )
        FieldObservationFactory.create(
            latitude=_LAT,
            longitude=_LON,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )

    _navigate_home(page, favourites_page.live_server_url)
    # Favourites overlay is eager for eligible users — its pin layer installs
    # at boot, before the overlays below are toggled on.
    page.wait_for_function("() => !!MAP.getLayer('favourites-pin')")

    page.click("#basemap-toggle")

    # Resorts overlay: a non-marker layer that appends on top of the pins.
    resorts_toggle = page.locator('[data-overlay-key="resorts"]')
    resorts_toggle.wait_for(state="visible")
    resorts_toggle.click()
    page.wait_for_function("() => !!MAP.getLayer('resorts-pin')")

    # Community-reports overlay: installs its own marker layers, also later.
    reports_toggle = page.locator('[data-overlay-key="community_reports"]')
    reports_toggle.wait_for(state="visible")
    with page.expect_response(lambda r: "/api/community-reports.geojson" in r.url):
        reports_toggle.click()
    page.wait_for_function("() => !!MAP.getLayer('community-reports-point')")

    order = page.evaluate("() => MAP.getStyle().layers.map((l) => l.id)")
    present_markers = [layer_id for layer_id in order if layer_id in _MARKER_LAYERS]
    # Both overlays contributed marker layers.
    assert "favourites-pin" in present_markers
    assert "community-reports-point" in present_markers

    lowest_marker_index = min(order.index(layer_id) for layer_id in present_markers)
    highest_other_index = max(
        i for i, layer_id in enumerate(order) if layer_id not in _MARKER_LAYERS
    )
    # Every marker sits above every non-marker layer (basemap, choropleth,
    # outlines, resort pins). Without the raise, favourites-pin (installed at
    # boot) would fall below the community-reports layers installed on toggle.
    assert lowest_marker_index > highest_other_index
