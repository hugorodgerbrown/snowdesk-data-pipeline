"""
tests/e2e/test_place_pin_clearance.py — Playwright tests for SNOW-538: the
place-picker pin must stay visible above the sheet driving the placement,
and the favourite sheet must not raise the on-screen keyboard before the
pin has been positioned.

Both bugs only bite on a phone, so every test here runs at a 375x812
viewport (the idiom in ``test_basemap_menu_clip.py``): that is the only
shape where the bottom-docked sheet is full-width and tall enough to reach
past the map's vertical midpoint. On a desktop viewport the sheet is a
bottom-right card that never covers the centre, the pin is not lifted at
all, and there is nothing to assert.

The report flow is the worse of the two — its form carries a location line,
five problem buttons and Cancel — which is why it, not the favourite sheet,
is what the user reported. Setup for it mirrors ``test_report_sheet.py``
(SW stripped, ``_session_login``, a stubbed geolocation fix).

The pin's anchor is the bottom of its box, not its centre: the teardrop is
translated -100% vertically so its point, not its middle, sits on the
coordinate being chosen (see ``.map-place-pin`` in static/css/map.css).
Every assertion below is therefore about ``bounding_box`` bottom edges.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import FavouritesPage, _session_login
from tests.factories import AccountFactory, UserFactory

# Reading the coordinate under the pin straight off the DOM: the pin's
# horizontal centre and its bottom edge, converted to canvas coordinates and
# unprojected. This is the invariant the fix turns on — the coordinate the
# form will POST has to be the one under the pin the user can see, which is
# no longer MAP.getCenter() once the pin is lifted clear of the sheet.
_COORD_UNDER_PIN = """
    () => {
        const pin = document.getElementById('map-place-pin').getBoundingClientRect();
        const canvas = MAP.getCanvas().getBoundingClientRect();
        const at = MAP.unproject([
            pin.left + pin.width / 2 - canvas.left,
            pin.bottom - canvas.top,
        ]);
        return [at.lat, at.lng];
    }
"""


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Load / on a phone-sized viewport and wait for the map to boot."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """As ``_navigate_home``, with ``navigator.serviceWorker`` stripped.

    Stripping serviceWorker before any page script runs makes sw_register.js
    bail out immediately — the same helper ``test_report_sheet.py`` uses, for
    the same reason (no real SW lifecycle needed here).
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    _navigate_home(page, live_server_url)


def _assert_pin_clears_sheet(page: Page, sheet_selector: str) -> None:
    """The pin's point must sit above the sheet's top edge, with the whole
    teardrop visible above it — otherwise the user cannot see where they are
    placing it, which is the bug.
    """
    pin_box = page.locator("#map-place-pin").bounding_box()
    sheet_box = page.locator(sheet_selector).bounding_box()
    assert pin_box is not None and sheet_box is not None
    assert pin_box["y"] >= 0, "the whole pin must be on screen"
    assert pin_box["y"] + pin_box["height"] <= sheet_box["y"], (
        f"pin bottom {pin_box['y'] + pin_box['height']} should clear "
        f"sheet top {sheet_box['y']}"
    )


def _assert_form_coords_match_pin(page: Page, form_selector: str) -> None:
    """The lat/lon the form will POST must be the point under the pin."""
    lat_under_pin, lon_under_pin = page.evaluate(_COORD_UNDER_PIN)
    lat = page.eval_on_selector(f"{form_selector} input[name=lat]", "el => el.value")
    lon = page.eval_on_selector(f"{form_selector} input[name=lon]", "el => el.value")
    assert float(lat) == pytest.approx(lat_under_pin, abs=0.02)
    assert float(lon) == pytest.approx(lon_under_pin, abs=0.02)


@pytest.mark.django_db(transaction=True)
def test_report_refine_pin_clears_the_sheet(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Refining a GPS fix shows the pin above the report sheet, not behind it."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-form")
    page.click('[data-action="refine-location"]')
    page.wait_for_selector("#map-place-pin:not([hidden])")

    _assert_pin_clears_sheet(page, "#report-sheet")
    _assert_form_coords_match_pin(page, "#report-form")


@pytest.mark.django_db(transaction=True)
def test_report_refine_keeps_the_gps_fix_under_the_pin(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """``recenterTo`` puts the GPS fix under the lifted pin, not under the
    map's centre — otherwise refining would silently shift the report north
    of where the device actually is before the user touched anything.
    """
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#report-btn")
    page.wait_for_selector("#report-form")
    page.click('[data-action="refine-location"]')
    page.wait_for_selector("#map-place-pin:not([hidden])")

    lat_under_pin, lon_under_pin = page.evaluate(_COORD_UNDER_PIN)
    assert lat_under_pin == pytest.approx(46.10, abs=0.02)
    assert lon_under_pin == pytest.approx(7.10, abs=0.02)


@pytest.mark.django_db(transaction=True)
def test_favourite_pin_clears_the_sheet(favourites_page: FavouritesPage) -> None:
    """The favourite create sheet leaves its pin visible too, and the coords
    it will POST track the pin rather than the map's centre.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-create-form")
    page.wait_for_selector("#map-place-pin:not([hidden])")

    _assert_pin_clears_sheet(page, "#favourite-sheet")
    _assert_form_coords_match_pin(page, "#favourite-create-form")


@pytest.mark.django_db(transaction=True)
def test_favourite_sheet_does_not_focus_the_name_input(
    favourites_page: FavouritesPage,
) -> None:
    """Opening the create sheet must not focus the name field — on a phone
    that raises the keyboard over the map before the pin has been placed.
    Focus goes to the sheet container instead, so the form stays reachable.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-create-form")

    assert page.evaluate("() => document.activeElement.id") == "favourite-sheet"

    # The field is still there and still usable — it just isn't grabbed.
    page.fill("#favourite-name-input", "Test Peak")
    assert page.evaluate("() => document.activeElement.id") == "favourite-name-input"
