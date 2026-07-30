"""
tests/e2e/test_home_intro_clearance.py — Playwright regression test for the
first-run intro card (#home-intro) covering the right-anchored map utility
cluster on a narrow viewport.

#home-intro is absolutely positioned at left:16px over #map and had a flat
``max-width: 380px``. The utility cluster (#basemap-toggle and friends) is
anchored to the right edge, so on any viewport narrower than roughly 430px
the two overlapped: at 412px the card spanned x 16–396 while #basemap-toggle
sat at x 360–400. The card is painted above the cluster, so it won the hit
test and the toggle could not be clicked — Playwright reports "subtree
intercepts pointer events".

``.home-intro-card``'s ``max-width: min(380px, calc(100vw - 96px))`` now caps
the card against the viewport, reserving the cluster's column.

Every other e2e test starts with the panel already dismissed
(``_suppress_home_intro`` in conftest.py), which is what let this survive:
the collision only exists while the panel is showing. This module therefore
carries ``@pytest.mark.shows_home_intro`` to opt back in — without it the
test passes vacuously against a hidden card.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

# Widths either side of the old 380px card. 412 is where the collision was
# measured; 375 is the narrowest common phone and the viewport SNOW-535's
# own follow-up fixes were chasing.
NARROW_VIEWPORTS = [(375, 812), (412, 916)]


def _navigate_home(page: Page, live_server_url: str, width: int, height: int) -> None:
    """Load / at the given viewport with the SW stripped, wait for the map.

    serviceWorker is stripped before any page script runs (mirroring the
    other map e2e tests) so a real worker's fetch interception cannot
    influence a pure layout assertion.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#home-intro:not([hidden])")
    page.wait_for_selector("#basemap-toggle")


def _clearance(page: Page) -> dict[str, Any]:
    """Read the card box, the toggle box, and what is painted over the toggle."""
    return cast(
        dict[str, Any],
        page.evaluate(
            """() => {
                const card = document.querySelector('.home-intro-card')
                    .getBoundingClientRect();
                const toggle = document.querySelector('#basemap-toggle')
                    .getBoundingClientRect();
                const hit = document.elementFromPoint(
                    toggle.left + toggle.width / 2,
                    toggle.top + toggle.height / 2,
                );
                return {
                    cardRight: card.right,
                    toggleLeft: toggle.left,
                    hitId: hit ? hit.id : null,
                    hitInsideCard: hit
                        ? !!hit.closest('#home-intro')
                        : false,
                };
            }"""
        ),
    )


@pytest.mark.shows_home_intro
@pytest.mark.parametrize(("width", "height"), NARROW_VIEWPORTS)
def test_intro_card_clears_the_utility_cluster(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
    width: int,
    height: int,
) -> None:
    """The intro card leaves #basemap-toggle hit-testable at narrow widths.

    Asserts the geometry (the card's right edge stops short of the toggle)
    and the consequence (the topmost element at the toggle's centre is not
    inside the card). The second assertion is the one that matters — a card
    that merely overlaps but sits below in the stacking order would still be
    clickable, and a future change to z-index should not fail this test for
    the wrong reason.
    """
    _navigate_home(page, live_server.url, width, height)
    geometry = _clearance(page)
    assert not geometry["hitInsideCard"], (
        f"#home-intro is painted over #basemap-toggle at {width}x{height} "
        f"(topmost element id={geometry['hitId']!r})"
    )
    assert geometry["cardRight"] <= geometry["toggleLeft"], (
        f"intro card right edge {geometry['cardRight']} overlaps "
        f"#basemap-toggle left edge {geometry['toggleLeft']} at {width}x{height}"
    )


@pytest.mark.shows_home_intro
def test_basemap_toggle_is_clickable_with_intro_showing(
    live_server: LiveServer, page: Page, _load_test_data: None
) -> None:
    """Clicking #basemap-toggle opens the layers menu while the card shows.

    The geometry test above proves the card is out of the way; this proves
    the click actually lands. Playwright's actionability check fails with
    "subtree intercepts pointer events" if the card is over the control, so
    the click itself is the assertion.
    """
    _navigate_home(page, live_server.url, 412, 916)
    page.click("#basemap-toggle")
    page.wait_for_selector("#basemap-menu:not([hidden])")
