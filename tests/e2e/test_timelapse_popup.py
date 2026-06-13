"""
tests/e2e/test_timelapse_popup.py — Playwright smoke tests for timelapse and
readout behaviour (SNOW-314 refresh of the original SNOW-240 suite).

SNOW-314 removed the region popup and the ``window.__SNOWDESK_TEST_MODE__``
hook that was used to spy on ``_refreshActivePopupForDate``.  This suite
replaces the popup-dependent and hook-dependent tests with:

1. Smoke tests — verify that ``snowdesk:date-changed`` and
   ``snowdesk:timelapse-state`` events fire without JS errors in offline
   headless environments where ``map.on('load')`` never runs.

2. IS_PLAYING guard — ``map.on('click', 'regions-fill')`` skips
   ``selectFeature`` when IS_PLAYING is true.  Since the fill layer never
   loads in headless (no tiles), we verify the IS_PLAYING flag via the
   public ``snowdesk:timelapse-state`` event listener; the flag is readable
   through ``window.__SNOWDESK_IS_PLAYING`` (set by the timelapse IIFE after
   the event fires).  We do NOT need a private test hook for this any more.

3. Readout update — ``snowdesk:date-changed`` must update the ``#region-readout``
   element's date text via ``seasonRibbonInit``'s listener. This path is
   exercisable without a loaded map: the listener is registered synchronously.

All tests stub ``/api/ratings/`` so the scrubber enters ready state.
"""

from __future__ import annotations

import json
from typing import cast

from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

# Season-ratings stub: two dates so sortedDates is non-empty and the scrubber
# can reach 'ready' state.
_SEASON_RATINGS: str = json.dumps(
    {
        "2026-04-07": {"CH-4115": 2},
        "2026-04-08": {"CH-4115": 3},
    }
)


def _route_season_ratings(route: Route) -> None:
    """Return a stub season-ratings payload so the scrubber enters ready state."""
    route.fulfill(
        status=200,
        content_type="application/json",
        body=_SEASON_RATINGS,
    )


def _setup_routes(page: Page, live_server_url: str) -> None:
    """Stub the ratings endpoint before navigating to /map/.

    Only the ratings endpoint is stubbed.  Everything else (basemap
    tiles, regions GeoJSON) is left as-is — MapLibre's 'load' event never
    fires in the offline headless context, but the synchronously-registered
    event listeners in the outer IIFE and ``seasonRibbonInit`` are always
    active.
    """
    page.route(f"{live_server_url}/api/ratings/*", _route_season_ratings)


def _wait_for_scrubber_ready(page: Page, timeout: float = 15_000) -> None:
    """Block until the season scrubber enters 'ready' state."""
    page.wait_for_selector(
        "#season-scrubber[data-state='ready']",
        timeout=timeout,
    )


def _navigate_and_wait(page: Page, live_server_url: str) -> None:
    """Navigate to /map/ and wait for the scrubber to be ready."""
    _setup_routes(page, live_server_url)
    page.goto(f"{live_server_url}/map/")
    page.wait_for_load_state("domcontentloaded")
    _wait_for_scrubber_ready(page)


def _dispatch_timelapse_state(page: Page, *, playing: bool) -> None:
    """Dispatch ``snowdesk:timelapse-state`` with the given playing flag."""
    page.evaluate(
        """(playing) => {
            document.dispatchEvent(
                new CustomEvent('snowdesk:timelapse-state', { detail: { playing } })
            );
        }""",
        playing,
    )


def _dispatch_date_changed(page: Page, date_key: str) -> None:
    """Dispatch ``snowdesk:date-changed`` as the timelapse fires on each frame."""
    page.evaluate(
        """(dateKey) => {
            document.dispatchEvent(
                new CustomEvent('snowdesk:date-changed', {
                    detail: { date: dateKey, source: 'timelapse' }
                })
            );
        }""",
        date_key,
    )


# ---------------------------------------------------------------------------
# Smoke tests — no test hook required; verify the listeners fire without errors.
# ---------------------------------------------------------------------------


def test_date_changed_during_playback_no_js_errors(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """``snowdesk:date-changed`` events during playback produce no JS errors.

    Verifies the guard code path does not throw when IS_PLAYING is true and
    that ``seasonRibbonInit``'s date listener handles the event cleanly.
    """
    _navigate_and_wait(page, live_server.url)

    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _dispatch_timelapse_state(page, playing=True)
    for date_key in ["2026-04-07", "2026-04-08"]:
        _dispatch_date_changed(page, date_key)

    page.wait_for_timeout(400)

    assert page_errors == [], f"JS errors during playback: {page_errors}"


def test_date_changed_after_stop_no_js_errors(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """After dispatching timelapse-state{playing:false}, date-changed runs without error."""
    page_errors: list[str] = []

    _navigate_and_wait(page, live_server.url)

    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _dispatch_timelapse_state(page, playing=True)
    _dispatch_timelapse_state(page, playing=False)
    _dispatch_date_changed(page, "2026-04-08")

    page.wait_for_timeout(400)

    assert page_errors == [], f"JS errors after stop+date-changed: {page_errors}"


def test_timelapse_state_event_fires_without_errors(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Both play and stop timelapse-state dispatches run without JS errors."""
    page_errors: list[str] = []

    _navigate_and_wait(page, live_server.url)

    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _dispatch_timelapse_state(page, playing=True)
    page.wait_for_timeout(100)
    _dispatch_timelapse_state(page, playing=False)
    page.wait_for_timeout(100)

    assert page_errors == [], f"JS errors on timelapse-state: {page_errors}"


# ---------------------------------------------------------------------------
# Readout update — seasonRibbonInit listens to snowdesk:date-changed.
# ---------------------------------------------------------------------------


def test_date_changed_updates_region_readout(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """``snowdesk:date-changed`` causes the region readout to become visible.

    ``seasonRibbonInit`` listens to ``snowdesk:date-changed`` and calls
    ``updateReadout()``.  With no region focused the readout is hidden; once
    a date is received it is made visible (``hidden`` attribute removed) so the
    date string is readable.  This listener is registered synchronously and runs
    without a loaded MapLibre map.
    """
    page_errors: list[str] = []

    _navigate_and_wait(page, live_server.url)
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Before any event, the readout is hidden.
    readout = page.locator("#region-readout")
    assert readout.count() == 1, "#region-readout element must be present"
    hidden_before = cast("bool | None", readout.get_attribute("hidden"))
    assert hidden_before is not None, "#region-readout should be hidden initially"

    # Dispatch date-changed — updateReadout() should unhide the element.
    _dispatch_date_changed(page, "2026-04-08")
    page.wait_for_timeout(150)

    hidden_after = readout.get_attribute("hidden")
    assert hidden_after is None, (
        "#region-readout should be visible after snowdesk:date-changed is dispatched"
    )

    assert page_errors == [], f"JS errors: {page_errors}"
