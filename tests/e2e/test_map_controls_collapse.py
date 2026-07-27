"""
tests/e2e/test_map_controls_collapse.py — Playwright tests for the
collapsible bottom-right map control stack (SNOW-536).

The state machine itself is covered by Vitest against jsdom
(``tests/js/test_map_controls_collapse.js``). What jsdom cannot see is the
part that only exists in a real engine, and that is what this file covers:

1. **The group actually collapses.** jsdom does not compute layout, so the
   CSS height calculation — ``n * --map-control-lg + (n - 1) * 8px``, driven
   off a child count published by the module — is unverifiable there. The
   height being *computed* rather than measured is the whole reason the
   feature works at all (measuring reads ``offsetHeight`` off an
   already-clipped box, which returns 0), so it is worth a real assertion.
2. **The toggle never moves.** The stack is bottom-anchored with the toggle
   as its last child specifically so it stays put while the group grows and
   shrinks above it — a control that slid away under the finger pressing it
   would be the bug this layout exists to avoid.
3. **The preference survives a reload**, i.e. the localStorage round trip
   runs against a real page load and not a re-imported module.
4. **The help tour force-expands a collapsed group.** Several tour steps
   target controls inside the group, and a collapsed control still matches
   ``document.querySelector`` — so ``map_help.js``'s absent-target filter
   does not drop the step, and without the force-expand the highlight ring
   would be positioned against a box clipped to zero height.

Like ``test_map_bottom_bar.py`` these run without a loaded MapLibre style:
the stack is server-rendered and positioned by CSS, and
``map_controls_collapse.js`` only reads ``#map-controls-br``'s DOM. The
ratings endpoint is stubbed so the scrubber reaches its ready state, which
is the signal that the page has finished its boot work.

Transitions are 300ms (``static/css/map.css``), so every assertion that
depends on the animation having finished waits on the settled value rather
than sleeping a fixed amount.
"""

from __future__ import annotations

import json

from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

_STORAGE_KEY = "snowdesk.map.controls.expanded"

# Two dates so sortedDates is non-empty and the scrubber reaches 'ready'
# (mirrors test_map_bottom_bar.py / test_timelapse_popup.py).
_SEASON_RATINGS: str = json.dumps(
    {
        "2026-04-07": {"CH-4115": 2},
        "2026-04-08": {"CH-4115": 3},
    }
)


def _route_season_ratings(route: Route) -> None:
    """Return a stub season-ratings payload so the scrubber enters ready state."""
    route.fulfill(status=200, content_type="application/json", body=_SEASON_RATINGS)


