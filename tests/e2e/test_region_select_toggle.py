"""
tests/e2e/test_region_select_toggle.py — Playwright coverage for micro-region
selection on the map: no popup on select, and re-tap to deselect.

Two behaviours that only exist in the browser, both owned by the consolidated
``map.on('click')`` dispatcher in ``static/js/map.js``:

(a) Selecting a region moves the selection only — highlight, season ribbon,
    readout chip, URL fragment. It must NOT open a popup over the terrain the
    visitor just tapped. ``openRegionPopup`` is still implemented (see its
    comment in map.js) but has no trigger; a re-wired trigger would show up
    here as a ``.region-popup`` node.

(b) Tapping the selected region again deselects it: the same end state as an
    empty-canvas tap — ``snowdesk:region-selected`` with a null ``region_id``
    and a cleared URL fragment.

SNOW-642 changed what "deselected" looks like in the DOM. The readout used to
take the ``hidden`` attribute with nothing selected, and both controls beside
it are siblings keyed off ``#region-readout.has-region``, so the whole ribbon
header emptied out at once. It now persists with a "No region selected" empty
state, so the deselected state is asserted here as the ABSENCE of
``.has-region`` rather than the presence of ``hidden`` — and its sibling test
asserts the presence of ``.has-region`` rather than the absence of ``hidden``,
which after that change was true either way and therefore proved nothing.

The fill-layer hit-test is stubbed the way ``tests/e2e/test_map_marker_
exclusion.py`` established: headless Chromium doesn't reliably composite the
fill layer for a genuine pixel hit-test, so ``queryRenderedFeatures`` returns
a real feature id for the region query and delegates every other query. The
click itself goes through ``MAP.fire('click', …)``, which the consolidated
(non-layer-scoped) handler receives exactly as it would a real tap.
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
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
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


@pytest.mark.django_db(transaction=True)
def test_retapping_the_selected_region_deselects_it(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The second tap on the selected region clears the selection.

    Same end state as an empty-canvas tap: a null-``region_id`` selection
    event, the readout back to its empty state, and the ``#CH-xxxx``
    fragment gone.

    SNOW-642: "readout back to its empty state" used to be "readout chip
    dropped" (``#region-readout[hidden]``). The chip no longer hides — it
    says "No region selected" instead — so the cleared selection is read
    off ``.has-region`` and the leaf text.
    """
    _navigate_home(page, live_server.url)
    _stub_region_hit_test(page)

    _tap_region(page)
    page.wait_for_function(
        "() => (window.__regionSelections || []).some((d) => d && d.region_id)"
    )
    page.wait_for_function("() => location.hash.length > 1")

    _tap_region(page)

    page.wait_for_function(
        "() => { const s = window.__regionSelections || [];"
        " return s.length && s[s.length - 1].region_id === null; }"
    )
    page.wait_for_function(
        "() => { const el = document.getElementById('region-readout');"
        " return el && !el.classList.contains('has-region'); }"
    )
    # The chip is still on screen, and says so rather than going blank or
    # keeping the name of the region that was just deselected.
    readout = page.locator("#region-readout")
    assert readout.is_visible()
    assert readout.inner_text().strip() == "No region selected"
    page.wait_for_function("() => location.hash === '' || location.hash === '#'")


@pytest.mark.django_db(transaction=True)
def test_tapping_a_different_region_moves_the_selection(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Tapping a *different* region re-selects rather than deselecting.

    Guards the toggle against over-reach: only the already-selected region
    deselects on tap.
    """
    _navigate_home(page, live_server.url)
    _stub_region_hit_test(page)

    _tap_region(page)
    page.wait_for_function(
        "() => (window.__regionSelections || []).some((d) => d && d.region_id)"
    )

    # Re-point the stub at a second feature, then tap again.
    swapped = page.evaluate(
        """() => {
            const data = MAP.getSource('regions').serialize().data;
            const ids = data.features.filter((x) => x.id != null).map((x) => x.id);
            if (ids.length < 2) return false;
            const second = ids[1];
            const orig = MAP.queryRenderedFeatures.bind(MAP);
            MAP.queryRenderedFeatures = (geometry, options) => {
                const opts = options || geometry || {};
                const layers = opts.layers || [];
                if (layers.indexOf('regions-fill') !== -1) {
                    return [{ layer: { id: 'regions-fill' }, id: second }];
                }
                return orig(geometry, options);
            };
            return true;
        }"""
    )
    if not swapped:
        pytest.skip("test data has fewer than two regions with ids")

    _tap_region(page)

    page.wait_for_function(
        "() => { const s = window.__regionSelections || [];"
        " return s.length && s[s.length - 1].region_id; }"
    )
    # Still focused on a region — the second tap re-selected rather than
    # clearing. Asserted on .has-region, not on the absence of [hidden]:
    # SNOW-642 stopped the readout hiding at all, so the old form was
    # satisfied by the deselected state too and had stopped discriminating.
    readout = page.locator("#region-readout")
    assert "has-region" in (readout.get_attribute("class") or "")
    assert readout.inner_text().strip() != "No region selected"
