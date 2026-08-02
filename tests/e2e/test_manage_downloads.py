"""
tests/e2e/test_manage_downloads.py — Playwright tests for the "Manage
downloads" sheet (SNOW-588).

The surface that lists what this device has stored offline, what each area
costs, and lets the user delete any of it or change the standing budget.
Opened from the ``Manage downloads…`` row in the layers menu; markup in
``public/partials/_map_downloads_sheet.html``, driven by
``static/js/map_downloads_manager.js`` over
``static/js/basemap_manage_core.js``.

**What these cover that the Vitest suites cannot.**
``tests/js/test_map_downloads_manager.js`` exercises the same behaviours
against a hand-copied fixture, because Vitest cannot render a Django
template. These run against the REAL template, the real layers menu and a
real Cache Storage — so they are what catches the fixture and the template
drifting apart, and what proves the two ``<template>`` elements the module
clones are actually served with the ids it looks them up by.

**Why the seeded tests use the plain ``page``/``live_server`` fixtures.**
With the real service worker controlling, the basemap style's sources never
resolve against the unreachable CDN, so ``map.on('load')`` never fires —
the same reason ``test_downloaded_areas_overlay.py`` drops it. This surface
never talks to a service worker (IndexedDB and Cache Storage are both
available to the page directly) and needs no region lookup, since names
live in the download record — so nothing is lost. The one real-download
test does need ``pwa_page``, because a download goes through the worker.

Two halves, deliberately.

Most tests SEED the records and drive the sheet, which is what makes the
breadth affordable: sizes, names, the empty device and the over-budget case
are all chosen by the test, where a real download of each would need a
reachable tile CDN. Crucially they seed the records main ACTUALLY writes —
``basemap.regions`` and ``basemap.customArea``, in the writer's own shape,
never one invented here. Seeding an invented shape is exactly how an
earlier version of this file stayed green while the sheet was reading a key
that nothing wrote.

``test_a_real_download_is_listed_and_can_be_removed`` is the other half,
and the one that pins the wiring down. It drives the REAL region-download
control, so map.js's own ``_recordRegionDownload`` writes the record and
the sheet reads back whatever that produced. No amount of seeding can catch
a key or a field name drifting between the writer and this reader; that
test can, which is why its helpers are imported from
``test_cache_this_area`` rather than reimplemented here.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _GEOMETRY,
    _MICRO_SUMMARY,
    _reload_home,
    _stub_active_basemap_template,
    _stub_region_basemap_tiles,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

pytestmark = pytest.mark.usefixtures("_load_test_data")

_MENU_TOGGLE = "#basemap-toggle"
_MENU_ROW = '[data-menu-action="manage-downloads"]'
_SHEET = "#map-downloads-sheet"
_PINNED_PREFIX = "snowdesk-basemap-pinned-"
# The pre-SNOW-586 single pinned cache. SNOW-586 retired it — sw.js now
# deletes it on activate — but a page whose worker has not yet swept can
# still have one lying about, and it is not an area, so it must never be
# counted as a listed download.
_LEGACY_PINNED_CACHE = "snowdesk-basemap-pinned-v1"

_MB = 1024 * 1024


def _regions(
    region_id: str = "CH-4115", name: str = "Aletsch", mb: int = 41
) -> list[dict[str, Any]]:
    """A ``basemap.regions`` record, in the shape map.js really writes.

    ``region_id`` is NOT the Cache Storage bucket id — that is
    ``areaIdForRegion(region_id)``, i.e. ``region-<region_id>``. Seeding in
    the writer's shape is what forces the sheet through that mapping
    rather than letting it assume the two are the same.
    """
    return [
        {
            "region_id": region_id,
            "name": name,
            "band": [10, 14],
            "z": {"10": [1, 1, 1, 1]},
            "bytes": mb * _MB,
            "savedAt": "2026-08-01T10:00:00.000Z",
        }
    ]


def _custom_area(mb: int = 123) -> dict[str, Any]:
    """A ``basemap.customArea`` record, in the shape map.js really writes."""
    return {
        "bbox": [7.9, 46.4, 8.1, 46.6],
        "band": [10, 14],
        "centre_tile": {"z": 14, "x": 8580, "y": 5810},
        "bytes": mb * _MB,
        "savedAt": "2026-08-02T10:00:00.000Z",
    }


def _boot(page: Page, live_server: LiveServer) -> None:
    """Navigate and wait for the sheet module and map.js's records bridge.

    No wait on ``FEATURE_BY_REGION_ID`` any more: names are stored in the
    download record itself, so the sheet needs no region lookup and cannot
    race the regions fetch.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        """() => typeof window.pwaDownloadsManager === 'object'
            && typeof window.pwaDb === 'object'
            && typeof window.pwaBasemapDownloads === 'object'""",
        timeout=30000,
    )


