"""
tests/e2e/test_basemap_download_budget.py — Playwright regression tests for
the standing download budget and whole-area eviction (SNOW-586).

The regression that matters: under the old design every pinned basemap
download shared ONE Cache Storage cache, capped at a fixed entry count and
trimmed oldest-inserted-entry-first with no record of which download an
entry belonged to. A second download that pushed the shared cache over its
cap could delete tiles from the MIDDLE of an earlier, unrelated area —
that area was not removed, it was **perforated**, silently. SNOW-586 gives
every downloaded area its own Cache Storage bucket
(``snowdesk-basemap-pinned-<areaId>``) and replaces the entry-count cap
with a standing byte budget: exceeding it evicts whole AREAS — one
``caches.delete(bucketName)`` each — oldest first, only after naming them
in a confirm banner and getting a yes.
``test_second_region_evicts_the_first_entirely_not_partially`` is the
direct regression test: it forces an eviction and asserts the evicted
area's bucket is gone ENTIRELY (not merely smaller) and the new area is
FULLY available, which the old shared-cache trim could not guarantee.

Reuses ``test_cache_this_area.py``'s helpers and fixtures (the synthetic
``FEATURE_BY_REGION_ID`` injection, the ``_stub_warm_cache`` service-worker
stub, the active-basemap-template stub) — see that file's module docstring
for the full rationale on why none of this needs a reachable basemap CDN.
Two distinct regions are used throughout (``_REGION_A`` / ``_REGION_B``,
non-overlapping bboxes ~1 degree apart — comfortably more than a z10 tile
span, so their tile sets never collide), each downloaded via the real
``#map-download-control`` roundel with a synthetically small ``mb``
estimate (1) so the budget arithmetic can be forced with a small,
easy-to-reason-about override of ``meta:app``'s ``basemap.budgetMb``
rather than downloading anything close to the real 500 MB default.

Every test's budget pre-flight runs against the region's ``mb`` ESTIMATE
(the same worst-case number ``DOWNLOAD_CEILING_MB`` uses), not the actual
``bytes`` a run reports afterward — see ``static/js/map.js``'s
``planBasemapDownloadBudget`` — so ``_stub_warm_cache``'s ``bytes_total``
override is set to match each region's ``mb`` (in bytes) purely so the
STANDING total a later download's pre-flight reads back stays consistent
with what was asked for, not because the pre-flight itself reads it.
"""

from __future__ import annotations

import json
from typing import Any, cast

from playwright.sync_api import Page, Route, Worker as SWWorker

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _CENTRE_TILE,
    _GEOMETRY,
    _STUB_BLOB,
    _reload_home,
    _stub_active_basemap_template,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

_MB = 1024 * 1024

_REGION_A = "CH-4115"
_REGION_B = "CH-4116"

_GEOMETRY_B: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [8.5000, 46.2500],
            [8.5005, 46.2500],
            [8.5005, 46.2505],
            [8.5000, 46.2505],
            [8.5000, 46.2500],
        ]
    ],
}
_CENTRE_TILE_B = {"z": 14, "x": 8578, "y": 5812}
_STUB_BLOB_B: dict[str, Any] = {
    "band": [10, 14],
    "count": 5,
    "mb": 1,
    "over_ceiling": False,
    "centre_tile": _CENTRE_TILE_B,
    "z": {
        "10": [536, 536, 363, 363],
        "11": [1072, 1072, 726, 726],
        "12": [2144, 2144, 1453, 1453],
        "13": [4289, 4289, 2906, 2906],
        "14": [8578, 8578, 5812, 5812],
    },
}
_SUMMARY_A: dict[str, Any] = {
    "count": 5,
    "mb": 1,
    "over_ceiling": False,
    "centre_tile": _CENTRE_TILE,
}
_SUMMARY_B: dict[str, Any] = {
    "count": 5,
    "mb": 1,
    "over_ceiling": False,
    "centre_tile": _CENTRE_TILE_B,
}
_BLOBS_BY_REGION = {_REGION_A: _STUB_BLOB, _REGION_B: _STUB_BLOB_B}


def _stub_region_basemap_tiles(page: Page) -> None:
    """Route ``/api/region-basemap-tiles/`` to the blob matching ``?id=``.

    Unlike ``test_cache_this_area.py``'s single-blob version, this file
    downloads TWO distinct regions and needs each to get its own blob.
    """

    def _handle_route(route: Route) -> None:
        url = route.request.url
        blob = next(
            (b for rid, b in _BLOBS_BY_REGION.items() if f"id={rid}" in url), None
        )
        if blob is None:
            route.fulfill(status=404, content_type="application/json", body="{}")
            return
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(blob)
        )

    page.route("**/api/region-basemap-tiles/**", _handle_route)


