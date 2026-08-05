/*
 * tests/js/test_map_downloads_manager.js — Vitest DOM tests for
 * static/js/map_downloads_manager.js (SNOW-588; the way in rewritten by
 * SNOW-634; SNOW-635 adds Rename and lets more than one custom area be
 * listed).
 *
 * The "Manage downloads" sheet: what it lists, what delete actually
 * removes, what the budget control persists, (SNOW-634) the "Download a
 * custom area" add-trigger now inside the sheet, and (SNOW-635) renaming a
 * custom area plus more than one of them coexisting. Follows the
 * established fixture convention (test_map_help.js, test_overlays.js) —
 * write the real template's markup into ``document.body``, then
 * ``vi.resetModules()`` + ``await import()`` so each test runs the
 * module's IIFE fresh against its own DOM and its own IndexedDB state.
 *
 * The sheet itself is opened via ``window.pwaDownloadsManager.open()``
 * directly, not a DOM click — SNOW-634 moved the trigger
 * (``#map-custom-download-control``) into map.js, a module this file does
 * not load, so there is no DOM element here for a click to reach any more.
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
 *
 * The lazy migration from the legacy single-row ``basemap.customArea`` to
 * the array-shaped ``basemap.customAreas`` lives in map.js and is covered
 * directly there (tests/js/test_basemap_custom_areas.js) — this file's own
 * ``installDownloadsBridge`` seeds the ALREADY-migrated array shape, same
 * as every other record here mirrors what the real writer produces.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MB = 1024 * 1024;

/** Markup mirroring _map_downloads_sheet.html (SNOW-634: no menu row; SNOW-635: Rename). */
function buildFixture() {
  document.body.innerHTML = `
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
        <button type="button" data-downloads-add>Download a custom area</button>
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
          <button type="button" data-downloads-rename>Rename</button>
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
      <span data-string="add-offline">You're offline — connect to download a new area.</span>
      <span data-string="default-custom-name">Custom area %(n)s</span>
      <span data-string="rename-prompt">Name this area</span>
      <span data-string="rename-failed">That name couldn't be saved. Try again.</span>
    </template>
  `;
}

