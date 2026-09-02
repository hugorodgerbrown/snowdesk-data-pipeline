"""tests/e2e/test_region_select_toggle.py — A user taps a region on the map and its readout appears.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: M2
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

# A point inside CH-4115 (Martigny/Verbier), which ``_load_test_data`` seeds
# with a boundary and RegionDayRating rows.
_LAT = 46.10
_LON = 7.10


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    # SNOW-794: `state="attached"`, not the default "visible". The scrubber
    # is a SEASON scrubber and is not rendered at all while the day the map
    # is showing falls outside the season — which a bare `/` in the
    # off-season is, since the default day is today. `data-state="ready"` is
    # still set on the hidden element, and it is the season-data signal this
    # wait actually wants; visibility never was.
    page.wait_for_selector('#season-scrubber[data-state="ready"]', state="attached")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _assert_transparent_fill_is_still_queryable(page: Page) -> None:
    """Set the bulletin fill to its off step, then prove it still hit-tests."""
    page.wait_for_function(
        "() => MAP.queryRenderedFeatures({ layers: ['regions-fill'] }).length > 0"
    )
    page.evaluate("() => document.querySelector('[data-bulletins-step=\"0\"]').click()")
    page.wait_for_function(
        "() => MAP.getPaintProperty('regions-fill', 'fill-opacity') === 0"
    )
    assert page.evaluate(
        "() => MAP.queryRenderedFeatures({ layers: ['regions-fill'] }).length > 0"
    )


def _stub_region_hit_test(page: Page) -> str:
    """Pin the fill-layer hit-test to one real region feature and record every
    ``snowdesk:region-selected`` detail. Returns that feature's id.
    """
    page.wait_for_function(
        "() => !!MAP.getSource('regions') && "
        "(MAP.getSource('regions').serialize().data.features || [])"
        ".some((f) => f.id != null)"
    )
    feature_id: str = page.evaluate(
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
            window.__regionSelections = [];
            document.addEventListener('snowdesk:region-selected', (e) => {
                window.__regionSelections.push(e.detail);
            });
            return String(f.id);
        }"""
    )
    return feature_id


def _tap_region(page: Page) -> None:
    """Fire a synthetic map click at the stubbed region's point."""
    point = page.evaluate(
        "() => { const p = MAP.project([%s, %s]); return { x: p.x, y: p.y }; }"
        % (_LON, _LAT)
    )
    page.evaluate(
        "({ x, y }) => MAP.fire('click', {"
        " lngLat: { lng: %s, lat: %s }, point: { x, y } })" % (_LON, _LAT),
        point,
    )


@pytest.mark.django_db(transaction=True)
def test_region_tap_selects_without_opening_a_popup(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """A region tap selects the region and overlays nothing on the map."""
    _navigate_home(page, live_server.url)

    # SNOW-656: with Bulletins switched off the choropleth goes TRANSPARENT,
    # not hidden, because the fill is the hit-test target. That a fully
    # transparent layer still answers ``queryRenderedFeatures`` is a fact
    # about MapLibre rather than anything our code computes, so it needs a
    # real browser — tests/js pins the layer state, this pins the consequence.
    _assert_transparent_fill_is_still_queryable(page)

    _stub_region_hit_test(page)

    _tap_region(page)

    # The selection lands (the ribbon/readout listen to this same event; the
    # chip itself starts on the default region, so the event is the signal
    # that isolates the tap's own effect).
    page.wait_for_function(
        "() => (window.__regionSelections || []).some((d) => d && d.region_id)"
    )
    page.wait_for_function("() => location.hash.length > 1")

    # Nothing is drawn over the map. Give a popup fetch time to have landed
    # before asserting its absence.
    page.wait_for_timeout(250)
    assert page.locator(".region-popup").count() == 0
    assert page.locator(".maplibregl-popup").count() == 0
