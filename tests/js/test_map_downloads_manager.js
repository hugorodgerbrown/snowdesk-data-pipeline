/*
 * tests/js/test_map_downloads_manager.js — Vitest DOM tests for
 * static/js/map_downloads_manager.js (SNOW-588).
 *
 * The "Manage downloads" sheet: what it lists, what delete actually
 * removes, and what the budget control persists. Follows the established
 * fixture convention (test_map_help.js, test_overlays.js) — write the real
 * template's markup into ``document.body``, then ``vi.resetModules()`` +
 * ``await import()`` so each test runs the module's IIFE fresh against its
 * own DOM and its own IndexedDB state.
 *
 * The fixture below mirrors
 * apps/public/templates/public/partials/_map_downloads_sheet.html. It is a
 * hand-copy, which is the standing trade-off for this harness: Vitest
 * cannot render a Django template, so the alternative is not a better
 * fixture but no DOM coverage at all. Anything structural asserted here —
 * the ``data-`` hooks, the template ids — is also exercised end-to-end
 * against the REAL template by tests/e2e/test_manage_downloads.py, which is
 * what catches the two drifting apart.
 *
 * ``caches`` does not exist in jsdom, so a minimal stub stands in. It is
 * the surface this module actually uses (``delete``), and asserting on it
 * is the point: the load-bearing behaviour of delete is that it removes
 * the AREA'S CACHE BUCKET, not merely its row.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MB = 1024 * 1024;

/** Markup mirroring _map_downloads_sheet.html plus the menu row. */
function buildFixture() {
  document.body.innerHTML = `
    <button type="button" role="menuitem"
            class="basemap-menu-item basemap-menu-item--action"
            data-menu-action="manage-downloads">Manage downloads…</button>
    <div id="map-downloads-sheet" hidden tabindex="-1" data-overlay
         aria-label="Downloads on this device"></div>
    <template id="map-downloads-body-template">
      <div>
        <div><span>Downloads on this device</span>
          <button type="button" data-action="dismiss">×</button></div>
        <p data-downloads-summary></p>
        <div aria-hidden="true">
          <div data-downloads-bar class="h-full rounded-pill bg-text-2" style="width: 0%"></div>
        </div>
        <p data-downloads-over hidden>You're over your budget.</p>
        <ul data-downloads-list></ul>
        <p data-downloads-empty hidden>You haven't downloaded any areas yet.</p>
        <div>
          <label for="map-downloads-budget">Storage budget</label>
          <select id="map-downloads-budget" data-downloads-budget></select>
        </div>
      </div>
    </template>
    <template id="map-downloads-row-template">
      <li>
        <span>
          <span data-row-label></span>
          <span data-row-kind></span>
        </span>
        <span>
          <span data-row-size></span>
          <button type="button" data-downloads-delete>Remove</button>
        </span>
      </li>
    </template>
    <template id="map-downloads-strings-template">
      <span data-string="kind-region">Region</span>
      <span data-string="kind-custom">Custom area</span>
      <span data-string="usage">%(used)s of %(budget)s used</span>
      <span data-string="confirm-remove">Remove the offline map for %(name)s? This
            frees %(size)s. You can download it again when you're back online.</span>
      <span data-string="remove-failed">That download couldn't be removed. Try again.</span>
    </template>
  `;
}

/**
 * In-memory stand-in for `meta:app`, seeded with the records main
 * ACTUALLY writes.
 *
 * `basemap.regions` (an array, one entry per downloaded region, keyed by
 * `region_id`) and `basemap.customArea` (at most one row) are the real
 * shapes written by map.js's two download controls — see
 * `_recordRegionDownload` / `_persistSavedArea` there. Seeding anything
 * else would make these tests assert that the sheet renders a synthetic
 * record correctly, which is exactly how an earlier version of this file
 * stayed green while the sheet was reading a key nothing wrote.
 */
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
  window.pwaDb = {
    get: vi.fn(async (_store, key) =>
      rows.has(key) ? { key, value: rows.get(key) } : undefined,
    ),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
      return row.key;
    }),
    delete: vi.fn(async (_store, key) => {
      rows.delete(key);
    }),
  };
  return rows;
}

/**
 * The real `window.pwaBasemapDownloads` bridge, reimplemented over the db
 * stub exactly as map.js implements it (`basemapDownloadedAreas` /
 * `evictBasemapAreas`).
 *
 * This mirrors production rather than stubbing it away, so these tests
 * still exercise the two-records-into-one-list normalisation and the
 * id-to-bucket mapping that the sheet depends on and does not itself own.
 */
