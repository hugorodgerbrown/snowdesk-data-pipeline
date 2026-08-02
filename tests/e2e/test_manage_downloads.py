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

**Why the plain ``page``/``live_server`` fixtures, not ``pwa_page``.** Same
reason as ``test_downloaded_areas_overlay.py``: with the real service worker
controlling, the basemap style's sources never resolve against the
unreachable CDN, so ``map.on('load')`` never fires. This surface never talks
to a service worker — it reads IndexedDB and Cache Storage, both available
to the page directly — so nothing is lost by dropping it, and the region
names it needs (``FEATURE_BY_REGION_ID``, via the ``window.pwaRegionNames``
bridge) are populated by the regions fetch either way.

The areas are seeded directly rather than downloaded. A real download needs
a reachable tile CDN, and what is under test here is the surface that reads
the record — so writing the record and its matching cache buckets by hand
is both faster and far more precise: sizes, names and the over-budget case
are all chosen by the test.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_MENU_TOGGLE = "#basemap-toggle"
_MENU_ROW = '[data-menu-action="manage-downloads"]'
_SHEET = "#map-downloads-sheet"
_PINNED_PREFIX = "snowdesk-basemap-pinned-"
# The pre-SNOW-586 single pinned cache. Still opened by the page on load
# until that ticket lands; never an area, so never listed by this surface.
_LEGACY_PINNED_CACHE = "snowdesk-basemap-pinned-v1"

_MB = 1024 * 1024


def _areas(
    region_id: str, *, region_mb: int = 41, custom_mb: int = 123
) -> list[dict[str, Any]]:
    """One region download plus the one custom area.

    ``region_id`` is read off the loaded map rather than hardcoded — the
    point of the region row is that its name RESOLVES, which only a region
    actually present in ``FEATURE_BY_REGION_ID`` can demonstrate.
    """
    return [
        {
            "id": f"region-{region_id}",
            "kind": "region",
            "region_id": region_id,
            "bytes": region_mb * _MB,
            "savedAt": "2026-08-01T10:00:00.000Z",
        },
        {
            "id": "custom",
            "kind": "custom",
            "bytes": custom_mb * _MB,
            "savedAt": "2026-08-02T10:00:00.000Z",
        },
    ]