/**
 * In-memory stand-in for `meta:app`, seeded with the records main
 * ACTUALLY writes.
 *
 * `basemap.regions` (an array, one entry per downloaded region, keyed by
 * `region_id`) and (SNOW-635) `basemap.customAreas` (an array, one entry
 * per user-framed area) are the real shapes written by map.js's two
 * download controls — see `_recordRegionDownload` / `_appendCustomArea`
 * there. Seeding anything else would make these tests assert that the
 * sheet renders a synthetic record correctly, which is exactly how an
 * earlier version of this file stayed green while the sheet was reading a
 * key nothing wrote.
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
 * `evictBasemapAreas` / `renameCustomArea`).
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
    for (const entry of rows.get('basemap.customAreas') || []) {
      if (!entry || !entry.id || !Array.isArray(entry.bbox)) continue;
      out.push({
        id: entry.id,
        // SNOW-635: NOT defaulted — a custom area's name is set only by a
        // rename, so an unrenamed one carries none, matching map.js's own
        // basemapDownloadedAreas.
        name: entry.name,
        ordinal: entry.ordinal,
        bytes: Number(entry.bytes) || 0,
        savedAt: entry.savedAt,
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
        if (core.isCustomAreaId(areaId)) {
          const existing = rows.get('basemap.customAreas') || [];
          rows.set(
            'basemap.customAreas',
            existing.filter((e) => !(e && e.id === areaId)),
          );
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
    rename: vi.fn(async (areaId, name) => {
      const core = getCore();
      if (!core.isCustomAreaId(areaId)) return false;
      const existing = rows.get('basemap.customAreas') || [];
      if (!existing.some((e) => e && e.id === areaId)) return false;
      rows.set(
        'basemap.customAreas',
        existing.map((e) => (e && e.id === areaId ? { ...e, name } : e)),
      );
      return true;
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
// SNOW-635: `basemap.customAreas` is an array; this default fixture holds
// one UNRENAMED area (`name` absent, `ordinal: 1`), so its sheet label is
// the numbered default "Custom area 1" — the same as a real never-renamed
// download would show.
const CUSTOM_AREAS = [
  {
    id: 'custom-a1',
    ordinal: 1,
    bbox: [7.9, 46.4, 8.1, 46.6],
    band: [10, 14],
    centre_tile: { z: 14, x: 8580, y: 5810 },
    bytes: 120 * MB,
    savedAt: '2026-08-02T10:00:00.000Z',
  },
];

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
  // SNOW-634: the trigger that used to live in this module's own DOM
  // fixture is now #map-custom-download-control in map.js, which this
  // file does not load — its click handler is nothing but
  // `window.pwaDownloadsManager?.open()`, so calling the bridge directly
  // is the equivalent entry point.
  window.pwaDownloadsManager.open();
}

function setOnline(value) {
  Object.defineProperty(window.navigator, 'onLine', { value, configurable: true });
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
    'snowdesk-basemap-pinned-custom-a1',
  ]);
  window.pwaLayersMenu = { close: vi.fn() };
  window.pwaDownloadedOverlay = { refresh: vi.fn() };
  window.pwaLayerSyncStatus = { refresh: vi.fn() };
  // SNOW-634: window.pwaCustomAreaDownload — map.js's own bridge, the
  // add-trigger's online path calls it. window.MapSheet.toast — its
  // offline path calls that instead.
  window.pwaCustomAreaDownload = { openFraming: vi.fn(), refresh: vi.fn() };
  window.MapSheet = { toast: vi.fn() };
  vi.stubGlobal('confirm', vi.fn(() => true));
  vi.stubGlobal('prompt', vi.fn(() => 'Home run'));
  setOnline(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setOnline(true);
  delete window.pwaDb;
  delete window.pwaDownloadsManager;
  delete window.pwaBasemapDownloads;
  delete window.pwaCustomAreaDownload;
  delete window.MapSheet;
});

describe('opening the sheet', () => {
  it('renders one row per stored area, largest first, with names and sizes', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    const sheet = document.getElementById('map-downloads-sheet');
    expect(sheet.hidden).toBe(false);
    // SNOW-635: an unrenamed custom area's default label is numbered.
    expect(rowLabels()).toEqual(['Custom area 1', 'Aletsch']);
    expect(
      Array.from(sheet.querySelectorAll('[data-row-size]')).map((el) => el.textContent),
    ).toEqual(['120 MB', '40.0 MB']);
  });

  it('states the running total against the budget', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    expect(
      document.querySelector('[data-downloads-summary]').textContent,
    ).toBe('160 MB of 500 MB used');
  });

  it('closes the layers menu it was opened from', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
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
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
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

    rows.set('basemap.customAreas', CUSTOM_AREAS);
    openSheet();
    await settle();
    expect(rowLabels()).toEqual(['Custom area 1', 'Aletsch']);
  });

  it('lists more than one custom area at once', async () => {
    // SNOW-635: the whole point — a second download used to silently
    // replace the first.
    const second = {
      id: 'custom-b2',
      ordinal: 2,
      bbox: [7.0, 46.0, 7.2, 46.2],
      band: [10, 14],
      centre_tile: { z: 14, x: 8500, y: 5800 },
      bytes: 30 * MB,
      savedAt: '2026-08-03T10:00:00.000Z',
    };
    seed({ 'basemap.customAreas': [...CUSTOM_AREAS, second] });
    await loadModule();
    openSheet();
    await settle();

    expect(rowLabels()).toEqual(['Custom area 1', 'Custom area 2']);
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
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
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
      'basemap.customAreas': [{ ...CUSTOM_AREAS[0], bytes: 190 * MB }],
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
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('[data-downloads-over]').hidden).toBe(true);
    expect(
      document.querySelector('[data-downloads-bar]').classList.contains('bg-text-2'),
    ).toBe(true);
  });
});

describe('the add-trigger', () => {
  // SNOW-634: "Download a custom area" moved from the roundel's own click
  // into this sheet, as [data-downloads-add] above the list.

  it('hides the sheet and opens framing when online', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    document.querySelector('[data-downloads-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).toHaveBeenCalled();
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(true);
    expect(window.MapSheet.toast).not.toHaveBeenCalled();
  });

  it('toasts and leaves the sheet open when offline', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();
    setOnline(false);

    document.querySelector('[data-downloads-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).not.toHaveBeenCalled();
    expect(window.MapSheet.toast).toHaveBeenCalledWith(
      "You're offline — connect to download a new area.",
    );
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);
  });

  it('does nothing when the custom-area bridge has not loaded', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();
    delete window.pwaCustomAreaDownload;

    document.querySelector('[data-downloads-add]').click();
    await settle();

    // Optional chaining swallows the missing bridge; the sheet still
    // hides, same as the online path above — nothing here throws.
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(true);
  });
});

describe('renaming an area (SNOW-635)', () => {
  it('shows Rename only on a renameable (custom) row, never on a region', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    const rows = Array.from(document.querySelectorAll('#map-downloads-sheet li'));
    const customRow = rows.find((li) => li.textContent.includes('Custom area'));
    const regionRow = rows.find((li) => li.textContent.includes('Aletsch'));
    expect(customRow.querySelector('[data-downloads-rename]')).toBeTruthy();
    expect(regionRow.querySelector('[data-downloads-rename]')).toBeNull();
  });

  it('pre-fills the prompt with the current (numbered default) label', async () => {
    seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    document.querySelector('[data-downloads-rename]').click();

    expect(window.prompt).toHaveBeenCalledWith('Name this area', 'Custom area 1');
  });

  it('writes the trimmed name back and re-renders showing it', async () => {
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();
    window.prompt.mockReturnValue('  Home run  ');

    document.querySelector('[data-downloads-rename]').click();
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBe('Home run');
    expect(rowLabels()).toEqual(['Home run']);
  });

  it('does nothing when the prompt is cancelled', async () => {
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();
    window.prompt.mockReturnValue(null);

    document.querySelector('[data-downloads-rename]').click();
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBeUndefined();
    expect(rowLabels()).toEqual(['Custom area 1']);
  });

  it('does nothing when the trimmed result is blank', async () => {
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();
    window.prompt.mockReturnValue('   ');

    document.querySelector('[data-downloads-rename]').click();
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBeUndefined();
  });

  it('renames only its own area, leaving a second one untouched', async () => {
    const second = {
      id: 'custom-b2',
      ordinal: 2,
      bbox: [7.0, 46.0, 7.2, 46.2],
      band: [10, 14],
      centre_tile: { z: 14, x: 8500, y: 5800 },
      bytes: 30 * MB,
      savedAt: '2026-08-03T10:00:00.000Z',
    };
    const rows = seed({ 'basemap.customAreas': [...CUSTOM_AREAS, second] });
    await loadModule();
    openSheet();
    await settle();
    window.prompt.mockReturnValue('Home run');

    // Rows render largest-first; the first area (120 MB) sorts first.
    document.querySelector('[data-downloads-rename]').click();
    await settle();

    const stored = rows.get('basemap.customAreas');
    expect(stored.find((a) => a.id === 'custom-a1').name).toBe('Home run');
    expect(stored.find((a) => a.id === 'custom-b2').name).toBeUndefined();
    expect(rowLabels()).toEqual(['Home run', 'Custom area 2']);
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
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.caches.delete).toHaveBeenCalledWith(
      'snowdesk-basemap-pinned-custom-a1',
    );
    expect(window.caches.names.has('snowdesk-basemap-pinned-custom-a1')).toBe(false);
    // The other area is untouched.
    expect(window.caches.names.has('snowdesk-basemap-pinned-region-CH-2101')).toBe(true);
  });

  it('drops only that area from the record', async () => {
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions').map((e) => e.region_id)).toEqual(['CH-2101']);
    expect(rows.get('basemap.customAreas')).toEqual([]);
  });

  it('re-renders, and restates the total without the removed area', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(rowLabels()).toEqual(['Aletsch']);
    expect(
      document.querySelector('[data-downloads-summary]').textContent,
    ).toBe('40.0 MB of 500 MB used');
  });

  it('deletes only its own custom area, leaving a second one intact', async () => {
    // SNOW-635: the multi-area case — deleting one must not touch another.
    const second = {
      id: 'custom-b2',
      ordinal: 2,
      bbox: [7.0, 46.0, 7.2, 46.2],
      band: [10, 14],
      centre_tile: { z: 14, x: 8500, y: 5800 },
      bytes: 30 * MB,
      savedAt: '2026-08-03T10:00:00.000Z',
    };
    installCachesStub([
      'snowdesk-basemap-pinned-custom-a1',
      'snowdesk-basemap-pinned-custom-b2',
    ]);
    const rows = seed({ 'basemap.customAreas': [...CUSTOM_AREAS, second] });
    await loadModule();
    await openAndDeleteFirst();

    expect(rows.get('basemap.customAreas').map((a) => a.id)).toEqual(['custom-b2']);
    expect(window.caches.names.has('snowdesk-basemap-pinned-custom-b2')).toBe(true);
    expect(rowLabels()).toEqual(['Custom area 2']);
  });

  it("refreshes the map's own views of the cache it just changed", async () => {
    // Otherwise the downloaded-areas overlay keeps ringing an area with no
    // tiles behind it, and the layers-menu dots keep claiming it's offline-ready.
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.pwaDownloadedOverlay.refresh).toHaveBeenCalled();
    expect(window.pwaLayerSyncStatus.refresh).toHaveBeenCalled();
    // SNOW-634: and the roundel that opens this sheet — a delete can take
    // the device from "done" back to "idle".
    expect(window.pwaCustomAreaDownload.refresh).toHaveBeenCalled();
  });

  it('names the area and the space freed in its confirmation', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
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
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    await openAndDeleteFirst();

    expect(window.caches.delete).not.toHaveBeenCalled();
    expect(rows.get('basemap.regions')).toHaveLength(1);
    expect(rows.get('basemap.customAreas')).toHaveLength(1);
    expect(rowLabels()).toEqual(['Custom area 1', 'Aletsch']);
  });

  it('keeps the record intact when the bucket delete throws', async () => {
    // The record is what still points at those bytes. Dropping it on a
    // failed delete would strand tiles on disk that no surface could then
    // account for or remove.
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    window.caches.delete.mockRejectedValue(new Error('storage error'));
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions')).toHaveLength(1);
    expect(rows.get('basemap.customAreas')).toHaveLength(1);
    const button = document.querySelector('[data-downloads-delete]');
    expect(button.textContent).toBe("That download couldn't be removed. Try again.");
    expect(button.hasAttribute('disabled')).toBe(true);
  });

  it('still drops the record entry when the bucket was already gone', async () => {
    // `caches.delete` resolving false means the bucket wasn't there — the
    // tiles are gone either way, so the record must not keep listing it.
    const rows = seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    window.caches.delete.mockResolvedValue(false);
    await openAndDeleteFirst();

    expect(rows.get('basemap.regions').map((e) => e.region_id)).toEqual(['CH-2101']);
    expect(rows.get('basemap.customAreas')).toEqual([]);
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
