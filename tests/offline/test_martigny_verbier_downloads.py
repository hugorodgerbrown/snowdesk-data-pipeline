"""
tests/offline/test_martigny_verbier_downloads.py — every way into a download
works on one real Swiss region, with every map feature switched on.

The rest of this suite draws its region and basemap from the week's seed and
signs in a plain account. That walks the space, but it never once ran a
download the way the product's owner does: on Martigny-Verbier, on the
Swisstopo map, with the rollout flags on. On 12 Nov 2026 that broke in
production in two ways no test saw:

  - the winter map beyond the border (``map_beyond_switzerland``, SNOW-1460)
    adds an ``osmclip://`` source the service worker cannot fetch, and the
    download listed it, so the area ended Incomplete after hundreds of
    failed fetches;
  - a tap made while any tile was still loading found no basemap at all
    (``map.isStyleLoaded()`` is false until every source is idle) and
    failed with "Check your connection".

So this file fixes the subject, switches every flag on, and runs each entry
point Hugo uses: a region download from a fresh worker, the same after a
worker update, a framed custom area, and Sync all over a downloaded region.
Each asserts the download finished and that no request the worker cannot
make was attempted.

Scenario: D1, P4
"""

from __future__ import annotations

from typing import Any

import pytest
from waffle.models import Flag

from apps.core.sw_shell import served_cache_version
from tests.offline.conftest import OfflineMapPage
from tests.offline.fuzz import OfflineSubject

_REGION_ID = "CH-4115"

# Every rollout flag that changes the map page, on for everyone.
_FLAGS = ("map_beyond_switzerland", "map_3d", "map_modes", "route_drawing")

# Ask for the update, apply it, and wait until the new worker is active —
# the same steps test_update_keeps_downloaded_content.py takes. The deadline
# is longer here: the map tops up its shared base layer as it boots, and the
# old worker finishes that warm before the new one can take over.
_APPLY_UPDATE_JS = """async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    await registration.update();
    const next = registration.installing || registration.waiting;
    if (!next) return 'no new worker';
    const deadline = Date.now() + 180000;
    while (next.state !== 'activated' && Date.now() < deadline) {
        if (next.state === 'installed') next.postMessage({ type: 'SKIP_WAITING' });
        if (next.state === 'redundant') return 'redundant';
        await new Promise((resolve) => setTimeout(resolve, 200));
    }
    return next.state;
}"""


@pytest.fixture()
def subject(django_db_blocker: Any, _load_offline_dataset: None) -> OfflineSubject:
    """Martigny-Verbier on the Swisstopo map, not the week's draw.

    Overrides the conftest's seeded ``subject`` for this module only.
    """
    from apps.regions.models import MicroRegion

    with django_db_blocker.unblock():
        region = MicroRegion.objects.get(region_id=_REGION_ID)
        blob = region.basemap_download or {}
        centre = region.centre_point()
    assert centre is not None and blob.get("count"), (
        f"{_REGION_ID} has no centre or no download blob in the seeded data."
    )
    latitude, longitude = centre
    return OfflineSubject(
        seed="fixed:martigny-verbier",
        region_id=region.region_id,
        region_name=region.name,
        tile_count=int(blob["count"]),
        centre=(latitude, longitude),
        outside=(latitude + 0.5, longitude + 0.5),
        basemap_key="swisstopo_winter",
        inside_zoom=12,
        above_band_zoom=16,
        below_band_zoom=9,
    )


@pytest.fixture()
def every_flag_on(django_db_blocker: Any, _load_offline_dataset: None) -> None:
    """Switch every map rollout flag on, before the page renders."""
    with django_db_blocker.unblock():
        for name in _FLAGS:
            Flag.objects.update_or_create(name=name, defaults={"everyone": True})


def _unfetchable_requests(page: OfflineMapPage) -> list[str]:
    """Return console errors from a fetch of a scheme the worker cannot use."""
    return [line for line in page.console_errors if "URL scheme" in line]


