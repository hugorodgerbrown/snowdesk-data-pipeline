"""
tests/e2e/test_offline_micro_region_paint.py — Playwright regression test for
SNOW-638 ("Micro-region overlay doesn't paint offline despite its sync dot
showing green").

Root cause: the boot handler's ``map.on('load', ...)`` in ``static/js/map.js``
fetched ``regions.geojson``, ``ratings.json`` and ``resorts.json`` in a single
``Promise.all`` with only the ratings leg guarded by a ``.catch()``.
``/api/resorts.json`` is a plain-JSON sibling deliberately absent from
``sw.js``'s ``STATIC_PATHS`` (only the ``.geojson`` variant is listed there),
so it rejects on every offline load — and with no ``.catch()`` on that leg,
the whole ``Promise.all`` rejected, so ``installRegionsLayers`` /
``applyCountryFilters`` / the choropleth paint never ran, even though the
regions feed the sync dot itself probes (``regions.geojson``) was perfectly
fine. The dot was honest; the render path was needlessly coupled to an
unrelated, permanently-uncacheable sibling fetch.

This test pins the exact repro without needing a real offline browser
context: it aborts ``/api/resorts.json`` ONLY via ``page.route`` (the same
"simulate an offline fetch failure without a real offline browser context"
technique ``test_offline_basemap_fallback.py`` uses for the basemap style
fetch — a page-level fetch, so ``page.route`` sees it directly), while
``regions.geojson`` and ``ratings.json`` are left unrouted and serve
normally from the real ``live_server``. Before the fix this reproduces the
bug deterministically: after the fix, the regions source/layer install
regardless of the resorts fetch's outcome.

The service worker is stripped (same technique as
``test_offline_layer_gating.py`` / ``test_offline_basemap_fallback.py``) so
the SW's own fetch handling can't shadow the ``page.route`` abort — sw.js
already treats ``/api/resorts.json`` as network-only (it's not in
``STATIC_PATHS``), so this isn't strictly required for that URL, but it
keeps this test's failure mode isolated to the page-level fetch under test,
matching the sibling files' established convention.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _poll(page: Page, expr: str, *, timeout_ms: int = 20000) -> None:
    """Poll a JS boolean ``expr`` via ``page.evaluate`` until it is truthy.

    Matches ``test_offline_basemap_fallback.py``'s helper — on the
    strict-CSP map page ``wait_for_function``'s polling proved unreliable
    for MapLibre source/layer predicates, whereas ``page.evaluate`` (CDP
    ``Runtime.evaluate``) reads them reliably.
    """
    waited = 0
    while waited < timeout_ms:
        if page.evaluate(expr):
            return
        page.wait_for_timeout(250)
        waited += 250
    raise AssertionError(f"condition never became true within {timeout_ms}ms: {expr}")


def test_regions_choropleth_paints_when_resorts_fetch_fails(
    live_server: LiveServer, page: Page
) -> None:
    """The regions source/layer install even though /api/resorts.json fails.

    Regression coverage for SNOW-638: before the fix, aborting only the
    resorts fetch took down the whole boot ``Promise.all``, so
    ``MAP.getSource('regions')`` and the ``regions-fill`` layer never
    existed. After the fix, the resorts leg degrades independently and the
    regions install proceeds regardless.
    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    # /api/resorts.json ONLY — regions.geojson and ratings.json are left
    # unrouted and serve normally from the real live_server, exactly as an
    # offline browser's cache-miss looks for one specific uncacheable feed
    # while its siblings are unaffected.
    page.route("**/api/resorts.json", lambda route: route.abort())

    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')

    _poll(
        page,
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')",
    )
    _poll(
        page,
        "() => MAP.getLayer('regions-fill') && "
        "MAP.getLayoutProperty('regions-fill', 'visibility') === 'visible'",
    )
    assert page_errors == [], f"unexpected JS errors: {page_errors}"