def _seed(
    page: Page,
    regions: list[dict[str, Any]] | None = None,
    custom: dict[str, Any] | None = None,
    budget_mb: int = 500,
) -> None:
    """Write the real records, plus the pinned bucket each area owns.

    The buckets are what a delete has to remove, so they are created for
    real — an empty ``Cache`` is still a ``Cache``, and ``caches.keys()``
    lists it, which is all the assertions need. Their names are derived
    the same way production derives them, via ``areaIdForRegion`` /
    ``CUSTOM_AREA_ID``.
    """
    page.evaluate(
        """async ({regions, custom, budgetMb}) => {
            const core = window.pwaBasemapDownloadCore;
            await window.pwaDb.put(
                'meta:app', {key: 'basemap.regions', value: regions || []},
            );
            if (custom) {
                await window.pwaDb.put(
                    'meta:app', {key: 'basemap.customArea', value: custom},
                );
            } else {
                await window.pwaDb.delete('meta:app', 'basemap.customArea');
            }
            await window.pwaDb.put(
                'meta:app', {key: 'basemap.budgetMb', value: budgetMb},
            );
            for (const entry of regions || []) {
                await caches.open(core.pinnedCacheName(
                    core.areaIdForRegion(entry.region_id),
                ));
            }
            if (custom) {
                await caches.open(core.pinnedCacheName(core.CUSTOM_AREA_ID));
            }
        }""",
        {"regions": regions or [], "custom": custom, "budgetMb": budget_mb},
    )


def _open_sheet(page: Page) -> None:
    """Open the sheet the way a user does — through the layers menu."""
    page.click(_MENU_TOGGLE)
    page.click(_MENU_ROW)
    expect(page.locator(_SHEET)).to_be_visible()


def _pinned_buckets(page: Page) -> list[str]:
    """Every PER-AREA pinned basemap cache bucket on the device.

    ``snowdesk-basemap-pinned-v1`` — the single undifferentiated pinned
    cache that predates SNOW-586's per-area buckets — is excluded. It is not
    an area, so counting it would make every assertion here off by one for
    a reason that has nothing to do with this surface.
    """
    return sorted(
        page.evaluate(
            """async ({prefix, legacy}) => (await caches.keys())
                .filter(n => n.startsWith(prefix) && n !== legacy)""",
            {"prefix": _PINNED_PREFIX, "legacy": _LEGACY_PINNED_CACHE},
        )
    )


def _stored_area_ids(page: Page) -> list[str]:
    """Area ids across BOTH records, read the way production reads them.

    Goes through ``window.pwaBasemapDownloads.areas()`` rather than the raw
    rows, so the assertion covers the region-id-to-bucket-id mapping too —
    a delete that removed the bucket but left the record entry (or vice
    versa) shows up here.
    """
    ids: list[str] = page.evaluate(
        "async () => (await window.pwaBasemapDownloads.areas()).map(a => a.id)"
    )
    return ids