def _assert_beyond_switzerland_drawn(page: OfflineMapPage) -> None:
    """Fail unless the map carries the ``osmclip://`` source this file guards."""
    spliced = page.page.evaluate(
        "() => !!(window.snowdeskMap && window.snowdeskMap.getSource('beyond-osm'))"
    )
    assert spliced, (
        "The map has no beyond-osm source, so this run did not exercise the "
        "beyond-Switzerland map the flag turns on."
    )


def _assert_clean(page: OfflineMapPage) -> None:
    """Fail if the download asked the worker for anything it cannot fetch."""
    bad = _unfetchable_requests(page)
    assert not bad, (
        f"The download attempted {len(bad)} request(s) the service worker "
        f"cannot make, first: {bad[0]}"
    )


def test_a_region_downloads_with_every_flag_on(
    every_flag_on: None, offline_map_page: OfflineMapPage
) -> None:
    """From a fresh worker, Martigny-Verbier downloads and draws offline."""
    offline_map_page.choose_basemap("swisstopo_winter")
    _assert_beyond_switzerland_drawn(offline_map_page)
    # Straight from the search fly-to, with tiles still loading: the tap a
    # hurried user makes, and the one that used to find no basemap.
    offline_map_page.select_region(offline_map_page.subject.region_name)
    offline_map_page.download_selected_region()
    _assert_clean(offline_map_page)


def test_a_region_downloads_after_a_worker_update(
    every_flag_on: None,
    offline_map_page: OfflineMapPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a deploy's new worker takes over, the download still works."""
    page = offline_map_page.page
    new = f"{served_cache_version()}-next"
    for module in (
        "apps.public.views",
        "apps.public.api",
        "apps.public.context_processors",
    ):
        monkeypatch.setattr(f"{module}.served_cache_version", lambda: new)
    state = page.evaluate(_APPLY_UPDATE_JS)
    assert state == "activated", f"The {new} worker ended {state!r}, not activated."
    page.reload()
    page.wait_for_load_state("load")
    page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=20_000)

    offline_map_page.choose_basemap("swisstopo_winter")
    offline_map_page.select_region(offline_map_page.subject.region_name)
    offline_map_page.download_selected_region()
    _assert_clean(offline_map_page)


def test_a_custom_area_downloads(
    every_flag_on: None, offline_map_page: OfflineMapPage
) -> None:
    """A small area framed over Verbier from the Downloads sheet downloads."""
    page = offline_map_page.page
    offline_map_page.choose_basemap("swisstopo_winter")
    latitude, longitude = offline_map_page.subject.centre
    offline_map_page.jump_to(longitude, latitude, 13)

    page.goto(offline_map_page.live_server_url + "/map/?downloads=open")
    page.wait_for_load_state("load")
    page.wait_for_selector("#map-downloads-sheet [data-panel-add]", state="visible")
    offline_map_page.jump_to(longitude, latitude, 13)
    page.click("#map-downloads-sheet [data-panel-add]")
    confirm = page.locator("#map-frame-confirm")
    confirm.wait_for(state="visible", timeout=15_000)
    page.wait_for_function(
        "() => !document.getElementById('map-frame-confirm').disabled", timeout=30_000
    )
    confirm.click()
    page.wait_for_function(
        """() => {
            const state = document.getElementById('map-frame-overlay')?.dataset.runState;
            return state === 'done' || state === 'error';
        }""",
        timeout=240_000,
    )
    state = page.get_attribute("#map-frame-overlay", "data-run-state")
    assert state == "done", (
        f"The custom area over {offline_map_page.subject.region_name} finished "
        f"{state!r}, not 'done'.\n  " + offline_map_page.diagnostics()
    )
    _assert_clean(offline_map_page)