def _navigate_and_wait(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the scrubber to signal a finished boot."""
    page.route(f"{live_server_url}/api/ratings/*", _route_season_ratings)
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#season-scrubber[data-state='ready']", timeout=15_000)


def _group_height(page: Page) -> float:
    """Current rendered height of the collapsible group, in CSS px."""
    return float(
        page.evaluate(
            "() => document.getElementById('map-controls-collapsible')"
            ".getBoundingClientRect().height"
        )
    )


def _toggle_top(page: Page) -> float:
    """Viewport-relative top edge of the collapse toggle, in CSS px."""
    return float(
        page.evaluate(
            "() => document.getElementById('map-controls-toggle')"
            ".getBoundingClientRect().top"
        )
    )


def _wait_for_expanded(page: Page, expanded: bool) -> None:
    """Block until data-expanded settles AND the height transition has finished.

    The attribute flips synchronously on click but the height eases over
    300ms, so asserting on geometry straight after the attribute samples
    mid-flight.

    Waiting for "height > 0" is not enough on the way open — that is true a
    frame into the transition, and an assertion on the final height then sees
    a partial value (this test first failed at 24.9px against an expected
    184px). So require the height to be *stable* across consecutive polls —
    Playwright polls wait_for_function on animation frames — and only then
    check it against the direction we asked for.
    """
    page.wait_for_selector(
        f"#map-controls-br[data-expanded='{str(expanded).lower()}']", timeout=5_000
    )
    # Reset the sampler so a value left over from an earlier wait can't be
    # mistaken for a settled reading on the first poll.
    page.evaluate("() => { delete window.__lastGroupHeight; }")
    page.wait_for_function(
        """(wantOpen) => {
            const h = document.getElementById('map-controls-collapsible')
                .getBoundingClientRect().height;
            const prev = window.__lastGroupHeight;
            window.__lastGroupHeight = h;
            // Still moving between frames — keep polling.
            if (prev === undefined || Math.abs(h - prev) > 0.5) return false;
            return wantOpen ? h > 1 : h < 1;
        }""",
        arg=expanded,
        timeout=5_000,
    )


def test_collapsing_hides_the_group_without_moving_the_toggle(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Collapsing clears the group's height and leaves the toggle where it was.

    The stack is bottom-anchored with the toggle last, so it grows and
    shrinks upward: the toggle must not move under the finger pressing it.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    _navigate_and_wait(page, live_server.url)

    # Default is expanded, and the computed height is real — this is the
    # assertion jsdom cannot make.
    _wait_for_expanded(page, True)
    expanded_height = _group_height(page)
    assert expanded_height > 0, (
        "the collapsible group should have a computed open height; "
        f"got {expanded_height}px"
    )

    toggle_top_before = _toggle_top(page)

    page.locator("#map-controls-toggle").click()
    _wait_for_expanded(page, False)

    assert _group_height(page) < 1, (
        f"the group should collapse to zero height; got {_group_height(page)}px"
    )
    # The whole point of anchoring the toggle last: it stays put.
    assert abs(_toggle_top(page) - toggle_top_before) <= 1, (
        "the toggle must not move when the group collapses; "
        f"moved from {toggle_top_before}px to {_toggle_top(page)}px"
    )

    # Re-expanding restores the same computed height.
    page.locator("#map-controls-toggle").click()
    _wait_for_expanded(page, True)
    assert abs(_group_height(page) - expanded_height) <= 1, (
        "re-expanding should restore the original height; "
        f"{_group_height(page)}px vs {expanded_height}px"
    )

    assert page_errors == [], f"JS errors: {page_errors}"


def test_collapsed_preference_survives_a_reload(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Collapsing persists to localStorage and is restored on the next load."""
    _navigate_and_wait(page, live_server.url)
    _wait_for_expanded(page, True)

    page.locator("#map-controls-toggle").click()
    _wait_for_expanded(page, False)

    stored = page.evaluate(f"() => localStorage.getItem({_STORAGE_KEY!r})")
    assert stored == "false", (
        f"expected the collapsed preference to persist; got {stored!r}"
    )

    _navigate_and_wait(page, live_server.url)
    _wait_for_expanded(page, False)
    assert _group_height(page) < 1, (
        "a stored collapsed preference should be applied on the next load"
    )
    assert (
        page.locator("#map-controls-toggle").get_attribute("aria-label")
        == "Show map controls"
    ), "the toggle label should describe what the next tap does"


def test_help_tour_force_expands_a_collapsed_group(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Opening the tour expands a collapsed group, and closing it restores.

    Tour steps target controls inside the group. A collapsed control still
    matches ``document.querySelector``, so map_help.js's absent-target filter
    keeps the step and the ring would otherwise land on a clipped box. The
    user's stored preference must not be overwritten by the tour.
    """
    _navigate_and_wait(page, live_server.url)
    _wait_for_expanded(page, True)

    page.locator("#map-controls-toggle").click()
    _wait_for_expanded(page, False)

    # The "?" roundel lives inside the collapsed group, so drive the tour the
    # way an auto-start would rather than clicking a clipped control.
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:map-help-open'))"
    )
    _wait_for_expanded(page, True)
    assert page.evaluate(f"() => localStorage.getItem({_STORAGE_KEY!r})") == "false", (
        "the tour must not overwrite the user's stored preference"
    )

    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'))"
    )
    _wait_for_expanded(page, False)
    assert _group_height(page) < 1, (
        "closing the tour should hand the group back to the stored preference"
    )