def _set_budget_mb(page: Page, mb: float) -> None:
    """Override the standing download budget via ``meta:app``.

    Nothing writes this row in production yet (SNOW-588's managed-downloads
    UI is what eventually will) — this is purely a test seam so the budget
    arithmetic can be forced without downloading anything close to the real
    500 MB default.
    """
    page.evaluate(
        "(mb) => window.pwaDb.put('meta:app', { key: 'basemap.budgetMb', value: mb })",
        mb,
    )


def _cache_names(page: Page) -> list[str]:
    return cast(list[str], page.evaluate("() => caches.keys()"))


def _select_and_click_region(
    page: Page,
    worker: SWWorker,
    region_id: str,
    summary: dict[str, Any],
    geometry: dict[str, Any],
    *,
    bytes_total: int,
) -> None:
    """Stub the warm-cache outcome, select `region_id`, and click its idle roundel.

    Deliberately does NOT wait for a terminal state afterward — a click that
    needs whole-area eviction pauses on the confirm banner, which only a
    caller expecting that banner should wait for. ``_download_region``
    below is the "no banner expected" convenience wrapper around this.
    """
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=bytes_total)
    page.evaluate(
        """({ regionId, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, download },
                geometry: geometry,
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: regionId },
            }));
        }""",
        {"regionId": region_id, "download": summary, "geometry": geometry},
    )
    icon = page.locator("#map-download-control")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()