function installDownloadsBridge(rows, cachesStub) {
  // Resolved per call, not at install time: tests seed before they import
  // the modules, so at this point pwaBasemapDownloadCore does not exist yet.
  const getCore = () => self.pwaBasemapDownloadCore;
  const areas = async () => {
    const core = getCore();
    const out = [];
    for (const entry of rows.get('basemap.regions') || []) {
      if (!entry || !entry.region_id) continue;
      out.push({
        id: core.areaIdForRegion(entry.region_id),
        name: entry.name || entry.region_id,
        bytes: Number(entry.bytes) || 0,
        savedAt: entry.savedAt,
      });
    }
    const custom = rows.get('basemap.customArea');
    if (custom && Array.isArray(custom.bbox)) {
      out.push({
        id: core.CUSTOM_AREA_ID,
        name: custom.name || core.CUSTOM_AREA_ID,
        bytes: Number(custom.bytes) || 0,
        savedAt: custom.savedAt,
      });
    }
    return out;
  };
  window.pwaBasemapDownloads = {
    areas: vi.fn(areas),
    evict: vi.fn(async (ids) => {
      const core = getCore();
      for (const areaId of ids) {
        await cachesStub.delete(core.pinnedCacheName(areaId));
        if (areaId === core.CUSTOM_AREA_ID) {
          rows.delete('basemap.customArea');
        } else {
          const existing = rows.get('basemap.regions') || [];
          rows.set(
            'basemap.regions',
            existing.filter(
              (e) => !(e && core.areaIdForRegion(e.region_id) === areaId),
            ),
          );
        }
      }
      // map.js's evictBasemapAreas does this itself (SNOW-570), which is
      // why map_downloads_manager.js no longer calls it.
      window.pwaDownloadedOverlay?.refresh();
    }),
  };
}