def test_sync_all_finishes_over_a_downloaded_region(
    every_flag_on: None, offline_map_page: OfflineMapPage
) -> None:
    """Sync all over a fresh download leaves the region ready, with no fault."""
    page = offline_map_page.page
    offline_map_page.choose_basemap("swisstopo_winter")
    offline_map_page.select_region(offline_map_page.subject.region_name)
    offline_map_page.download_selected_region()

    page.goto(offline_map_page.live_server_url + "/map/?downloads=open")
    page.wait_for_load_state("load")
    sync_all = page.locator('[data-testid="downloads-sync-all"]')
    sync_all.wait_for(state="visible", timeout=20_000)
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"downloads-sync-all\"]').disabled",
        timeout=20_000,
    )
    sync_all.click()
    page.wait_for_function(
        "() => window.pwaDownloadsSyncAll.state().running", timeout=10_000
    )
    page.wait_for_function(
        "() => !window.pwaDownloadsSyncAll.state().running", timeout=240_000
    )
    verdict = page.evaluate(
        """async (regionId) => {
            const records = await window.pwaDownloadsSyncAll.records();
            const key = [...records.keys()].find((id) => id.includes(regionId));
            return key ? window.pwaDownloadsSyncAll.verdict(records.get(key)) : null;
        }""",
        offline_map_page.subject.region_id,
    )
    assert verdict and verdict["verdict"] == "ready", (
        f"After Sync all, {offline_map_page.subject.region_name} reads {verdict!r}, "
        "not ready.\n  " + offline_map_page.diagnostics()
    )
    _assert_clean(offline_map_page)


# A failed run's leftovers: the tiles stay in the area's bucket and the
# record is never written. Remove the record and one map tile, as a run cut
# short leaves them, and say which tile went.
_LEAVE_AS_A_FAILED_RUN_JS = """async (regionId) => {
    const key = 'basemap.regions';
    const row = await window.pwaDb.get('meta:app', key);
    const kept = ((row && row.value) || []).filter((entry) => entry.region_id !== regionId);
    await window.pwaDb.put('meta:app', { key: key, value: kept });
    const name = self.pwaBasemapDownloadCore.pinnedCacheName('region-' + regionId);
    const bucket = await caches.open(name);
    const tile = (await bucket.keys()).find((request) => /\\/\\d+\\/\\d+\\/\\d+(\\.\\w+)?(\\?|$)/.test(request.url));
    if (!tile) return '';
    await bucket.delete(tile);
    return tile.url;
}"""


def test_a_failed_region_is_named_and_completes_from_its_row(
    every_flag_on: None, offline_map_page: OfflineMapPage
) -> None:
    """A region a failed run left behind reads by name and completes.

    Hugo, 12 Nov 2026: the row read "region-CH-4115", and Remove was all
    it offered.
    """
    page = offline_map_page.page
    subject = offline_map_page.subject
    offline_map_page.choose_basemap("swisstopo_winter")
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()
    lost = page.evaluate(_LEAVE_AS_A_FAILED_RUN_JS, subject.region_id)
    assert lost, "The download left no map tile to take away."

    page.goto(offline_map_page.live_server_url + "/map/?downloads=open")
    page.wait_for_load_state("load")
    row = page.locator(
        f'#map-downloads-sheet li[data-row-area="region-{subject.region_id}"]'
    )
    row.wait_for(state="visible", timeout=20_000)
    assert row.locator("[data-row-label]").inner_text() == subject.region_name
    assert row.locator("[data-row-meta]").inner_text() == "Region · Incomplete"
    assert (
        row.locator("[data-row-status]").get_attribute("data-area-verdict")
        == "incomplete"
    )

    # "Incomplete" is the exception on the row, and says why when tapped.
    row.locator("[data-incomplete-term]").click()
    popover = row.locator("[data-incomplete-def]")
    popover.wait_for(state="visible", timeout=5_000)
    assert "Download failed" in popover.inner_text()
    page.keyboard.press("Escape")
    popover.wait_for(state="hidden", timeout=5_000)

    row.locator('[data-overflow-menu] [aria-haspopup="menu"]').click()
    row.locator("[data-downloads-complete]").click()
    page.wait_for_function(
        """async ([regionId, url]) => {
            const row = await window.pwaDb.get('meta:app', 'basemap.regions');
            const recorded = ((row && row.value) || []).some((e) => e.region_id === regionId);
            const name = self.pwaBasemapDownloadCore.pinnedCacheName('region-' + regionId);
            return recorded && !!(await (await caches.open(name)).match(url));
        }""",
        arg=[subject.region_id, lost],
        timeout=240_000,
    )
    _assert_clean(offline_map_page)