def _boot(page: Page, live_server: LiveServer) -> None:
    """Navigate and wait for the record bridge, sheet module and regions.

    The regions wait matters: region names come from
    ``FEATURE_BY_REGION_ID``, which the regions GeoJSON fetch populates
    after load. Without it, a name-resolution assertion would race the
    fetch and fail for a reason that has nothing to do with the sheet.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        """() => typeof window.pwaDownloadsManager === 'object'
            && typeof window.pwaDb === 'object'
            && typeof window.pwaRegionNames === 'object'
            && typeof FEATURE_BY_REGION_ID !== 'undefined'
            && Object.keys(FEATURE_BY_REGION_ID).length > 0""",
        timeout=30000,
    )


def _a_loaded_region(page: Page) -> tuple[str, str]:
    """A real ``(region_id, name)`` pair from the loaded map."""
    return tuple(  # type: ignore[return-value]
        page.evaluate(
            """() => {
                const id = Object.keys(FEATURE_BY_REGION_ID).sort()[0];
                return [id, FEATURE_BY_REGION_ID[id].properties.name];
            }"""
        )
    )


def _seed(page: Page, areas: list[dict[str, Any]], budget_mb: int = 500) -> None:
    """Write the ``basemap.areas`` record plus one cache bucket per area.

    The buckets are what a delete has to remove, so they are created for
    real — an empty ``Cache`` is still a ``Cache``, and ``caches.keys()``
    lists it, which is all the assertions need.
    """
    page.evaluate(
        """async ({areas, budgetMb, prefix}) => {
            await window.pwaDb.put('meta:app', {key: 'basemap.areas', value: areas});
            await window.pwaDb.put('meta:app', {key: 'basemap.budgetMb', value: budgetMb});
            for (const area of areas) await caches.open(prefix + area.id);
        }""",
        {"areas": areas, "budgetMb": budget_mb, "prefix": _PINNED_PREFIX},
    )


def _open_sheet(page: Page) -> None:
    """Open the sheet the way a user does — through the layers menu."""
    page.click(_MENU_TOGGLE)
    page.click(_MENU_ROW)
    expect(page.locator(_SHEET)).to_be_visible()


def _pinned_buckets(page: Page) -> list[str]:
    """Every PER-AREA pinned basemap cache bucket on the device.

    ``snowdesk-basemap-pinned-v1`` — the single undifferentiated pinned
    cache that predates SNOW-586's per-area buckets — is excluded. The page
    still opens it on load until that ticket lands and drops it, and it is
    not an area, so counting it would make every assertion here off by one
    for a reason that has nothing to do with this surface. Remove this
    filter once SNOW-586 has merged and the legacy cache is gone.
    """
    return sorted(
        page.evaluate(
            """async ({prefix, legacy}) => (await caches.keys())
                .filter(n => n.startsWith(prefix) && n !== legacy)""",
            {"prefix": _PINNED_PREFIX, "legacy": _LEGACY_PINNED_CACHE},
        )
    )


def _stored_area_ids(page: Page) -> list[str]:
    """The ids in the ``basemap.areas`` record, as stored."""
    return page.evaluate(
        """async () => {
            const row = await window.pwaDb.get('meta:app', 'basemap.areas');
            return (row?.value || []).map(a => a.id);
        }"""
    )


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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
    _open_sheet(page)

    # Largest first — the axis a user deciding what to remove cares about.
    assert _row_texts(page, "[data-row-size]") == ["123 MB", "41.0 MB"]

    labels = _row_texts(page, "[data-row-label]")
    assert labels[0] == "Custom area"
    # The region resolves to a real name through the FEATURE_BY_REGION_ID
    # bridge, not to its id.
    assert labels[1] == region_name


def test_the_sheet_states_the_running_total_against_the_budget(
    page: Page, live_server: LiveServer
) -> None:
    """The "what is it costing me" figure, in words and not only as a bar."""
    _boot(page, live_server)
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id), budget_mb=500)
    _open_sheet(page)

    expect(page.locator(f"{_SHEET} [data-downloads-summary]")).to_have_text(
        "164 MB of 500 MB used"
    )


def test_opening_the_sheet_closes_the_layers_menu(
    page: Page, live_server: LiveServer
) -> None:
    """The menu would otherwise sit over the sheet it just opened."""
    _boot(page, live_server)
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
    assert _pinned_buckets(page) == [
        f"{_PINNED_PREFIX}custom",
        f"{_PINNED_PREFIX}region-{region_id}",
    ]

    _open_sheet(page)
    page.on("dialog", lambda dialog: dialog.accept())
    # Rows are largest-first, so the first Remove is the custom area's.
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)
    assert _pinned_buckets(page) == [f"{_PINNED_PREFIX}region-{region_id}"]
    assert _stored_area_ids(page) == [f"region-{region_id}"]


def test_removing_an_area_restates_the_total(
    page: Page, live_server: LiveServer
) -> None:
    """The running total is the reason to remove something; it must move."""
    _boot(page, live_server)
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id), budget_mb=500)
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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
    _open_sheet(page)

    page.on("dialog", lambda dialog: dialog.dismiss())
    page.locator(f"{_SHEET} [data-downloads-delete]").first.click()

    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(2)
    assert len(_pinned_buckets(page)) == 2
    assert sorted(_stored_area_ids(page)) == sorted(["custom", f"region-{region_id}"])


def test_the_confirmation_names_the_area_and_the_space_it_frees(
    page: Page, live_server: LiveServer
) -> None:
    """So the choice can be judged before it is made.

    Also guards the whitespace collapsing in ``map_downloads_manager.js``:
    ``djangofmt`` reflows the long ``blocktrans`` across lines, which would
    otherwise put source indentation into the middle of this dialog.
    """
    _boot(page, live_server)
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id), budget_mb=500)
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
    region_id, _ = _a_loaded_region(page)
    # 190 + 41 MB, against the smallest offered budget of 200 MB. An
    # ordinary pair: the per-run ceiling is 200 MB, so two real downloads
    # can exceed the floor budget between them without either being unusual.
    _seed(page, _areas(region_id, custom_mb=190), budget_mb=500)
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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))

    page.context.set_offline(True)
    try:
        _open_sheet(page)
        assert _row_texts(page, "[data-row-size]") == ["123 MB", "41.0 MB"]
        labels = _row_texts(page, "[data-row-label]")
        assert labels[1] == region_name

        # And deleting still works — it is all local too.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator(f"{_SHEET} [data-downloads-delete]").first.click()
        expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)
        assert _pinned_buckets(page) == [f"{_PINNED_PREFIX}region-{region_id}"]
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
    region_id, _ = _a_loaded_region(page)
    _seed(page, _areas(region_id)[:1])
    _open_sheet(page)
    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(1)

    page.click(f"{_SHEET} [data-action='dismiss']")
    expect(page.locator(_SHEET)).to_be_hidden()

    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
    _open_sheet(page)
    expect(page.locator(f"{_SHEET} [data-row-label]")).to_have_count(2)


def test_an_unresolvable_region_is_still_listed_and_still_removable(
    page: Page, live_server: LiveServer
) -> None:
    """A download the user cannot see is the bug this ticket is fixing.

    A region retired upstream (or a ``regions.geojson`` that never loaded)
    has no name to show. It falls back to its id rather than to nothing, so
    the bytes it is holding stay visible and reclaimable.
    """
    _boot(page, live_server)
    orphan = [
        {
            "id": "region-CH-9999",
            "kind": "region",
            "region_id": "CH-9999",
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
    region_id, region_name = _a_loaded_region(page)
    _seed(page, _areas(region_id))
    _open_sheet(page)

    sheet_text = page.locator(_SHEET).text_content() or ""
    assert "this device" in sheet_text.lower()
