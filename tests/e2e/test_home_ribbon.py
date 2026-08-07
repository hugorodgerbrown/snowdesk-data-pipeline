"""
tests/e2e/test_home_ribbon.py — Playwright tests for the SNOW-314 scrubber ribbon.

Guards two regressions that pure DOM-presence assertions (Django test client)
cannot catch, and which do NOT require live MapLibre tiles:

(a) The scrubber ribbon cells (``.scrubber-ribbon-cell``) must paint with a
    non-zero width. These are JS-injected ``<span>`` elements inside the
    ``.scrubber-ribbon`` fill div; they are painted by ``seasonRibbonInit``
    once the ``/api/ratings/`` cache resolves. A regression that broke the
    flex sizing would collapse every cell to zero width while they remain in
    the DOM — invisible but present, so a DOM-count test would still pass.

(b) Scrubbing to a date on the homepage must keep the visitor on ``/``.
    ``commitDate`` in map.js rewrites the URL via ``replaceState``; a
    hardcoded ``/map/`` there silently bounced homepage visitors to ``/map/``
    on every scrub/ribbon click.

(c) SNOW-642: the ribbon header must persist with an empty state when no
    region is selected, rather than vanishing. The readout and both of its
    sibling controls used to disappear together, which no DOM-presence test
    would catch either — the elements stayed in the DOM, hidden.

The ribbon cells are JS-injected after the ``/api/ratings/`` fetch resolves.
These tests request ``_load_test_data`` so CH-4115 has ``RegionDayRating``
rows and the default-region focus paints cells immediately after the cache
resolves — no MapLibre tile fetch needed.
"""

from __future__ import annotations

