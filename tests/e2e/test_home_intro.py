"""
tests/e2e/test_home_intro.py — Playwright tests for the #home-intro overlay (SNOW-314).

Covers the three pure-DOM / localStorage paths that do not require live
MapLibre tiles or a loaded choropleth:

(a) Dismissing #home-intro sets the localStorage flag and hides the overlay.
(b) Reloading the page with the flag set keeps the overlay hidden.
(c) Visiting /#about re-opens the overlay regardless of the persisted state.

These tests navigate to ``/`` (the map-as-homepage).  The map canvas will not
render (MapLibre tiles are unavailable in headless CI), but home_intro.js runs
synchronously on DOMContentLoaded — before any basemap fetch — so the overlay
behaviour is fully testable without tiles.

Paths that genuinely require a loaded choropleth (region-select → ribbon swap,
ribbon-click → scrubber move) are excluded; they need a full map+tile stack and
are verified by manual testing against the running dev server.

Note: the ``live_server`` fixture requires ``transactional_db`` which flushes
the DB before each test.  We do NOT request ``_load_test_data`` here because
``/`` renders without bulletin data (the view degrades gracefully).
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

_STORAGE_KEY = "snowdesk.home.intro"
_DISMISSED_VALUE = "dismissed"


def _navigate_home(page: Page, live_server_url: str, *, hash_: str = "") -> None:
    """Navigate to / (with optional hash) and wait for DOMContentLoaded."""
    page.goto(f"{live_server_url}/{hash_}")
    page.wait_for_load_state("domcontentloaded")


def _overlay_is_hidden(page: Page) -> bool:
    """Return True when #home-intro carries the ``hidden`` attribute."""
    return cast(
        bool,
        page.evaluate(
            "() => document.getElementById('home-intro')?.hasAttribute('hidden') ?? true"
        ),
    )


def _get_storage_value(page: Page) -> str | None:
    """Return the localStorage value for the intro storage key, or None."""
    key = _STORAGE_KEY
    return cast(
        "str | None",
        page.evaluate(
            "(key) => { try { return localStorage.getItem(key); } catch(_) { return null; } }",
            key,
        ),
    )


def _clear_storage(page: Page) -> None:
    """Remove the intro storage key so tests start from a clean state."""
    key = _STORAGE_KEY
    page.evaluate(
        "(key) => { try { localStorage.removeItem(key); } catch(_) {} }",
        key,
    )


def _dismiss_intro(page: Page) -> None:
    """Trigger the intro dismiss button's click handler.

    Dispatches the ``click`` event straight to the button rather than doing a
    physical mouse click. The homepage is the full interactive map, and in
    headless CI the map canvas / site chrome can transiently intercept the
    click point while tiles settle, making a pixel-level click flaky (it passes
    reliably locally, where the button is consistently the top element). The
    unit under test here is home_intro.js's dismiss handler, not the browser's
    hit-testing, so a direct event dispatch is the deterministic way to fire it.
    """
    page.dispatch_event("#home-intro-dismiss", "click")


def test_dismiss_hides_overlay_and_persists_to_storage(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Clicking the dismiss button hides #home-intro and writes the storage flag.

    (a) from the review spec: dismissing the intro sets the localStorage flag
    and the overlay becomes hidden.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    _clear_storage(page)

    # Overlay should be visible on first visit.
    assert not _overlay_is_hidden(page), (
        "#home-intro should be visible on a fresh visit"
    )

    # Click the dismiss button.
    _dismiss_intro(page)
    page.wait_for_timeout(100)  # let the synchronous JS settle

    assert _overlay_is_hidden(page), "#home-intro should be hidden after dismiss"
    assert _get_storage_value(page) == _DISMISSED_VALUE, (
        f"localStorage['{_STORAGE_KEY}'] should be '{_DISMISSED_VALUE}' after dismiss"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_reload_keeps_overlay_hidden_when_dismissed(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Reloading the page keeps the overlay hidden when the storage flag is set.

    (b) from the review spec: after a dismiss, a fresh page load stays dismissed.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # First visit: dismiss.
    _navigate_home(page, live_server.url)
    _clear_storage(page)
    _dismiss_intro(page)
    page.wait_for_timeout(100)

    # Reload — home_intro.js should read the flag and hide immediately.
    page.reload()
    page.wait_for_load_state("domcontentloaded")

    assert _overlay_is_hidden(page), (
        "#home-intro should stay hidden after reload when flag is set"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


def test_hash_about_reopens_dismissed_overlay(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Visiting /#about re-opens the overlay even after it has been dismissed.

    (c) from the review spec: the /#about hash forces the overlay open
    regardless of the persisted localStorage state.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Set up a dismissed state first.
    _navigate_home(page, live_server.url)
    _clear_storage(page)
    _dismiss_intro(page)
    page.wait_for_timeout(100)
    assert _overlay_is_hidden(page), (
        "Precondition: overlay should be hidden after dismiss"
    )

    # Navigate to /#about — should reopen the overlay.
    _navigate_home(page, live_server.url, hash_="#about")

    assert not _overlay_is_hidden(page), (
        "#home-intro should be visible when navigating to /#about, "
        "regardless of the persisted dismissed state"
    )
    assert page_errors == [], f"JS errors: {page_errors}"
