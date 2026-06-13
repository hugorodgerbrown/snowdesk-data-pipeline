"""
tests/e2e/test_home_ribbon.py — Playwright tests for the season ribbon on / (SNOW-314).

Guards two regressions that pure DOM-presence assertions (Django test client)
cannot catch, and which do NOT require live MapLibre tiles:

(a) The ribbon cells must render with a non-zero width. The cells are empty
    ``<button>`` elements inside ``<li>`` wrappers; their thin-column sizing
    lives on the ``<li>`` (the flex item of ``.ribbon-track``). A regression
    that put the flex sizing on the button instead collapsed every ``<li>`` to
    zero width, leaving the whole ribbon invisible while still present in the
    DOM — so a presence-only test passed despite a blank ribbon.

(b) Clicking a ribbon day must keep the visitor on ``/``. ``commitDate`` in
    map.js rewrites the URL via ``replaceState``; a hardcoded ``/map/`` there
    silently bounced homepage visitors to ``/map/`` on every scrub/ribbon click.

The ribbon is server-rendered (so cells exist before any tile fetch) and the
scrubber/ribbon JS runs off the ``/api/ratings/`` fetch, not the basemap — so
both paths are exercisable in headless CI without a tile stack. These tests
request ``_load_test_data`` because the ribbon only has cells when CH-4115 has
``RegionDayRating`` rows.
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
    """Each season-ribbon cell renders as a visible, non-zero-width bar.

    Directly guards the flex-sizing-on-the-wrong-element regression that
    collapsed the ribbon to an invisible strip of zero-width cells.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)

    cell = page.locator("#season-ribbon .ribbon-cell").first
    cell.wait_for(state="attached")
    box = cell.bounding_box()

    assert box is not None, "ribbon cell should have a bounding box"
    assert box["width"] > 0, (
        f"ribbon cell width should be > 0 (the ribbon is invisible otherwise); "
        f"got {box['width']}"
    )
    assert box["height"] > 0, f"ribbon cell height should be > 0; got {box['height']}"
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

    We dispatch ``snowdesk:scrub-to`` directly — the exact event a ribbon-day
    click fires — rather than clicking a cell. The ribbon's interactive cells
    are only re-rendered (with click handlers) once the ``/api/ratings`` cache
    has data for the selected region, which the sparse e2e ``test_data`` fixture
    does not guarantee; the URL behaviour under test lives in ``commitDate`` and
    is reached identically via the event, so this stays deterministic.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _navigate_home(page, live_server.url)
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    # The season start is an in-season date (never today, which is off-season),
    # so committing it must produce a ?d= param.
    season_start = cast(
        "str", page.get_attribute("#season-ribbon", "data-season-start")
    )
    page.evaluate(
        "(d) => document.dispatchEvent("
        "new CustomEvent('snowdesk:scrub-to', { detail: { date: d } }))",
        season_start,
    )
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