def _row_texts(page: Page, selector: str) -> list[str]:
    return [
        (el.text_content() or "").strip()
        for el in page.locator(f"{_SHEET} {selector}").all()
    ]


def test_the_layers_menu_offers_the_manage_downloads_row(
    page: Page, live_server: LiveServer
) -> None:
    """The only way in — and it is an action row, not a toggle.

    Every other row in this menu is a ``menuitemcheckbox`` or
    ``menuitemradio``. This one performs a one-shot action and must never
    carry ``aria-checked``, or a screen reader would announce a checked
    state that means nothing.
    """
    _boot(page, live_server)
    page.click(_MENU_TOGGLE)

    row = page.locator(_MENU_ROW)
    expect(row).to_be_visible()
    expect(row).to_have_attribute("role", "menuitem")
    assert row.get_attribute("aria-checked") is None


def test_opening_the_sheet_lists_each_area_with_its_name_and_size(
    page: Page, live_server: LiveServer
) -> None:
    """The itemised list the ticket exists to provide, largest first."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    _open_sheet(page)

    # Largest first — the axis a user deciding what to remove cares about.
    assert _row_texts(page, "[data-row-size]") == ["123 MB", "41.0 MB"]

    labels = _row_texts(page, "[data-row-label]")
    assert labels[0] == "Custom area"
    # The region resolves to a real name through the FEATURE_BY_REGION_ID
    # bridge, not to its id.
    assert labels[1] == "Aletsch"


def test_the_sheet_states_the_running_total_against_the_budget(
    page: Page, live_server: LiveServer
) -> None:
    """The "what is it costing me" figure, in words and not only as a bar."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area(), budget_mb=500)
    _open_sheet(page)

    expect(page.locator(f"{_SHEET} [data-downloads-summary]")).to_have_text(
        "164 MB of 500 MB used"
    )


def test_opening_the_sheet_closes_the_layers_menu(
    page: Page, live_server: LiveServer
) -> None:
    """The menu would otherwise sit over the sheet it just opened."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    _open_sheet(page)

    expect(page.locator("#basemap-menu")).to_be_hidden()
    expect(page.locator(_MENU_TOGGLE)).to_have_attribute("aria-expanded", "false")


def test_a_device_with_no_downloads_says_so(
    page: Page, live_server: LiveServer
) -> None:
    """The state every user is in before their first download."""
    _boot(page, live_server)
    _open_sheet(page)

    expect(page.locator(f"{_SHEET} [data-downloads-empty]")).to_be_visible()
    assert _row_texts(page, "[data-row-label]") == []


def test_removing_an_area_deletes_its_whole_cache_bucket(
    page: Page, live_server: LiveServer
) -> None:
    """The load-bearing test of the whole ticket.

    Before SNOW-586's per-area buckets there was no delete path for a
    region download at all, and the custom area's eviction re-derived a URL
    list and deleted entry by entry — which could leave an area perforated
    rather than gone. Removal has to be atomic and has to take the WHOLE
    bucket, leaving every other area untouched.
    """
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    assert _pinned_buckets(page) == [
        f"{_PINNED_PREFIX}custom",
        f"{_PINNED_PREFIX}region-CH-4115",
    ]

    _open_sheet(page)
    page.on("dialog", lambda dialog: dialog.accept())
    # Rows are largest-first, so the first Remove is the custom area's.
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)
    assert _pinned_buckets(page) == [f"{_PINNED_PREFIX}region-CH-4115"]
    assert _stored_area_ids(page) == ["region-CH-4115"]


def test_removing_an_area_restates_the_total(
    page: Page, live_server: LiveServer
) -> None:
    """The running total is the reason to remove something; it must move."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area(), budget_mb=500)
    _open_sheet(page)

    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-downloads-summary]")).to_have_text(
        "41.0 MB of 500 MB used"
    )


