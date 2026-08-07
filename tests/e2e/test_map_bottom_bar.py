"""
tests/e2e/test_map_bottom_bar.py — Playwright smoke tests for the map's
bottom-bar layout rework.

Covers the invariants introduced when the scrubbed date was split out of the
top ``#region-readout`` chip and the bottom-right control cluster was unified:

1. Date split — the scrubbed date lives in ``#map-date-ribbon`` (bottom-left,
   beside the (i) toggle), and no longer inside the top ``#region-readout``
   chip, which now carries only the region name + danger swatch.
2. Stack foot — the last child of ``#map-controls-br`` sits on the same bottom
   baseline as the bottom-left (i) legend toggle. That foot was the help (?)
   roundel until SNOW-536 added the collapse toggle beneath it.
3. Legend structure — the info card splits into an "EAWS Danger Scale" section
   (with a "No rating" row) and a "Map pins" section (Resorts / Favourites /
   Observations).

These run without a loaded MapLibre style: the controls are positioned by CSS
(``static/css/map.css``) and the DOM is server-rendered, so geometry and
structure are assertable in the offline headless context. The ratings endpoint
is stubbed so the scrubber reaches 'ready' state, mirroring
``test_timelapse_popup.py``.
"""

from __future__ import annotations

import json

from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import _dismiss_home_intro

# Season-ratings stub: two dates so sortedDates is non-empty and the scrubber
# can reach 'ready' state (mirrors test_timelapse_popup.py).
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


def _navigate_and_wait(page: Page, live_server_url: str) -> None:
    """Navigate to / (the canonical map page) and wait for the scrubber to be ready."""
    page.route(f"{live_server_url}/api/ratings/*", _route_season_ratings)
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#season-scrubber[data-state='ready']", timeout=15_000)


def test_date_lives_in_ribbon_not_region_chip(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The scrubbed date renders in #map-date-ribbon, not the top region chip.

    The top #region-readout no longer carries a `.region-readout-date` child;
    the bottom #map-date-ribbon shows the day and tracks `snowdesk:date-changed`.
    """
    page_errors: list[str] = []
    _navigate_and_wait(page, live_server.url)
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # The region chip has no date element — the date moved out of it.
    assert page.locator("#region-readout .region-readout-date").count() == 0, (
        "the date was split out of #region-readout; it must not carry a date span"
    )

    # The bottom ribbon exists and reflects the scrubbed day.
    ribbon = page.locator("#map-date-ribbon")
    assert ribbon.count() == 1, "#map-date-ribbon must be present"
    page.evaluate(
        """() => document.dispatchEvent(new CustomEvent('snowdesk:date-changed',
            { detail: { date: '2026-04-08', source: 'timelapse' } }))"""
    )
    page.wait_for_timeout(150)
    date_text = ribbon.inner_text()
    assert "2026" in date_text and "8" in date_text, (
        f"#map-date-ribbon should show the scrubbed day; got {date_text!r}"
    )

    assert page_errors == [], f"JS errors: {page_errors}"


def test_collapse_toggle_is_stack_foot_aligned_with_legend_toggle(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """The stack's foot sits level with the bottom-left (i) legend toggle.

    #map-controls-br is one bottom-anchored column, and whatever sits at its
    foot shares the 60px baseline with #map-legend-toggle. SNOW-536 made the
    collapse toggle that foot — the help (?) roundel moved up into the
    collapsible group — so the baseline invariant now belongs to
    #map-controls-toggle. The toggle is deliberately the last child: the stack
    grows and shrinks upward out of it, so it never moves under the finger
    pressing it.
    """
    _navigate_and_wait(page, live_server.url)

    # The collapse toggle is the final child of the bottom-right stack.
    foot_class = page.evaluate(
        "() => document.querySelector('#map-controls-br').lastElementChild.className"
    )
    assert "map-controls-toggle" in foot_class, (
        f"the collapse toggle must be the foot of #map-controls-br; got {foot_class!r}"
    )

    # Its bottom edge aligns with the bottom-left (i) legend toggle (shared 60px
    # baseline). A 2px tolerance absorbs sub-pixel rounding.
    delta = page.evaluate(
        """() => {
            const foot = document.getElementById('map-controls-toggle').getBoundingClientRect();
            const info = document.getElementById('map-legend-toggle').getBoundingClientRect();
            return Math.abs(foot.bottom - info.bottom);
        }"""
    )
    assert delta <= 2, (
        f"the stack foot should sit level with the (i) toggle; bottom delta {delta}px"
    )


def test_legend_splits_danger_scale_and_map_pins(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Opening the info card shows the EAWS Danger Scale and Map pins sections.

    The danger section leads with a "No rating" row; the pins section lists
    Resorts, Favourites and Observations.
    """
    _navigate_and_wait(page, live_server.url)
    # SNOW-535: #home-intro now grows tall enough to cover the (i) toggle at
    # this viewport size — dismiss it first, as a real visitor would.
    _dismiss_home_intro(page)

    page.locator("#map-legend-toggle").click()
    page.wait_for_selector("#map-legend[data-state='expanded']", timeout=5_000)

    card_text = page.locator("#map-legend-card").inner_text()
    for expected in (
        "EAWS Danger Scale",
        "No rating",
        "Map pins",
        "Resorts",
        "Favourites",
        "Observations",
    ):
        assert expected in card_text, f"legend card should contain {expected!r}"

    # The two new keys are present as their own rows.
    assert page.locator('[data-testid="map-legend-favourites"]').count() == 1
    assert page.locator('[data-testid="map-legend-observations"]').count() == 1


def test_map_data_section_is_never_a_heading_over_nothing(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """SNOW-640: the "Map data" section shows credits or it shows nothing.

    Staging painted the heading with an empty line under it, because the
    self-hosted style serves no source attribution for the union to find.
    The invariant asserted here holds either way round, which is why it is
    written as an invariant rather than as "the section is hidden": in this
    headless context the tile CDN is unreachable so the style never
    resolves and the section stays collapsed, while against a correctly
    served style it is visible WITH credits. What must never happen is the
    third state — visible and blank.
    """
    _navigate_and_wait(page, live_server.url)
    _dismiss_home_intro(page)

    page.locator("#map-legend-toggle").click()
    page.wait_for_selector("#map-legend[data-state='expanded']", timeout=5_000)

    section = page.locator("#map-attribution-section")
    assert section.count() == 1
    if section.is_visible():
        assert page.locator("#map-attribution-text").inner_text().strip(), (
            'the "Map data" section is visible, so it must carry attribution'
        )
    else:
        # Collapsed, so its heading must not be readable in the card either.
        assert "Map data" not in page.locator("#map-legend-card").inner_text()