/** Minimal Cache Storage stub — only `delete` is used by this module. */
function installCachesStub(existing) {
  const names = new Set(existing || []);
  const stub = {
    names,
    delete: vi.fn(async (name) => names.delete(name)),
  };
  // jsdom has no `caches`; define it rather than stubGlobal so the
  // module's `'caches' in window` guard sees it.
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

async function loadModule() {
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_manage_core.js');
  await import('../../static/js/map_downloads_manager.js');
}

// The two records main actually writes. `basemap.regions` entries carry
// `region_id` (NOT the bucket id — that is `areaIdForRegion(region_id)`),
// plus the `name` and `bytes` the download itself recorded.
const REGIONS = [
  { region_id: 'CH-2101', name: 'Aletsch', band: [10, 14], bytes: 40 * MB, savedAt: '2026-08-01T10:00:00.000Z' },
];
const CUSTOM_AREA = {
  bbox: [7.9, 46.4, 8.1, 46.6],
  band: [10, 14],
  centre_tile: { z: 14, x: 8580, y: 5810 },
  bytes: 120 * MB,
  savedAt: '2026-08-02T10:00:00.000Z',
};

/**
 * Seed `meta:app` and wire the bridge over it, in one step — the two are
 * never useful apart, and forgetting the bridge would silently give the
 * sheet an empty device.
 */
function seed(initial) {
  const rows = installDbStub(initial);
  installDownloadsBridge(rows, window.caches);
  return rows;
}

function openSheet() {
  document.querySelector('[data-menu-action="manage-downloads"]').click();
}

/** Let the module's async render settle. */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function rowLabels() {
  return Array.from(
    document.querySelectorAll('#map-downloads-sheet [data-row-label]'),
  ).map((el) => el.textContent);
}

beforeEach(() => {
  buildFixture();
  installCachesStub([
    'snowdesk-basemap-pinned-region-CH-2101',
    'snowdesk-basemap-pinned-custom',
  ]);
  window.pwaLayersMenu = { close: vi.fn() };
  window.pwaDownloadedOverlay = { refresh: vi.fn() };
  window.pwaLayerSyncStatus = { refresh: vi.fn() };
  vi.stubGlobal('confirm', vi.fn(() => true));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaDownloadsManager;
  delete window.pwaBasemapDownloads;
});

describe('opening the sheet', () => {
  it('renders one row per stored area, largest first, with names and sizes', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    const sheet = document.getElementById('map-downloads-sheet');
    expect(sheet.hidden).toBe(false);
    expect(rowLabels()).toEqual(['Custom area', 'Aletsch']);
    expect(
      Array.from(sheet.querySelectorAll('[data-row-size]')).map((el) => el.textContent),
    ).toEqual(['120 MB', '40.0 MB']);
  });

  it('states the running total against the budget', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    expect(
      document.querySelector('[data-downloads-summary]').textContent,
    ).toBe('160 MB of 500 MB used');
  });

  it('closes the layers menu it was opened from', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA });
    await loadModule();
    openSheet();
    await settle();

    expect(window.pwaLayersMenu.close).toHaveBeenCalled();
  });

  it('shows the empty state, and no rows, on a device with no downloads', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    expect(rowLabels()).toEqual([]);
    expect(document.querySelector('[data-downloads-empty]').hidden).toBe(false);
  });

  it('hides the empty state once there is something to list', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('[data-downloads-empty]').hidden).toBe(true);
  });

  it('falls back to the region id when the record stored no name', async () => {
    // A download recorded before names were stored, or one whose region
    // was retired upstream. The row must still be listed — a download the
    // user cannot see is the bug this ticket exists to fix.
    seed({ 'basemap.regions': [{ ...REGIONS[0], name: undefined }] });
    await loadModule();
    openSheet();
    await settle();

    expect(rowLabels()).toEqual(['CH-2101']);
  });

  it('reports an empty device when the downloads bridge is absent', async () => {
    // map.js owns the records; without it there is genuinely nothing this
    // surface can see, and saying so beats throwing on open.
    installDbStub({ 'basemap.budgetMb': 500 });
    delete window.pwaBasemapDownloads;
    await loadModule();
    openSheet();
    await settle();

    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);
    expect(document.querySelector('[data-downloads-empty]').hidden).toBe(false);
  });

  it('re-reads the record on every open rather than caching the DOM', async () => {
    // Downloads and evictions both happen while this sheet is closed, so a
    // second open must reflect them.
    const rows = seed({ 'basemap.regions': REGIONS });
    await loadModule();
    openSheet();
    await settle();
    expect(rowLabels()).toEqual(['Aletsch']);

    rows.set('basemap.customArea', CUSTOM_AREA);
    openSheet();
    await settle();
    expect(rowLabels()).toEqual(['Custom area', 'Aletsch']);
  });
});

describe('the budget readout', () => {
  it('offers every choice the core defines, with the stored one selected', async () => {
    seed({ 'basemap.budgetMb': 1000 });
    await loadModule();
    openSheet();
    await settle();

    const select = document.querySelector('[data-downloads-budget]');
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      '200', '500', '1000', '2000',
    ]);
    expect(select.value).toBe('1000');
  });

  it('defaults to 500 MB when no budget has been stored', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('[data-downloads-budget]').value).toBe('500');
  });

  it('persists a change to meta:app and restates the total against it', async () => {
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    const select = document.querySelector('[data-downloads-budget]');
    select.value = '1000';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();

    expect(rows.get('basemap.budgetMb')).toBe(1000);
    expect(
      document.querySelector('[data-downloads-summary]').textContent,
    ).toBe('160 MB of 1000 MB used');
  });

  it('says so when stored downloads exceed the budget', async () => {
    // Reachable by lowering the budget under what is already held —
    // allowed deliberately, so the surface has to be able to state it.
    // 190 + 40 MB against the smallest offered budget (200 MB), which is
    // an ordinary pair of downloads: the per-run ceiling is 200 MB, so two
    // real regions can exceed the floor budget between them.
    seed({
      'basemap.regions': REGIONS,
      'basemap.customArea': { ...CUSTOM_AREA, bytes: 190 * MB },
      'basemap.budgetMb': 200,
    });
    await loadModule();
    openSheet();
    await settle();

    const bar = document.querySelector('[data-downloads-bar]');
    expect(document.querySelector('[data-downloads-over]').hidden).toBe(false);
    expect(bar.classList.contains('bg-status-error-text')).toBe(true);
    expect(bar.classList.contains('bg-text-2')).toBe(false);
  });

  it('keeps the over-budget line hidden while under budget', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('[data-downloads-over]').hidden).toBe(true);
    expect(
      document.querySelector('[data-downloads-bar]').classList.contains('bg-text-2'),
    ).toBe(true);
  });
});