from typing import cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for DOMContentLoaded."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.usefixtures("_load_test_data")
def test_ribbon_cells_render_with_nonzero_width(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Each scrubber-ribbon cell paints with a non-zero width.

    ``seasonRibbonInit`` injects ``.scrubber-ribbon-cell`` spans into the
    ``.scrubber-ribbon`` fill div after the ``/api/ratings/`` cache resolves.
    A flex-sizing regression would leave them zero-width while still in the
    DOM — invisible but DOM-present. This test catches that by measuring the
    bounding box of the first injected cell.

    The homepage defaults to CH-4115, so cells paint as soon as the cache
    resolves when test_data includes CH-4115 RegionDayRating rows.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)

    # Wait for the scrubber to finish loading (ratings cache resolved + painted).
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    cell = page.locator(".scrubber-ribbon-cell").first
    cell.wait_for(state="attached")
    box = cell.bounding_box()

    assert box is not None, "scrubber-ribbon cell should have a bounding box"
    assert box["width"] > 0, (
        f"scrubber-ribbon cell width should be > 0 (the ribbon is invisible otherwise); "
        f"got {box['width']}"
    )
    assert box["height"] > 0, (
        f"scrubber-ribbon cell height should be > 0; got {box['height']}"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


@pytest.mark.usefixtures("_load_test_data")
def test_scrub_keeps_url_on_home(
    live_server: LiveServer,
    page: Page,
) -> None:
    """Scrubbing to a date on the homepage keeps the visitor on ``/``.

    Guards the hardcoded ``/map/`` in ``commitDate``: committing a date on the
    homepage must leave ``location.pathname`` as ``/`` (not bounce to ``/map/``)
    and add a ``?d=`` date param.

    Drives the scrubber track directly with a press-and-release near its left
    edge, which is what ``commitDate`` is actually reached by. This used to
    dispatch ``snowdesk:scrub-to`` instead — an event no ribbon cell had
    dispatched since the cells moved into the scrubber's own track, so the
    test was exercising a listener that existed only for it. SNOW-615 deleted
    the listener and pointed the test at the real control.

    The press lands a few pixels in from the left edge, so the committed date
    is near the season start and cannot be today (which is off-season) — that
    is what makes the ``?d=`` assertion meaningful.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    track = page.locator(".season-scrubber .season-scrubber-track")
    box = track.bounding_box()
    assert box is not None, "the scrubber track has no layout box"
    page.mouse.move(box["x"] + 4, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(150)

    pathname = cast("str", page.evaluate("() => location.pathname"))
    search = cast("str", page.evaluate("() => location.search"))

    assert pathname == "/", (
        f"scrubbing on the homepage must keep the visitor on '/', "
        f"not rewrite to '{pathname}'"
    )
    assert "d=" in search, (
        f"committing a (non-today) date should add a ?d= param; got '{search}'"
    )
    assert page_errors == [], f"JS errors: {page_errors}"


@pytest.mark.usefixtures("_load_test_data")
def test_ribbon_header_persists_with_an_empty_state(
    live_server: LiveServer,
    page: Page,
) -> None:
    """SNOW-642: nothing selected shows an empty state, not an empty row.

    ``#region-readout`` carried ``hidden`` until a region was focused, and
    both controls beside it are siblings keyed off
    ``#region-readout.has-region`` — so with nothing selected all three
    disappeared at once and ``.ribbon-header`` collapsed to a blank strip.
    That read as the ribbon failing to load rather than as "nothing is
    selected".

    The homepage pre-selects CH-4115, so the unfocused state is reached by
    deselecting — which is also the reverse guard the ticket asks for: it
    must return to the empty state rather than leave a stale region name.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    readout = page.locator("#region-readout")
    action = page.locator("#region-readout-action")
    download = page.locator("#map-download-control")

    # Pre-selected: the focused state, and the baseline the empty state has
    # to be visibly different from.
    readout.wait_for(state="visible")
    assert "has-region" in (readout.get_attribute("class") or "")

    # Deselect — a region-selected event with no region is how the map
    # clears focus.
    page.evaluate(
        """() => document.dispatchEvent(
            new CustomEvent('snowdesk:region-selected', { detail: {} }),
        )"""
    )
    page.wait_for_timeout(150)

    # The header keeps all three of its parts.
    assert readout.is_visible(), "the readout must persist with nothing selected"
    assert action.is_visible(), "the view-bulletin roundel must persist"
    assert download.is_visible(), "the download roundel must persist"

    # And says what is true, with no stale region name left behind.
    assert readout.inner_text().strip() == "No region selected"
    assert "has-region" not in (readout.get_attribute("class") or "")

    # Both controls are present but inert.
    assert action.get_attribute("aria-disabled") == "true"
    assert action.get_attribute("href") is None, (
        "a disabled view-bulletin roundel must not carry a live href"
    )
    assert action.get_attribute("tabindex") == "-1", (
        "a disabled roundel must be out of the tab order"
    )
    assert download.get_attribute("data-download-state") == "no-region"

    assert page_errors == [], f"JS errors: {page_errors}"


@pytest.mark.usefixtures("_load_test_data")
def test_selecting_a_region_enables_both_ribbon_controls(
    live_server: LiveServer,
    page: Page,
) -> None:
    """SNOW-642: the empty state is a state, not a dead end.

    Selecting a region must name it and make both roundels actionable —
    the half of the contract that a permanently-disabled pair would also
    satisfy if only the empty state were asserted.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    # Start from the empty state so the transition is the thing measured.
    page.evaluate(
        """() => document.dispatchEvent(
            new CustomEvent('snowdesk:region-selected', { detail: {} }),
        )"""
    )
    page.wait_for_timeout(150)

    # region_name and region_slug are both load-bearing: updateReadout only
    # sets .has-region on a truthy date + id + name, and only builds the
    # bulletin href when there is a slug.
    page.evaluate(
        """() => document.dispatchEvent(
            new CustomEvent('snowdesk:region-selected', {
                detail: {
                    region_id: 'CH-4115',
                    region_name: 'Martigny-Verbier',
                    region_slug: 'martigny-verbier',
                },
            }),
        )"""
    )
    page.wait_for_timeout(150)

    readout = page.locator("#region-readout")
    action = page.locator("#region-readout-action")

    assert "Martigny-Verbier" in readout.inner_text()
    assert "No region selected" not in readout.inner_text()
    assert "has-region" in (readout.get_attribute("class") or "")

    assert action.get_attribute("aria-disabled") is None
    assert action.get_attribute("tabindex") is None
    href = action.get_attribute("href") or ""
    assert href.startswith("/ch-4115/martigny-verbier/"), (
        f"the roundel should point at the dated bulletin; got '{href}'"
    )

    assert page_errors == [], f"JS errors: {page_errors}"