def _download_region(
    page: Page,
    worker: SWWorker,
    region_id: str,
    summary: dict[str, Any],
    geometry: dict[str, Any],
    *,
    bytes_total: int,
) -> None:
    """Select `region_id`, click its idle roundel, and wait for it to settle.

    Only for a click that resolves WITHOUT a confirm-eviction pause — waits
    for EITHER ``done`` or ``error``. Callers whose click is expected to
    raise the whole-area-eviction confirm banner must use
    ``_select_and_click_region`` directly and handle the banner themselves.
    """
    _select_and_click_region(
        page, worker, region_id, summary, geometry, bytes_total=bytes_total
    )
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('#map-download-control');
            const state = btn && btn.dataset.downloadState;
            return state === 'done' || state === 'error';
        }""",
        timeout=10000,
    )


def _boot(pwa_page: PwaPage) -> tuple[Page, SWWorker]:
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    return page, worker


def _evict_confirm_banner(page: Page) -> Any:
    return page.locator("#map-download-evict-confirm")


def test_second_region_evicts_the_first_entirely_not_partially(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """The regression that matters: eviction removes a whole area, not a fraction.

    Downloads region A under a 1 MB budget (fits exactly), then downloads
    region B under the SAME budget — which only fits by evicting A first.
    Confirming the eviction banner must leave A's bucket gone ENTIRELY (no
    stray tiles left mid-region) and B fully available. Under the old
    shared-cache entry-count trim both would have ended up partially
    cached and both would have read as NOT downloaded.
    """
    page, worker = _boot(pwa_page)
    _set_budget_mb(page, 1)

    _download_region(
        page, worker, _REGION_A, _SUMMARY_A, _GEOMETRY, bytes_total=1 * _MB
    )
    _wait_for_state(page, "done")

    # A plain _download_region would hang here: this click pauses on the
    # confirm banner rather than resolving straight to done/error.
    _select_and_click_region(
        page, worker, _REGION_B, _SUMMARY_B, _GEOMETRY_B, bytes_total=1 * _MB
    )
    # Region B's own budget pre-flight now finds A on disk and cannot fit
    # both under a 1 MB budget — the confirm banner must appear rather than
    # the click silently going straight to a run.
    banner = _evict_confirm_banner(page)
    banner.wait_for(state="visible", timeout=10000)
    body = page.locator("#map-download-evict-confirm-body")
    assert _REGION_A in (body.text_content() or "")

    page.click("#map-download-evict-confirm-cta")
    _wait_for_state(page, "done", timeout=10000)

    # Region A's bucket is gone OUTRIGHT — not merely smaller.
    names = _cache_names(page)
    assert not any(_REGION_A in n for n in names), (
        f"expected region A's bucket to be deleted entirely, found: {names}"
    )
    # Region B's bucket exists and is fully populated.
    assert any(_REGION_B in n for n in names)

    # Re-focusing region A must now read it as genuinely NOT downloaded —
    # a perforated region would still show some tiles but fail full
    # coverage; a correctly-evicted one has none at all, both read `idle`.
    page.evaluate(
        """({ regionId, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, download },
                geometry: geometry,
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: regionId },
            }));
        }""",
        {"regionId": _REGION_A, "download": _SUMMARY_A, "geometry": _GEOMETRY},
    )
    _wait_for_state(page, "idle", timeout=10000)


def test_confirm_banner_names_the_area_and_cancel_writes_nothing(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """Cancelling the eviction banner starts nothing and evicts nothing."""
    page, worker = _boot(pwa_page)
    _set_budget_mb(page, 1)

    _download_region(
        page, worker, _REGION_A, _SUMMARY_A, _GEOMETRY, bytes_total=1 * _MB
    )
    _wait_for_state(page, "done")
    names_before = set(_cache_names(page))

    _select_and_click_region(
        page, worker, _REGION_B, _SUMMARY_B, _GEOMETRY_B, bytes_total=1 * _MB
    )

    banner = _evict_confirm_banner(page)
    banner.wait_for(state="visible", timeout=10000)

    # Cancel via the shared "×" dismiss control.
    banner.locator('[data-action="dismiss"]').click()
    banner.wait_for(state="hidden", timeout=10000)

    # Nothing ran: the roundel reverts to idle rather than busy/done/error,
    # region A's bucket is untouched, and region B's was never created —
    # the click never got past the confirm prompt to warm anything.
    _wait_for_state(page, "idle", timeout=10000)
    names_after = set(_cache_names(page))
    assert names_after == names_before
    assert not any(_REGION_B in n for n in names_after)


def test_run_larger_than_the_whole_budget_is_refused_up_front(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """A run that alone exceeds the WHOLE budget is refused, nothing written.

    No amount of evicting other areas could ever make this fit, so it must
    be refused outright — `error` state plus the budget-specific toast —
    without the CLICK fetching the region's blob or writing a single tile.

    The steps `_select_and_click_region` normally bundles are inlined here
    so the fetch count can be snapshotted at the moment of the click.
    SNOW-583 made `_probeDone` fall back to fetching the blob when no
    `basemap.regions` record exists, which is exactly this test's starting
    state — so the roundel legitimately fetches once while settling to
    `idle`, before any click. Asserting "never fetched" would now be
    asserting against SNOW-583's probe rather than against this ticket's
    refuse-up-front path; the honest invariant is that the click itself
    adds no fetch.
    """
    page, worker = _boot(pwa_page)
    _set_budget_mb(page, 1)
    oversized_summary = {**_SUMMARY_A, "mb": 600}

    fetched: list[str] = []

    def _handle_route(route: Route) -> None:
        fetched.append(route.request.url)
        route.fulfill(status=500)

    page.route("**/api/region-basemap-tiles/**", _handle_route)

    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=1 * _MB)
    page.evaluate(
        """({ regionId, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, download },
                geometry: geometry,
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: regionId },
            }));
        }""",
        {
            "regionId": _REGION_A,
            "download": oversized_summary,
            "geometry": _GEOMETRY,
        },
    )
    icon = page.locator("#map-download-control")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    fetched_before_click = len(fetched)
    icon.click()
    _wait_for_state(page, "error", timeout=10000)

    toast = page.locator("#map-download-error-toast-budget")
    toast.wait_for(state="visible", timeout=10000)
    assert len(fetched) == fetched_before_click, (
        "a refused-outright run must bail before fetching the blob"
    )
    assert not any(_REGION_A in n for n in _cache_names(page))


def test_coverage_probe_answers_correctly_with_several_buckets_present(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """Two areas can coexist under a generous budget, each reading `done`.

    Downloads both regions with no eviction needed (a large enough budget),
    then re-selects region A and asserts it STILL reads `done` — proving
    the per-region probe answers correctly with more than one pinned
    bucket on disk, not just the one it happens to be looking at.
    """
    page, worker = _boot(pwa_page)
    _set_budget_mb(page, 500)

    _download_region(
        page, worker, _REGION_A, _SUMMARY_A, _GEOMETRY, bytes_total=1 * _MB
    )
    _wait_for_state(page, "done")
    _download_region(
        page, worker, _REGION_B, _SUMMARY_B, _GEOMETRY_B, bytes_total=1 * _MB
    )
    _wait_for_state(page, "done")

    names = _cache_names(page)
    assert any(_REGION_A in n for n in names)
    assert any(_REGION_B in n for n in names)

    # Re-focus region A — still done, from real per-area cache state.
    page.evaluate(
        """({ regionId, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, download },
                geometry: geometry,
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: regionId },
            }));
        }""",
        {"regionId": _REGION_A, "download": _SUMMARY_A, "geometry": _GEOMETRY},
    )
    _wait_for_state(page, "done", timeout=10000)
