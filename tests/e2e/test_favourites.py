"""tests/e2e/test_favourites.py — A signed-in user drops a favourite pin on the map and it persists.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: none — no manual scenario covers favourites yet; add one and cite it here
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from apps.favourites.models import Favourite
from tests.e2e.conftest import FavouritesPage


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
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

    page.click("#favourite-add-btn")
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
        favourite = Favourite.objects.get(
            user=favourites_page.account.user, name="Test Peak"
        )
        assert favourite.latitude == pytest.approx(46.2, abs=0.01)
        assert favourite.longitude == pytest.approx(7.6, abs=0.01)