describe('deleting an area', () => {
  async function openAndDeleteFirst() {
    openSheet();
    await settle();
    document.querySelector('[data-downloads-delete]').click();
    await settle();
  }

  it("deletes the area's whole cache bucket, not just its row", async () => {
    // The load-bearing assertion. Per-area buckets (SNOW-586) are what make
    // removal atomic — the alternative it replaced re-derived a URL list and
    // deleted entry by entry, which could leave an area perforated.
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.caches.delete).toHaveBeenCalledWith(
      'snowdesk-basemap-pinned-custom',
    );
    expect(window.caches.names.has('snowdesk-basemap-pinned-custom')).toBe(false);
    // The other area is untouched.
    expect(window.caches.names.has('snowdesk-basemap-pinned-region-CH-2101')).toBe(true);
  });

  it('drops only that area from the record', async () => {
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions').map((e) => e.region_id)).toEqual(['CH-2101']);
    expect(rows.get('basemap.customArea')).toBeUndefined();
  });

  it('re-renders, and restates the total without the removed area', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(rowLabels()).toEqual(['Aletsch']);
    expect(
      document.querySelector('[data-downloads-summary]').textContent,
    ).toBe('40.0 MB of 500 MB used');
  });

  it("refreshes the map's own views of the cache it just changed", async () => {
    // Otherwise the downloaded-areas overlay keeps ringing an area with no
    // tiles behind it, and the layers-menu dots keep claiming it's offline-ready.
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.pwaDownloadedOverlay.refresh).toHaveBeenCalled();
    expect(window.pwaLayerSyncStatus.refresh).toHaveBeenCalled();
  });

  it('names the area and the space freed in its confirmation', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    const message = window.confirm.mock.calls[0][0];
    expect(message).toContain('Custom area');
    expect(message).toContain('120 MB');
    // The template's blocktrans is reflowed across lines by djangofmt;
    // the module collapses that whitespace, so no source indentation
    // reaches the dialog.
    expect(message).not.toMatch(/\s{2,}/);
  });

  it('removes nothing when the confirmation is declined', async () => {
    window.confirm.mockReturnValue(false);
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.caches.delete).not.toHaveBeenCalled();
    expect(rows.get('basemap.regions')).toHaveLength(1);
    expect(rows.get('basemap.customArea')).toBeDefined();
    expect(rowLabels()).toEqual(['Custom area', 'Aletsch']);
  });

  it('keeps the record intact when the bucket delete throws', async () => {
    // The record is what still points at those bytes. Dropping it on a
    // failed delete would strand tiles on disk that no surface could then
    // account for or remove.
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    window.caches.delete.mockRejectedValue(new Error('storage error'));
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions')).toHaveLength(1);
    expect(rows.get('basemap.customArea')).toBeDefined();
    const button = document.querySelector('[data-downloads-delete]');
    expect(button.textContent).toBe("That download couldn't be removed. Try again.");
    expect(button.hasAttribute('disabled')).toBe(true);
  });

  it('still drops the record entry when the bucket was already gone', async () => {
    // `caches.delete` resolving false means the bucket wasn't there — the
    // tiles are gone either way, so the record must not keep listing it.
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customArea': CUSTOM_AREA, 'basemap.budgetMb': 500 });
    await loadModule();
    window.caches.delete.mockResolvedValue(false);
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions').map((e) => e.region_id)).toEqual(['CH-2101']);
    expect(rows.get('basemap.customArea')).toBeUndefined();
  });
});

describe('resilience', () => {
  it('opens and reports an empty device when IndexedDB is unreadable', async () => {
    // db.js rejects with reset_required / indexeddb_unavailable in private
    // mode or after a blocked upgrade. A manage surface that throws on open
    // is worse than one that says it can see nothing.
    window.pwaDb = { get: vi.fn(async () => { throw new Error('reset_required'); }) };
    await loadModule();
    openSheet();
    await settle();

    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);
    expect(document.querySelector('[data-downloads-empty]').hidden).toBe(false);
  });

  it('does nothing at all when the sheet markup is absent', async () => {
    // Every page but the map: the module is only loaded there today, but
    // it must not throw if that ever changes.
    document.body.innerHTML = '';
    seed({});
    await expect(loadModule()).resolves.not.toThrow();
    expect(window.pwaDownloadsManager).toBeUndefined();
  });
});
