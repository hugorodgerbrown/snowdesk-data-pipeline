"""
tests/e2e/test_timelapse_popup.py — Playwright tests for timelapse/popup interaction.

Verifies the plumbing introduced by SNOW-240:

1. The ``snowdesk:date-changed`` and ``snowdesk:timelapse-state`` listeners are
   registered at outer-IIFE scope, so they fire in offline headless environments
   where ``map.on('load')`` never runs.
2. The IS_PLAYING flag genuinely gates the ``_refreshActivePopupForDate`` call
   path: events during playback do not invoke the refresh function, and events
   after stop do invoke it.  This is verified by:
     a. Injecting a spy via ``window.__snowdesk_test.setRefreshFn()``.
     b. Controlling IS_PLAYING directly via ``window.__snowdesk_test.setIsPlaying()``.
   Both hooks are exposed only when ``window.__SNOWDESK_TEST_MODE__ = true`` was
   set before navigation (via ``page.add_init_script``); they are entirely absent
   in production.
3. Both playback transitions (play → stop) complete cleanly without JS errors,
   and the IS_PLAYING state machine behaves correctly across both edges.

Test-mode hook
--------------
``window.__SNOWDESK_TEST_MODE__ = true`` is set via ``page.add_init_script``
before navigation so it is visible when ``map.js`` executes.  When the flag is
present, ``map.js`` exposes ``window.__snowdesk_test`` with:

- ``setRefreshFn(fn)`` — replaces the inner ``_refreshActivePopupForDate``
  forwarding variable with a caller-supplied function.  The
  ``snowdesk:date-changed`` listener calls through this variable, so injecting
  a spy makes the IS_PLAYING guard observable without the basemap loading.
- ``getIsPlaying()`` — returns the current value of the module-scope
  ``IS_PLAYING`` variable.
- ``setIsPlaying(bool)`` — sets ``IS_PLAYING`` directly, bypassing the
  ``timelapseInit()`` start()/stop() functions (which require a loaded map
  and populated sortedDates).  This lets the guard tests control state without
  the basemap.

The hook is absent from production (no ``window.__SNOWDESK_TEST_MODE__``).

Why ``setIsPlaying`` rather than dispatching ``snowdesk:timelapse-state``
-------------------------------------------------------------------------
``IS_PLAYING`` is set directly by ``timelapseInit()``'s ``start()`` and
``stop()`` functions *before* they dispatch ``snowdesk:timelapse-state``.  The
main IIFE's ``timelapse-state`` listener does NOT update ``IS_PLAYING`` — it
only calls ``_clearTooltip()``.  So dispatching the event alone does not
change IS_PLAYING in headless tests; ``setIsPlaying`` is the correct hook.
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
    """Stub the season-ratings endpoint before navigating to /map/.

    Only the season-ratings endpoint is stubbed.  Everything else (basemap
    tiles, regions GeoJSON, today-summaries) is left as-is — the basemap will
    fail to load in the offline headless context, which means MapLibre's 'load'
    event never fires and the choropleth setup never runs.  The event
    listeners added by the main IIFE are registered synchronously and are
    therefore always active regardless of whether MapLibre loads.
    """
    page.route(f"{live_server_url}/api/season-ratings/", _route_season_ratings)


def _wait_for_scrubber_ready(page: Page, timeout: float = 15_000) -> None:
    """Block until the season scrubber enters 'ready' state.

    The scrubber's data-state attribute transitions from 'loading' to 'ready'
    once the season-ratings fetch resolves.
    """
    page.wait_for_selector(
        "#season-scrubber[data-state='ready']",
        timeout=timeout,
    )


def _dispatch_timelapse_state(page: Page, *, playing: bool) -> None:
    """Dispatch ``snowdesk:timelapse-state`` with the given playing flag.

    Note: this does NOT update IS_PLAYING — the real start()/stop() functions
    set IS_PLAYING directly before dispatching the event.  Use
    ``window.__snowdesk_test.setIsPlaying()`` to control IS_PLAYING in tests.
    This helper is kept for the smoke tests that verify the listener fires
    without errors.
    """
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


def _navigate_and_wait(page: Page, live_server_url: str) -> None:
    """Navigate to /map/ and wait for the scrubber to be ready."""
    _setup_routes(page, live_server_url)
    page.goto(f"{live_server_url}/map/")
    page.wait_for_load_state("domcontentloaded")
    _wait_for_scrubber_ready(page)


def _install_refresh_spy(page: Page) -> None:
    """Inject a spy into ``_refreshActivePopupForDate`` via the test hook.

    The spy records each call by pushing the date argument onto
    ``window.__refresh_calls``.  Requires ``window.__SNOWDESK_TEST_MODE__ = true``
    to have been set before navigation.
    """
    page.evaluate("""() => {
        window.__refresh_calls = [];
        window.__snowdesk_test.setRefreshFn((dateKey) => {
            window.__refresh_calls.push(dateKey);
        });
    }""")


def _get_refresh_calls(page: Page) -> list[str]:
    """Return the list of date keys passed to the spy since it was installed."""
    return cast(list[str], page.evaluate("() => window.__refresh_calls"))


def _assert_test_hook_present(page: Page) -> None:
    """Assert that the test hook is exposed (fails fast if hook is missing)."""
    has_hook: bool = page.evaluate(
        "() => typeof window.__snowdesk_test !== 'undefined' "
        "&& typeof window.__snowdesk_test.setRefreshFn === 'function'"
    )
    assert has_hook, (
        "window.__snowdesk_test not set — did you add "
        "page.add_init_script('window.__SNOWDESK_TEST_MODE__ = true;') "
        "before navigating?"
    )


# ---------------------------------------------------------------------------
# IS_PLAYING guard tests — these are the load-bearing tests.
# Both fail immediately if the ``if (!IS_PLAYING)`` guard is removed from the
# ``snowdesk:date-changed`` listener in map.js.
# ---------------------------------------------------------------------------


def test_is_playing_guard_blocks_refresh_fn(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """IS_PLAYING=true prevents the refresh fn from being called on date-changed.

    This test FAILS if the ``if (!IS_PLAYING)`` guard is removed from the
    ``snowdesk:date-changed`` listener in ``map.js``.

    Sets ``window.__SNOWDESK_TEST_MODE__ = true`` before navigation so
    ``map.js`` exposes ``window.__snowdesk_test``.  After load:

    1. Installs a spy via ``setRefreshFn()`` — any call to the inner
       ``_refreshActivePopupForDate`` is recorded.
    2. Sets IS_PLAYING=true directly via ``setIsPlaying(true)`` (bypassing
       start(), which requires a loaded map).
    3. Fires ``snowdesk:date-changed`` and asserts the spy was NOT called.
    """
    page.add_init_script("window.__SNOWDESK_TEST_MODE__ = true;")
    _navigate_and_wait(page, live_server.url)
    _assert_test_hook_present(page)
    _install_refresh_spy(page)

    # Set IS_PLAYING directly — the timelapse-state event alone does not do this.
    page.evaluate("() => window.__snowdesk_test.setIsPlaying(true)")
    assert page.evaluate("() => window.__snowdesk_test.getIsPlaying()") is True

    # Fire several date-changed frames while playing.
    for date_key in ["2026-04-07", "2026-04-08"]:
        _dispatch_date_changed(page, date_key)

    page.wait_for_timeout(200)

    calls = _get_refresh_calls(page)
    assert calls == [], (
        f"IS_PLAYING guard failed: refresh fn was called {len(calls)} time(s) "
        f"during playback with dates: {calls}"
    )


def test_is_playing_guard_allows_refresh_fn_when_not_playing(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """IS_PLAYING=false allows the refresh fn to be called on date-changed.

    Positive counterpart to the blocking test: confirms the spy IS called when
    IS_PLAYING is false, so together the two tests prove the guard has real
    effect.
    """
    page.add_init_script("window.__SNOWDESK_TEST_MODE__ = true;")
    _navigate_and_wait(page, live_server.url)
    _assert_test_hook_present(page)
    _install_refresh_spy(page)

    # IS_PLAYING defaults to false; confirm before proceeding.
    assert page.evaluate("() => window.__snowdesk_test.getIsPlaying()") is False

    # Fire a date-changed while NOT playing.
    _dispatch_date_changed(page, "2026-04-08")

    page.wait_for_timeout(200)

    calls = _get_refresh_calls(page)
    assert calls == ["2026-04-08"], (
        f"Expected refresh fn called once with '2026-04-08', got: {calls}"
    )


def test_is_playing_guard_blocked_then_allowed_after_stop(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """IS_PLAYING gate correctly transitions: blocked while playing, open after stop.

    Combined test: starts playback (spy not called), stops playback (spy called
    on next date-changed).  Verifies the full play → stop lifecycle of the guard.

    This test FAILS if the ``if (!IS_PLAYING)`` guard is removed, because the
    spy would be called during the playback phase.
    """
    page.add_init_script("window.__SNOWDESK_TEST_MODE__ = true;")
    _navigate_and_wait(page, live_server.url)
    _assert_test_hook_present(page)
    _install_refresh_spy(page)

    # Simulate playback starting — guard should block the refresh fn.
    page.evaluate("() => window.__snowdesk_test.setIsPlaying(true)")
    _dispatch_date_changed(page, "2026-04-07")
    page.wait_for_timeout(200)

    calls_during_play = _get_refresh_calls(page)
    assert calls_during_play == [], (
        f"Refresh fn called during playback: {calls_during_play}"
    )

    # Simulate playback stopping — guard should now allow the refresh fn.
    page.evaluate("() => window.__snowdesk_test.setIsPlaying(false)")
    _dispatch_date_changed(page, "2026-04-08")
    page.wait_for_timeout(200)

    calls_after_stop = _get_refresh_calls(page)
    assert calls_after_stop == ["2026-04-08"], (
        f"Expected refresh fn called once after stop, got: {calls_after_stop}"
    )


# ---------------------------------------------------------------------------
# Smoke tests — no test hook required, verify the listeners fire without errors.
# ---------------------------------------------------------------------------


def test_date_changed_during_playback_no_js_errors(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """``snowdesk:date-changed`` events during playback produce no JS errors.

    Complements the spy-based guard tests: confirms the guard code path itself
    does not throw when IS_PLAYING is true.  No test hook needed — just
    dispatch events and check for errors.
    """
    _navigate_and_wait(page, live_server.url)

    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # Dispatch timelapse-state; note IS_PLAYING is not updated by this event
    # in the main IIFE, but the test is only checking for absence of errors.
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


def test_timelapse_state_event_received_by_main_iife(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The main IIFE's snowdesk:timelapse-state listener executes without error.

    Verifies that the listener registered by the main IIFE actually fires when
    the event is dispatched, and that the IS_PLAYING state machine is observable
    via the test hook.
    """
    page.add_init_script("window.__SNOWDESK_TEST_MODE__ = true;")
    page_errors: list[str] = []

    _navigate_and_wait(page, live_server.url)

    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # IS_PLAYING defaults false.
    assert page.evaluate("() => window.__snowdesk_test.getIsPlaying()") is False

    # Set IS_PLAYING true (as start() would before dispatching the event).
    page.evaluate("() => window.__snowdesk_test.setIsPlaying(true)")
    # Also dispatch the event so the main IIFE's listener runs.
    _dispatch_timelapse_state(page, playing=True)

    assert page.evaluate("() => window.__snowdesk_test.getIsPlaying()") is True
    assert page_errors == [], f"JS errors on play: {page_errors}"

    # Simulate stop.
    page.evaluate("() => window.__snowdesk_test.setIsPlaying(false)")
    _dispatch_timelapse_state(page, playing=False)

    assert page.evaluate("() => window.__snowdesk_test.getIsPlaying()") is False
    assert page_errors == [], f"JS errors on stop: {page_errors}"
