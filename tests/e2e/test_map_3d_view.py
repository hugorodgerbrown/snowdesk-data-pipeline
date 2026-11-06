"""tests/e2e/test_map_3d_view.py — A reader tilts the map into 3-D, in Terrain and in Conditions.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Here for what jsdom cannot answer: that MapLibre's own terrain goes on and
comes off a real WebGL map through the 3D roundel, the swisstopo winter
swap and the reader's basemap coming back; and (SNOW-1452) that in
Conditions the danger fill drapes on the relief and a real tap on the
tilted map still hits it. Every rule (availability, the URL, the zoom
floor, both modes, offline or outside Switzerland, route drawing, the
timelapse) is in tests/js/test_map_view_core.js and
tests/js/test_map_view_3d.js, and the 3-D paint in
tests/js/test_map_bulletins_layer.js. Every tile is a fixture
(``map_3d_page``).

Scenario: MS10
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect
from waffle.testutils import override_flag

from tests.e2e.conftest import Map3dPage

_ZERMATT = '{"lng": 7.75, "lat": 46.02, "zoom": 11.5, "bearing": 0, "pitch": 0}'


@pytest.mark.django_db(transaction=True)
def test_tilt_the_terrain_map_into_3d(map_3d_page: Map3dPage) -> None:
    """3D lifts the relief and 2D lays it flat on the reader's basemap."""
    page = map_3d_page.page
    page.add_init_script(f"localStorage.setItem('snowdesk.map.viewport', '{_ZERMATT}')")
    with override_flag("map_3d", active=True), override_flag("map_modes", active=True):
        page.goto(f"{map_3d_page.live_server_url}/map/?mode=terrain")
        page.wait_for_function("() => window.pwaMapView && MAP && MAP.loaded()")
        page.click('#map-view-toggle [data-view-face="3d"]')
        page.wait_for_function("() => (MAP.getTerrain() || {}).source === 'map-3d-dem'")
        assert "view=3d" in page.url
        assert page.evaluate("MAP.getStyle().name") == "e2e-swisstopo-winter"
        page.evaluate("MAP.rotateTo(40, { duration: 0 })")
        expect(page.locator("#map-compass")).to_be_visible()
        page.click("#map-today-panel-toggle")
        expect(page.locator("#today-sheet")).to_be_visible()
        page.wait_for_timeout(500)
        expect(page.locator("#today-sheet")).to_be_visible()
        page.click('#map-view-toggle [data-view-face="flat"]')
        page.wait_for_function(
            "() => !MAP.getTerrain() && MAP.getStyle().name === 'e2e-reader'"
        )
        assert "view=3d" not in page.url
        assert page.evaluate("MAP.getPitch()") == 0


# Verbier, inside CH-4115, which ``_load_test_data`` seeds with a boundary.
_VERBIER = '{"lng": 7.10, "lat": 46.10, "zoom": 10.5, "bearing": 0, "pitch": 0}'


@pytest.mark.django_db(transaction=True)
def test_conditions_drapes_in_3d_and_a_tilted_tap_picks_the_region(
    map_3d_page: Map3dPage, _load_test_data: None
) -> None:
    """SNOW-1452: Conditions opens in 3-D, the fill drapes, and a real tap hits it."""
    page = map_3d_page.page
    page.add_init_script(f"localStorage.setItem('snowdesk.map.viewport', '{_VERBIER}')")
    with override_flag("map_3d", active=True), override_flag("map_modes", active=True):
        page.goto(f"{map_3d_page.live_server_url}/map/?view=3d")
        page.wait_for_function(
            "() => window.pwaMapView && MAP && MAP.loaded()"
            " && (MAP.getTerrain() || {}).source === 'map-3d-dem' && MAP.getPitch() > 30"
        )
        assert page.evaluate("window.pwaMapMode.get()") == "dated"
        # The wash: one per-feature case, no zoom fade, drawn over the relief.
        page.wait_for_function(
            "() => !!MAP.getLayer('regions-fill')"
            " && MAP.getPaintProperty('regions-fill', 'fill-opacity')[0] === 'case'"
        )
        point = "MAP.project([7.10, 46.10])"
        page.wait_for_function(
            f"() => MAP.queryRenderedFeatures({point}, {{ layers: ['regions-fill'] }}).length > 0"
        )
        page.evaluate(
            "() => { window.__picked = []; document.addEventListener("
            "'snowdesk:region-selected', (e) => window.__picked.push(e.detail)); }"
        )
        box = page.locator("#map canvas").bounding_box()
        at = page.evaluate(f"() => {{ const p = {point}; return [p.x, p.y]; }}")
        assert box is not None
        page.mouse.click(box["x"] + at[0], box["y"] + at[1])
        page.wait_for_function("() => window.__picked.some((d) => d && d.region_id)")
        assert page.evaluate("window.pwaMapView.get()") == "3d"
