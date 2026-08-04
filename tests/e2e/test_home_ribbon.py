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
