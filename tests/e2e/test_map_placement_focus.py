"""
tests/e2e/test_map_placement_focus.py — Playwright tests for
distraction-free pin positioning (``static/js/map_placement_focus.js``).

While a pin is being positioned, everything Snowdesk draws over the basemap
is hidden, and it all comes back the moment the pin is saved or the flow is
cancelled. ``tests/js/test_map_placement_focus.js`` covers the module's
bookkeeping against a fake map; what needs a real browser is the *wiring* —
that the favourite-add flow actually arms the place-picker, that the picker
actually reaches ``window.PlacementFocus``, and that a MapLibre style really
does honour the visibility writes.

Uses the ``favourites_page`` fixture (``tests/e2e/conftest.py``) — a plain
``page`` + ``live_server`` with a subscriber session. The favourite flow is
the one placement surface reachable without a GPS permission grant (the
field-observation flow waits on the geolocate round-trip first) and it shares
``place_picker.js`` with it, so covering it covers both.

Layer visibility is read back through ``MAP.getLayoutProperty`` rather than
by looking at pixels: the assertion is about MapLibre's style state, and a
canvas screenshot would depend on WebGL frame timing in headless Chromium —
the same flake the rest of the map e2e suite deliberately avoids.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import FavouritesPage

# Every layer the app installs over the basemap on a default (CH-only,
# resorts-off) cold load. The choropleth and its outlines are the layers a
# user actually sees; ``regions-label`` and ``regions-line-selected`` come
# from the same install path.
_APP_LAYER_IDS = (
    "regions-fill",
    "regions-line",
    "regions-line-selected",
    "regions-label",
)


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_function("() => MAP.getLayer('regions-fill') !== undefined")


def _visibilities(page: Page) -> dict[str, str]:
    """Read every app layer's current ``visibility``, defaulting an unset
    value to 'visible' (MapLibre's own default for the property).
    """
    result: dict[str, str] = page.evaluate(
        """(ids) => Object.fromEntries(ids.map(
            (id) => [id, MAP.getLayer(id)
                ? (MAP.getLayoutProperty(id, 'visibility') || 'visible')
                : 'missing'],
        ))""",
        list(_APP_LAYER_IDS),
    )
    return result


def _start_placing_favourite(page: Page) -> None:
    """Open the favourite sheet and wait for the place-picker to arm."""
    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")
    page.wait_for_selector("#favourite-create-form")
    page.wait_for_selector("#map-place-pin:not([hidden])")


@pytest.mark.django_db(transaction=True)
def test_placing_a_favourite_clears_the_map_to_the_basemap(
    favourites_page: FavouritesPage,
) -> None:
    """Arming the place-picker hides every app layer, leaving the basemap."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    assert _visibilities(page) == dict.fromkeys(_APP_LAYER_IDS, "visible")

    _start_placing_favourite(page)

    assert _visibilities(page) == dict.fromkeys(_APP_LAYER_IDS, "none")
    assert page.evaluate("() => window.PlacementFocus.isActive()") is True
    # The basemap itself is untouched — its layers are backed by vector
    # sources, which is exactly the distinction the module draws.
    assert (
        page.evaluate(
            """() => {
                const style = MAP.getStyle();
                return style.layers.filter((l) => {
                    const src = style.sources[l.source];
                    return src && src.type !== 'geojson'
                        && MAP.getLayoutProperty(l.id, 'visibility') === 'none';
                }).length;
            }"""
        )
        == 0
    )


@pytest.mark.django_db(transaction=True)
def test_cancelling_placement_restores_the_layers(
    favourites_page: FavouritesPage,
) -> None:
    """Closing the sheet without saving brings every hidden layer back."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)
    _start_placing_favourite(page)
    assert _visibilities(page) == dict.fromkeys(_APP_LAYER_IDS, "none")

    page.keyboard.press("Escape")
    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")

    assert _visibilities(page) == dict.fromkeys(_APP_LAYER_IDS, "visible")
    assert page.evaluate("() => window.PlacementFocus.isActive()") is False


@pytest.mark.django_db(transaction=True)
def test_submitting_a_pin_restores_the_layers(
    favourites_page: FavouritesPage,
) -> None:
    """Saving the pin restores the layers as soon as the optimistic
    confirmation renders — the user does not have to wait for the queued
    POST to replay before the map comes back.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)
    _start_placing_favourite(page)

    page.evaluate("() => MAP.setCenter([7.6, 46.2])")
    page.fill("#favourite-name-input", "Focus Peak")
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.click("#favourite-create-form button[type=submit]")
    page.wait_for_selector('#favourite-sheet:has-text("Pin saved")')

    assert _visibilities(page) == dict.fromkeys(_APP_LAYER_IDS, "visible")
    assert page.evaluate("() => window.PlacementFocus.isActive()") is False


@pytest.mark.django_db(transaction=True)
def test_placement_keeps_an_overlay_that_was_already_off_off(
    favourites_page: FavouritesPage,
) -> None:
    """Restoring on exit means "back to how it was", not "everything on" —
    an overlay the user had switched off stays off.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # Turn the micro-regions choropleth off from the layers menu, so the
    # map has a deliberately-hidden app layer before placement starts.
    page.evaluate(
        """() => MAP.setLayoutProperty('regions-fill', 'visibility', 'none')"""
    )

    _start_placing_favourite(page)
    page.keyboard.press("Escape")
    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")

    visibilities = _visibilities(page)
    assert visibilities["regions-fill"] == "none"
    assert visibilities["regions-line"] == "visible"


@pytest.mark.django_db(transaction=True)
def test_placement_dismisses_an_open_region_popup(
    favourites_page: FavouritesPage, _load_test_data: None
) -> None:
    """A popup anchored to a region that is no longer drawn would be a
    leftover, so map.js closes it when placement focus starts.

    Selection is driven by stubbing ``queryRenderedFeatures`` for the
    fill-layer query and firing a synthetic click — the deterministic
    pattern ``tests/e2e/test_map_marker_exclusion.py`` established, since
    headless Chromium doesn't reliably composite the fill layer for a
    genuine pixel hit-test.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.wait_for_function(
        "() => !!MAP.getSource('regions') && "
        "(MAP.getSource('regions').serialize().data.features || [])"
        ".some((f) => f.id != null)"
    )
    page.evaluate(
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
        }"""
    )
    point = page.evaluate(
        "() => { const p = MAP.project(MAP.getCenter()); return { x: p.x, y: p.y }; }"
    )
    page.evaluate(
        "({ x, y }) => MAP.fire('click', { lngLat: MAP.getCenter(), point: { x, y } })",
        point,
    )
    page.wait_for_selector(".region-popup")

    _start_placing_favourite(page)

    assert page.locator(".region-popup").count() == 0
