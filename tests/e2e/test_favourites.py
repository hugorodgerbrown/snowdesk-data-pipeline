"""tests/e2e/test_favourites.py — A signed-in user drops a favourite pin on the map and it persists.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

SNOW-1485: the second test drops the pin by holding a finger on the map,
which needs MapLibre's real canvas and real touch events (sent through a
Chromium CDP session; Playwright's touchscreen only taps).

Scenario: 12c
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from apps.locations.models import Location
from tests.e2e.conftest import FavouritesPage


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/map/")
    page.wait_for_load_state("domcontentloaded")
    # SNOW-794: `state="attached"`, not the default "visible". The scrubber
    # is hidden while the day the map is showing is off its track, and it is
    # not rendered below 640px at all. SNOW-1431 put today on the track in
    # every month, but `data-state="ready"` is still set on a hidden element,
    # and it is the ratings-loaded signal this wait actually wants;
    # visibility never was.
    page.wait_for_selector('#season-scrubber[data-state="ready"]', state="attached")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


# Polls the map's favourites source for the AUTHORITATIVE pin — the one the
# drain replayed — rather than the optimistic `pending` feature the sheet
# renders straight away. Reading the source once races that fetch: reliable
# locally, flaky in CI where the create round trip is slower.
_AUTHORITATIVE_PIN_JS = """() => {
    const src = MAP.getSource('favourites');
    if (!src) return false;
    const data = src.serialize().data;
    return (data.features || []).some(
        (f) => f.properties && f.properties.name === 'Test Peak'
               && !f.properties.pending,
    );
}"""


@pytest.mark.django_db(transaction=True)
def test_signed_in_add_flow_creates_favourite(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Add -> pan -> name -> Save creates a Favourite at the map's centre."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # SNOW-1481: the star opens Saved › Places; its header + drops a pin.
    page.click("#favourite-add-btn")
    page.locator("[data-panel-add]:visible").click()
    page.wait_for_selector("#favourite-create-form")
    page.wait_for_selector("#map-place-pin:not([hidden])")

    # MAP.setCenter fires the same 'moveend' a real pan would, without
    # depending on WebGL frame timing. The pin never moves; the map under
    # it does — SNOW-475's place-picker, not a draggable marker.
    page.evaluate("() => MAP.setCenter([7.6, 46.2])")
    page.wait_for_function(
        "() => { "
        "const el = document.querySelector('#favourite-create-form input[name=lat]'); "
        "return el && Math.abs(parseFloat(el.value) - 46.2) < 0.01; "
        "}"
    )

    page.fill("#favourite-name-input", "Test Peak")
    # SNOW-479: Save routes through the client mutation queue, not htmx.
    page.wait_for_function(
        "() => typeof window.pwaMutationQueue === 'object'"
        " && typeof window.pwaDb === 'object'"
    )
    page.click("#favourite-create-form button[type=submit]")

    page.wait_for_function(_AUTHORITATIVE_PIN_JS)

    with django_db_blocker.unblock():
        pin = Location.objects.get(
            favourites__user=favourites_page.account.user, favourites__name="Test Peak"
        )
        assert pin.latitude == pytest.approx(46.2, abs=0.01)
        assert pin.longitude == pytest.approx(7.6, abs=0.01)


@pytest.mark.django_db(transaction=True)
def test_hold_on_the_map_then_save_pin(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Hold -> the held point under the pin -> pan -> Save pin -> Save."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)
    box = page.locator("#map canvas").bounding_box()
    assert box is not None
    x, y = box["width"] / 2 + 60, box["height"] / 3
    held = page.evaluate(f"() => MAP.unproject([{x}, {y}]).toArray()")
    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True})
    point = {"x": box["x"] + x, "y": box["y"] + y}
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
    page.wait_for_selector("#map-hold-bar:not([hidden])")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    centre = page.evaluate("() => MAP.getCenter().toArray()")
    assert centre == pytest.approx(held, abs=1e-4)

    # Fine-tune, then hand over: the form opens on the panned point.
    page.evaluate("() => MAP.setCenter([7.6, 46.2])")
    page.click('#map-hold-bar [data-hold-action="save-pin"]')
    page.wait_for_selector("#favourite-create-form")
    page.fill("#favourite-name-input", "Test Peak")
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")
    page.click("#favourite-create-form button[type=submit]")
    page.wait_for_function(_AUTHORITATIVE_PIN_JS)

    with django_db_blocker.unblock():
        pin = Location.objects.get(favourites__name="Test Peak")
        assert pin.latitude == pytest.approx(46.2, abs=0.01)
        assert pin.longitude == pytest.approx(7.6, abs=0.01)