def test_declining_the_confirmation_removes_nothing(
    page: Page, live_server: LiveServer
) -> None:
    """A misclick must not cost a 123 MB download over a slow connection."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    _open_sheet(page)

    page.on("dialog", lambda dialog: dialog.dismiss())
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(2)
    assert len(_pinned_buckets(page)) == 2
    assert sorted(_stored_area_ids(page)) == sorted(["custom", "region-CH-4115"])


def test_the_confirmation_names_the_area_and_the_space_it_frees(
    page: Page, live_server: LiveServer
) -> None:
    """So the choice can be judged before it is made.

    Also guards the whitespace collapsing in ``map_downloads_manager.js``:
    ``djangofmt`` reflows the long ``blocktrans`` across lines, which would
    otherwise put source indentation into the middle of this dialog.
    """
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    _open_sheet(page)

    messages: list[str] = []

    def _capture(dialog: Any) -> None:
        messages.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", _capture)
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()
    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(2)

    assert len(messages) == 1
    assert "Custom area" in messages[0]
    assert "123 MB" in messages[0]
    assert "  " not in messages[0]
    assert "\n" not in messages[0]


def test_changing_the_budget_persists_it_and_restates_the_total(
    page: Page, live_server: LiveServer
) -> None:
    """The budget SNOW-586's eviction planner reads is the one written here."""
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area(), budget_mb=500)
    _open_sheet(page)

    page.select_option(f"{_SHEET} [data-downloads-budget]", "1000")

    expect(page.locator(f"{_SHEET} [data-downloads-summary]")).to_have_text(
        "164 MB of 1000 MB used"
    )
    stored = page.evaluate(
        "async () => (await window.pwaDb.get('meta:app', 'basemap.budgetMb'))?.value"
    )
    assert stored == 1000


def test_being_over_budget_is_stated_rather_than_silently_clamped(
    page: Page, live_server: LiveServer
) -> None:
    """Reachable by lowering the budget under what is already held.

    Allowed deliberately — refusing would leave a user on a full device
    unable to say how much room they are willing to give up — so the sheet
    has to be able to say it out loud. The bar's percentage is clamped to
    100 and cannot.
    """
    _boot(page, live_server)
    # 190 + 41 MB, against the smallest offered budget of 200 MB. An
    # ordinary pair: the per-run ceiling is 200 MB, so two real downloads
    # can exceed the floor budget between them without either being unusual.
    _seed(page, _regions(), _custom_area(mb=190), budget_mb=500)
    _open_sheet(page)
    expect(page.locator(f"{_SHEET} [data-downloads-over]")).to_be_hidden()

    page.select_option(f"{_SHEET} [data-downloads-budget]", "200")

    expect(page.locator(f"{_SHEET} [data-downloads-over]")).to_be_visible()


def test_the_sheet_opens_and_lists_correctly_while_offline(
    page: Page, live_server: LiveServer
) -> None:
    """The case the ticket exists for — storage pressure is felt offline.

    Everything the sheet needs is already local: the record is IndexedDB,
    the sizes are stored figures rather than a measurement, and the region
    names come from ``FEATURE_BY_REGION_ID``, which the regions fetch
    populated before the network went away. Nothing here is allowed to
    require a request.
    """
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())

    page.context.set_offline(True)
    try:
        _open_sheet(page)
        assert _row_texts(page, "[data-row-size]") == ["123 MB", "41.0 MB"]
        labels = _row_texts(page, "[data-row-label]")
        assert labels[1] == "Aletsch"

        # And deleting still works — it is all local too.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator(f"{_SHEET} [data-downloads-delete]").first.click()
        expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)
        assert _pinned_buckets(page) == [f"{_PINNED_PREFIX}region-CH-4115"]
    finally:
        page.context.set_offline(False)


