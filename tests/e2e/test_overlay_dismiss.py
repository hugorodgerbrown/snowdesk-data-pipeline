"""
tests/e2e/test_overlay_dismiss.py — Playwright test for the SNOW-486 shared
overlay dismiss handler (static/js/overlays.js).

Every hand-migrated overlay's per-feature spec (test_favourites.py,
test_report_sheet.py, test_map_help.py, test_home_intro.py, …) already
covers its own hide behaviour and teardown; this file covers the shared
primitive itself, once, against a real overlay rather than a synthetic
fixture — the anonymous report-sheet state, which needs no login,
geolocation grant, or service-worker stripping to reach.

Confirms the two contract pieces static/js/overlays.js promises for any
``[data-overlay]`` carrying a ``[data-action="dismiss"]`` control:
hiding the overlay (respecting its ``hidden``-attribute idiom, per
docs/decisions/overlay-primitives.md) and dispatching a bubbling
``overlay:dismissed`` CustomEvent with ``detail.overlay`` set to the
overlay element.
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("field_observations", active=True)
def test_dismiss_click_hides_overlay_and_fires_event(
    live_server: LiveServer, page: Page
) -> None:
    """Clicking a [data-action="dismiss"] control hides its [data-overlay]
    ancestor and dispatches a bubbling overlay:dismissed event carrying
    detail.overlay — the report sheet's anonymous sign-in CTA state exercises
    both without needing a login session or a geolocation grant.
    """
    _navigate_home(page, live_server.url)

    # Listen on document (the event bubbles) before triggering the click, and
    # stash the overlay's id — the element itself isn't structured-cloneable
    # back to Python, but its id is enough to prove detail.overlay is the
    # #report-sheet element rather than some other node.
    page.evaluate(
        "() => { window.__overlayDismissedId = undefined; "
        "document.addEventListener('overlay:dismissed', (e) => { "
        "window.__overlayDismissedId = e.detail.overlay.id; }); }"
    )

    page.click("#report-btn")
    page.wait_for_selector("#report-sheet:not([hidden])")

    close_btn = page.locator('#report-sheet [data-action="dismiss"]', has_text="×")
    close_btn.click()

    # Hide idiom: map-scoped overlays use the HTML5 `hidden` attribute (not
    # the Tailwind `hidden` class shell banners use) — see
    # docs/decisions/overlay-primitives.md.
    page.wait_for_selector("#report-sheet[hidden]", state="attached")

    fired_for = cast(str, page.evaluate("() => window.__overlayDismissedId"))
    assert fired_for == "report-sheet"
