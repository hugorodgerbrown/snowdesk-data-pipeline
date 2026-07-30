"""
tests/e2e/test_basemap_menu_clip.py — Playwright regression test for
SNOW-511: the map layers popover (#basemap-menu) clipping under the header
on a short (mobile) viewport.

The menu is bottom-anchored (``bottom: -96px`` in ``static/css/map.css``)
and grows upward. Its CSS ``max-height: calc(100dvh - 96px)`` measures the
whole viewport and reserves nothing for the chrome above ``#map`` — the nav
and the conditional off-season banner — so on a tall menu / short viewport
the first rows (the Countries section, incl. the CH/FR/AT country toggles)
grew up behind the header where they were unreachable.

``basemapPickerInit`` (``static/js/map.js``) now clamps ``max-height`` on
open to the room between ``#map``'s top edge and the menu's fixed bottom
baseline, so the top rows stay on-screen and the overflow scrolls
internally. This test opens the menu at 375×812 and asserts the menu's top
edge — and the Countries section label specifically — sit at or below the
top of ``#map`` rather than above it (behind the header).

The assertion is deliberately banner-agnostic: the invariant "the menu never
overruns the top of the map surface" must hold whether or not the off-season
banner is showing, so the test does not depend on the current date/season.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import _dismiss_home_intro


def _navigate_home_mobile(page: Page, live_server_url: str) -> None:
    """Load / at a mobile viewport with the SW stripped, wait for the map.

    serviceWorker is stripped before any page script runs (mirroring the
    other map e2e tests) so ``sw_register.js`` bails out and a real worker's
    fetch interception can't influence a pure layout assertion.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )


def _menu_geometry(page: Page) -> dict[str, Any]:
    """Read the open menu's box, #map's top, and the Countries label box."""
    return cast(
        dict[str, Any],
        page.evaluate(
            """() => {
            const menu = document.getElementById('basemap-menu');
            const map = document.getElementById('map');
            const label = menu.querySelector('.basemap-menu-section-label');
            const mr = menu.getBoundingClientRect();
            const pr = map.getBoundingClientRect();
            const lr = label.getBoundingClientRect();
            return {
                hidden: menu.hidden,
                menuTop: mr.top,
                menuBottom: mr.bottom,
                mapTop: pr.top,
                labelText: label.textContent.trim(),
                labelTop: lr.top,
                scrollable: menu.scrollHeight > menu.clientHeight + 1,
            };
        }"""
        ),
    )


@pytest.mark.django_db(transaction=True)
def test_layers_menu_does_not_clip_under_header(
    live_server: LiveServer, page: Page
) -> None:
    """Opening the layers menu on mobile keeps the Countries rows on-screen.

    Regression coverage for SNOW-511: the menu's top edge, and the Countries
    section label at the very top of the list, must not rise above the top of
    ``#map`` (i.e. behind the nav + off-season banner).
    """
    _navigate_home_mobile(page, live_server.url)
    # Mirrors what a real visitor does before using the map. The card no
    # longer covers #basemap-toggle (its max-width is capped against the
    # viewport — see tests/e2e/test_home_intro_clearance.py), so this is
    # fidelity rather than a workaround, and it is a no-op under the autouse
    # suppression fixture anyway.
    _dismiss_home_intro(page)

    page.click("#basemap-toggle")
    page.wait_for_selector("#basemap-menu:not([hidden])")
    # Let the open handler's clamp (and any layout) settle before measuring.
    page.wait_for_function(
        "() => document.getElementById('basemap-menu').style.maxHeight !== ''"
    )

    geom = _menu_geometry(page)

    assert geom["hidden"] is False
    # The full menu is taller than the room available on a 375x812 viewport,
    # so the clamp must have engaged and the list must scroll internally —
    # otherwise the test isn't exercising the overflow case the bug needs.
    assert geom["scrollable"] is True, (
        "menu should overflow and scroll internally on a short viewport"
    )
    # The core invariant: the menu never overruns the top of the map surface.
    assert geom["menuTop"] >= geom["mapTop"] - 1, (
        f"menu top {geom['menuTop']} rose above #map top {geom['mapTop']} "
        "— it is clipping behind the header"
    )
    # And the first rows (the Countries section) are reachable, not hidden
    # behind the chrome at the top.
    assert geom["labelText"] == "Countries"
    assert geom["labelTop"] >= geom["mapTop"] - 1, (
        f"Countries label top {geom['labelTop']} is above #map top "
        f"{geom['mapTop']} — the country toggles are hidden behind the header"
    )