def test_the_sheet_reflects_downloads_made_since_it_was_last_open(
    page: Page, live_server: LiveServer
) -> None:
    """It re-reads the record on every open rather than caching the DOM.

    Downloads land and SNOW-586 evicts while this sheet is closed, so a
    body kept between opens is a stale row waiting to be shown.
    """
    _boot(page, live_server)
    _seed(page, _regions())
    _open_sheet(page)
    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)

    page.click(f"{_SHEET} [data-action='dismiss']")
    expect(page.locator(_SHEET)).to_be_hidden()

    _seed(page, _regions(), _custom_area())
    _open_sheet(page)
    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(2)


def test_an_unresolvable_region_is_still_listed_and_still_removable(
    page: Page, live_server: LiveServer
) -> None:
    """A download the user cannot see is the bug this ticket is fixing.

    A download recorded before names were stored, or one whose region was
    retired upstream, has no name to show. It falls back to its id rather
    than to nothing, so the bytes it holds stay visible and reclaimable.
    """
    _boot(page, live_server)
    orphan = [
        {
            "region_id": "CH-9999",
            "band": [10, 14],
            "z": {"10": [1, 1, 1, 1]},
            "bytes": 12 * _MB,
            "savedAt": "2026-08-01T10:00:00.000Z",
        }
    ]
    _seed(page, orphan)
    _open_sheet(page)

    assert _row_texts(page, "[data-row-label]") == ["CH-9999"]

    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-downloads-empty]")).to_be_visible()
    assert _pinned_buckets(page) == []


def test_the_copy_says_the_downloads_are_device_local(
    page: Page, live_server: LiveServer
) -> None:
    """The budget is per-browser, and the sheet must not imply otherwise.

    A signed-in user with a phone and a laptop has two independent sets of
    downloads and two independent budgets. Copy that read as account-level
    would be a straightforward lie, which is why the ticket calls it out.
    """
    _boot(page, live_server)
    _seed(page, _regions(), _custom_area())
    _open_sheet(page)

    sheet_text = page.locator(_SHEET).text_content() or ""
    assert "this device" in sheet_text.lower()


def test_a_real_download_is_listed_and_can_be_removed(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """End to end through the REAL writer — the test seeding cannot replace.

    Every other test in this file writes the record itself, so all of them
    would keep passing if the download control started writing a different
    key, a different field name, or a differently-derived id. That is not
    hypothetical: this sheet shipped its first version reading a
    ``basemap.areas`` row that nothing has ever written, and a full suite of
    green seeded tests said nothing about it.

    So this one downloads a region for real — the actual roundel, the actual
    ``_recordRegionDownload``, the actual per-area bucket written by the
    warm-cache round trip — then opens the sheet and works entirely from
    what that produced: the region's name, a non-zero size, and a delete
    that takes the bucket with it.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)

    # A real region, with a real display name — so "the sheet shows the
    # name" means the name travelled writer -> record -> sheet, rather than
    # the sheet falling back to the id and looking the same.
    page.evaluate(
        """({ regionId, name, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, name, download },
                geometry,
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: name },
            }));
        }""",
        {
            "regionId": "CH-4115",
            "name": "Aletsch",
            "download": _MICRO_SUMMARY,
            "geometry": _GEOMETRY,
        },
    )
    _wait_for_state(page, "idle")

    # 41 MB, so the size shown is unmistakably the run's own figure.
    _stub_warm_cache(worker, ok=3, failed=0, bytes_total=41 * _MB)
    page.click("#map-download-control")
    _wait_for_state(page, "done")

    # The record the REAL writer produced.
    assert _stored_area_ids(page) == ["region-CH-4115"]

    _open_sheet(page)
    assert _row_texts(page, "[data-row-label]") == ["Aletsch"]
    assert _row_texts(page, "[data-row-size]") == ["41.0 MB"]
    expect(page.locator(f"{_SHEET} [data-downloads-summary]")).to_have_text(
        "41.0 MB of 500 MB used"
    )
    assert _pinned_buckets(page) == [f"{_PINNED_PREFIX}region-CH-4115"]

    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-downloads-empty]")).to_be_visible()
    assert _pinned_buckets(page) == []
    assert _stored_area_ids(page) == []
