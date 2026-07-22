"""
tests/e2e/test_map_bulletin_journey.py — Playwright happy-path journey for
SNOW-497: tapping a region on the map surfaces its bulletin.

Loads ``/?d=2026-04-08`` (the canonical seeded date — CH-4115/Martigny-
Verbier carries a ``RegionDayRating`` for it, so ``region_summary`` returns
the "Open bulletin" CTA branch of ``public/_region_tooltip.html`` rather
than the no-bulletin placeholder), taps the region, and asserts the
resulting MapLibre popup (``.region-popup``) carries a real
``[data-testid="region-tooltip-bulletin-link"]`` link to that day's
bulletin page.

The click is dispatched via ``MAP.fire('click', …)`` with
``queryRenderedFeatures`` stubbed to return a real region feature id —
the same technique ``tests/e2e/test_map_marker_exclusion.py``'s
``test_region_tap_still_selects_when_no_marker_near`` uses, and for the
same reason: headless Chromium does not reliably composite the
``regions-fill`` layer for a genuine pixel hit-test, so a synthetic
``fire`` on the real (non-layer-scoped) click handler is the deterministic
way to drive the exact same production code path a real tap would. This is
the highest-flake-risk journey in the SNOW-497 set — if a future change
makes even the stubbed click driving the popup fetch unreliable, prefer
falling back to the plain selection-state assertion
``test_map_marker_exclusion.py`` already proves (``snowdesk:region-selected``
firing with a real ``region_id``) over reintroducing a sleep.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

_DATE = "2026-04-08"
_LAT = 46.10
_LON = 7.10


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / for the seeded date and wait for the map to finish booting."""
    page.goto(f"{live_server_url}/?d={_DATE}")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def test_region_tap_surfaces_bulletin_popup(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
    _disable_real_sw: None,
) -> None:
    """Tapping the CH-4115 region polygon opens a popup with a bulletin link."""
    _navigate_home(page, live_server.url)

    page.wait_for_function(
        "() => !!MAP.getSource('regions') && "
        "(MAP.getSource('regions').serialize().data.features || [])"
        ".some((f) => f.properties && f.properties.id === 'CH-4115')"
    )

    # Make the fill-layer hit-test deterministic — see the module docstring
    # for why a synthetic queryRenderedFeatures stub is used rather than a
    # genuine pixel click. Targets CH-4115 specifically (not an arbitrary
    # feature) so the popup's bulletin link is asserted against a known,
    # seeded ``RegionDayRating``.
    region_id = page.evaluate(
        """() => {
            const data = MAP.getSource('regions').serialize().data;
            const f = data.features.find(
                (x) => x.properties && x.properties.id === 'CH-4115',
            );
            const orig = MAP.queryRenderedFeatures.bind(MAP);
            MAP.queryRenderedFeatures = (geometry, options) => {
                const opts = options || geometry || {};
                const layers = opts.layers || [];
                if (layers.indexOf('regions-fill') !== -1) {
                    return [{ layer: { id: 'regions-fill' }, id: f.id }];
                }
                return orig(geometry, options);
            };
            return f.id;
        }"""
    )
    assert region_id is not None

    point = page.evaluate(
        f"() => {{ const p = MAP.project([{_LON}, {_LAT}]);"
        " return { x: p.x, y: p.y }; }"
    )
    page.evaluate(
        "({ x, y }) => MAP.fire('click', {"
        f" lngLat: {{ lng: {_LON}, lat: {_LAT} }},"
        " point: { x, y } })",
        point,
    )

    popup = page.locator(".region-popup")
    expect(popup).to_be_visible()
    expect(
        popup.locator('[data-testid="region-tooltip-bulletin-link"]')
    ).to_have_attribute("href", f"/ch-4115/martigny-verbier/{_DATE}/")
