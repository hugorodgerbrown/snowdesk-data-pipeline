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

// Mirrors map.js's MAP_STRINGS['default-custom-name'] English fallback
// (_map_embed.html's map-strings-template) — this fixture has no template
// of its own to read it from, so the literal is duplicated here the same
// way the other seeded records mirror the real writer's shape.
const DEFAULT_CUSTOM_NAME = 'Custom area %(n)s';

/**
 * Markup mirroring _map_downloads_sheet.html (SNOW-634: no menu row;
 * SNOW-635: Rename; SNOW-645 review: grouped by kind, budget folded into
 * the header, the toggle moved to the foot; SNOW-658: Hugo's map-panel
 * design — the "…" menu is gone from every panel, so a row's actions are
 * two visible icon controls and its rename is an inline edit on its own
 * label).
 *
 * The node ORDER here is kept in step with the real template even though
 * nothing in this file depends on it — the module addresses every element
 * by data-attribute, never by index or sibling relationship, which is
 * exactly what let SNOW-641/645 reorder the sheet without touching the
 * JS. A fixture that drifted out of order would still pass while quietly
 * ceasing to be a description of the thing under test.
 *
 * The row template is a hand-copy of includes/_ugc_panel_row.html +
 * includes/_map_downloads_row_actions.html rather than the partials
 * themselves — Vitest cannot render a Django template (see the file
 * header) — carrying every hook buildRow fills or strips, including the
 * hidden `[data-row-rename-input]` the inline editor reveals.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map-downloads-sheet" hidden tabindex="-1" data-overlay
         aria-label="Downloads on this device"></div>
    <template id="map-downloads-body-template">
      <div>
        <div>
          <div><span>Downloads on this device</span>
            <button type="button" data-action="dismiss">×</button></div>
          <div>
            <div>
              <div data-downloads-track class="h-2 flex-1 overflow-hidden rounded-pill bg-chip">
                <div data-downloads-bar class="flex h-full rounded-pill" style="width: 0%"></div>
              </div>
              <p data-downloads-summary>
                <span data-downloads-summary-value></span>
                <span>of</span>
              </p>
              <select id="map-downloads-budget" data-downloads-budget></select>
            </div>
            <p>Downloads and budget stay on this device.</p>
          </div>
        </div>
        <p data-downloads-over hidden>You're over your budget.</p>
        <div>
          <div data-downloads-group="region" hidden>
            <p>Regions</p>
            <ul data-downloads-list-region></ul>
          </div>
          <div data-downloads-group="custom" hidden>
            <p>Custom areas</p>
            <ul data-downloads-list-custom></ul>
          </div>
          <p data-downloads-empty hidden>You haven't downloaded any areas yet.</p>
        </div>
        <div>
          <button type="button" data-panel-add>Download a custom area</button>
        </div>
        <div>
          <label for="map-downloads-overlay-toggle">Display on the map</label>
          <label for="map-downloads-overlay-toggle">
            <input id="map-downloads-overlay-toggle" type="checkbox" role="switch">
          </label>
        </div>
      </div>
    </template>
    <template id="map-downloads-row-template">
      <li>
        <span data-row-rule class="basemap-identity-fill" aria-hidden="true"></span>
        <span>
          <span data-row-label class="text-text-1"></span>
          <input type="text" data-row-rename-input hidden aria-label="Area name">
          <span data-row-meta class="text-text-2"></span>
        </span>
        <span>
          <span data-row-value class="text-text-2"></span>
          <button type="button" data-row-rename data-downloads-rename aria-label="Rename">✎</button>
          <button type="button" data-downloads-delete aria-label="Remove">🗑</button>
        </span>
      </li>
    </template>
    <ul id="basemap-menu" hidden>
      <li role="none">
        <button type="button" data-basemap-key="openfreemap_liberty" aria-checked="true">Standard</button>
      </li>
      <li role="none">
        <button type="button" data-basemap-key="swisstopo_winter" aria-checked="false">Swisstopo (CH)</button>
      </li>
    </ul>
    <template id="map-downloads-strings-template">
      <span data-string="kind-incomplete">Incomplete</span>
      <span data-string="confirm-remove">Remove the offline map for %(name)s? This
            frees %(size)s. You can download it again when you're back online.</span>
      <span data-string="remove-failed">That download couldn't be removed. Try again.</span>
      <span data-string="add-offline">You're offline — connect to download a new area.</span>
      <span data-string="add-disabled">Downloading needs a connection</span>
      <span data-string="rename-prompt">Name this area</span>
      <span data-string="rename-failed">That name couldn't be saved. Try again.</span>
      <span data-string="row-menu-label">More actions for %(name)s</span>
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
        // SNOW-645: mirrors map.js's own basemapDownloadedAreas — absent
        // on a record written before this ticket shipped.
        basemapKey: entry.basemapKey || null,
      });
    }
    for (const entry of rows.get('basemap.customAreas') || []) {
      if (!entry || !entry.id || !Array.isArray(entry.bbox)) continue;
      out.push({
        id: entry.id,
        // SNOW-635 review: a rename's stored name wins; an unrenamed
        // area's numbered default is filled in HERE, from `ordinal`, in
        // memory only — mirroring map.js's own basemapDownloadedAreas so
        // every downstream reader (the sheet, the eviction banner, the
        // rename prompt's pre-fill) can read `name` uniformly.
        name: entry.name || self.pwaStrings.interpolate(DEFAULT_CUSTOM_NAME, { n: entry.ordinal }),
        bytes: Number(entry.bytes) || 0,
        savedAt: entry.savedAt,
        basemapKey: entry.basemapKey || null,
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
    // SNOW-645: mirrors map_basemap_downloads.js's basemapLabel — this
    // bridge is what map_downloads_manager.js reaches it through, since
    // that module is deliberately outside the map bundle's load-order
    // contract (see buildRow's own comment). Reads the SAME picker markup
    // buildFixture() renders, never interpolating the key into a selector.
    basemapLabel: vi.fn((key) => {
      const menu = document.getElementById('basemap-menu');
      if (!menu) return '';
      let label = '';
      menu.querySelectorAll('[data-basemap-key]').forEach((btn) => {
        if (btn.dataset.basemapKey === key) label = btn.textContent.trim();
      });
      return label;
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
  // SNOW-658: renaming a row is an inline edit on its label, and the
  // interaction lives in its own module shared with the favourites panel.
  // home.html loads it ahead of this one; so does this fixture.
  await import('../../static/js/inline_rename.js');
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
  // SNOW-658: the sheet takes part in the shared "one overlay at a time"
  // registry (static/js/map_overlay_exclusivity.js) instead of closing the
  // one sibling it used to know about by name. A stub stands in for the
  // module so this suite can assert the announcement without loading it;
  // the registry's own behaviour is covered in
  // tests/js/test_map_overlay_exclusivity.js.
  window.pwaMapOverlays = { register: vi.fn(), opening: vi.fn() };
  // SNOW-645 review: open() calls show() unconditionally before render(),
  // and the "Available offline" toggle calls show()/hide() directly (see
  // that block's own describe below) — a stub missing either used to fail
  // EVERY test that opens the sheet with "show is not a function", not
  // just the ones this bridge is actually the point of.
  window.pwaDownloadedOverlay = {
    refresh: vi.fn(),
    show: vi.fn(),
    hide: vi.fn(),
    isEnabled: vi.fn(() => false),
  };
  window.pwaLayerSyncStatus = { refresh: vi.fn() };
  // SNOW-634: window.pwaCustomAreaDownload — map.js's own bridge, the
  // add-trigger's online path calls it. window.MapSheet.toast — its
  // offline path calls that instead.
  window.pwaCustomAreaDownload = { openFraming: vi.fn(), refresh: vi.fn() };
  window.MapSheet = { toast: vi.fn() };
  vi.stubGlobal('confirm', vi.fn(() => true));
  setOnline(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setOnline(true);
  delete window.pwaDb;
  delete window.pwaDownloadsManager;
  delete window.pwaBasemapDownloads;
  delete window.pwaCustomAreaDownload;
  delete window.pwaOverlayBounds;
  delete window.MapSheet;
  delete window.pwaConnectivity;
});

describe('opening the sheet', () => {
  it('renders one row per stored area, largest first, with names and sizes', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    const sheet = document.getElementById('map-downloads-sheet');
    expect(sheet.hidden).toBe(false);
    // SNOW-645 review: grouped by kind — REGIONS before CUSTOM AREAS,
    // regardless of size — not one flat largest-first list any more.
    // SNOW-635: an unrenamed custom area's default label is numbered.
    expect(rowLabels()).toEqual(['Aletsch', 'Custom area 1']);
    expect(
      Array.from(sheet.querySelectorAll('[data-row-value]')).map((el) => el.textContent),
    ).toEqual(['40.0 MB', '120 MB']);
  });

  it('states the running total against the budget', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    // SNOW-645 review: the used figure and the budget are two separate
    // elements now — the numeral (data-downloads-summary-value) plus a
    // static "of" node in the header, and the budget itself in its own
    // <select> right after — not one interpolated "X of Y used" string.
    expect(
      document.querySelector('[data-downloads-summary-value]').textContent,
    ).toBe('160 MB');
    expect(
      document.querySelector('[data-downloads-budget]').value,
    ).toBe('500');
  });

  it('places itself inside the map area (SNOW-658)', async () => {
    // The geometry belongs to static/js/map_overlay_bounds.js and is covered
    // in tests/js/test_map_overlay_sheet_position.js. This module is the one
    // sheet that does NOT go through MapSheet.attach() — it owns
    // `sheet.hidden` itself — so its own open() is the only place the call
    // can be made, and the only thing that can be asserted here.
    window.pwaOverlayBounds = { positionSheet: vi.fn() };
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    expect(window.pwaOverlayBounds.positionSheet).toHaveBeenCalledWith(
      document.getElementById('map-downloads-sheet'),
    );
  });

  it('announces its open so every other map overlay closes', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    expect(window.pwaMapOverlays.opening).toHaveBeenCalledWith(
      'map-downloads-sheet',
    );
  });

  it('registers itself so every other map overlay can close it', async () => {
    seed({});
    await loadModule();

    expect(window.pwaMapOverlays.register).toHaveBeenCalledWith(
      'map-downloads-sheet',
      expect.objectContaining({
        isOpen: expect.any(Function),
        close: expect.any(Function),
      }),
    );
  });

  it('toggles shut when the roundel is tapped a second time', async () => {
    // The roundel's own handler is one line — `pwaDownloadsManager.toggle()`
    // — in map_custom_download.js, which this suite does not load; the
    // bridge it calls is the equivalent entry point (see openSheet).
    seed({ 'basemap.regions': REGIONS });
    await loadModule();
    await window.pwaDownloadsManager.toggle();
    await settle();
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);

    await window.pwaDownloadsManager.toggle();

    expect(document.getElementById('map-downloads-sheet').hidden).toBe(true);
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
    expect(rowLabels()).toEqual(['Aletsch', 'Custom area 1']);
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

describe('the downloaded-areas overlay bridge (SNOW-645 review)', () => {
  it('does NOT turn the overlay on just because the sheet opened (SNOW-656)', async () => {
    // SNOW-645 called show() unconditionally here, for discoverability. That
    // was cheap while the squares were all it changed — but they are now
    // mutually exclusive with the danger choropleth, so opening this sheet
    // took the choropleth off the map and dropped the fill control to 0,
    // neither of which the user asked for. The overlay is the switch's
    // business now.
    seed({});
    await loadModule();
    openSheet();

    expect(window.pwaDownloadedOverlay.show).not.toHaveBeenCalled();
    await settle();
  });

  it("paints the toggle from the overlay's real visibility, not a flag of its own", async () => {
    seed({});
    window.pwaDownloadedOverlay.isEnabled.mockReturnValue(true);
    await loadModule();
    openSheet();
    await settle();

    expect(
      document.getElementById('map-downloads-overlay-toggle').checked,
    ).toBe(true);
  });

  it('the "Display on the map" toggle drives show()/hide() directly', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    const toggle = document.getElementById('map-downloads-overlay-toggle');
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    // Once, from this change alone — open() no longer calls it (SNOW-656).
    expect(window.pwaDownloadedOverlay.show).toHaveBeenCalledTimes(1);

    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(window.pwaDownloadedOverlay.hide).toHaveBeenCalledTimes(1);
  });

  it('mirrors a change made from outside the sheet (SNOW-656)', async () => {
    // The in-sheet switch is no longer the only writer: switching the
    // Bulletins row on from the layers menu switches the squares off, and a
    // sheet still open behind that menu must not sit on a checked switch for
    // an overlay that is no longer drawn.
    seed({});
    window.pwaDownloadedOverlay.isEnabled.mockReturnValue(true);
    await loadModule();
    openSheet();
    await settle();

    const toggle = document.getElementById('map-downloads-overlay-toggle');
    expect(toggle.checked).toBe(true);

    document.dispatchEvent(
      new CustomEvent('snowdesk:downloaded-overlay-changed', {
        detail: { visible: false },
      }),
    );

    expect(toggle.checked).toBe(false);
  });
});

describe('basemap identity (SNOW-645 review — coloured rule + subtitle, not a swatch)', () => {
  it("colours the left rule with the row's own basemapKey and names it in the subtitle", async () => {
    seed({
      'basemap.regions': [{ ...REGIONS[0], basemapKey: 'openfreemap_liberty' }],
      'basemap.customAreas': [{ ...CUSTOM_AREAS[0], basemapKey: 'swisstopo_winter' }],
    });
    await loadModule();
    openSheet();
    await settle();

    // Regions render before custom areas (grouped by kind) — see
    // "opening the sheet" above.
    const rules = Array.from(document.querySelectorAll('#map-downloads-sheet [data-row-rule]'));
    expect(rules.map((el) => el.dataset.basemapKey)).toEqual([
      'openfreemap_liberty',
      'swisstopo_winter',
    ]);
    const subtitles = Array.from(
      document.querySelectorAll('#map-downloads-sheet [data-row-meta]'),
    );
    expect(subtitles.map((el) => el.textContent)).toEqual(['Standard', 'Swisstopo (CH)']);
  });

  it('removes the subtitle for a record written before this ticket shipped', async () => {
    // No `basemapKey` at all — a legacy record, not a wrong basemap.
    seed({ 'basemap.regions': [REGIONS[0]] });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('#map-downloads-sheet [data-row-meta]')).toBeNull();
  });

  it('shows "Incomplete" for an orphaned bucket, never a guessed basemap subtitle', async () => {
    seed({});
    await loadModule();
    // An orphan has no record at all, so no basemapKey — mirrors what
    // basemapDownloadedAreas()'s reconciliation hands manageRows for a
    // pinned bucket left behind by a failed download (SNOW-612). This
    // file's own bridge reimplementation has no reconciliation of its
    // own (see installDownloadsBridge's docstring), so the orphan is
    // injected directly onto the one call this render makes.
    window.pwaBasemapDownloads.areas.mockResolvedValueOnce([
      { id: 'orphan-1', orphaned: true, bytes: 5 * MB, savedAt: undefined },
    ]);
    openSheet();
    await settle();

    expect(
      document.querySelector('#map-downloads-sheet [data-row-meta]').textContent,
    ).toBe('Incomplete');
  });

  it('removes the subtitle for a key the picker has no row for', async () => {
    // The picker markup is the source of truth for the label; a key it
    // does not recognise (a deployment BASEMAP override, or a stale key
    // from a since-removed style) must not show an unlabelled subtitle.
    seed({ 'basemap.regions': [{ ...REGIONS[0], basemapKey: 'no_such_basemap' }] });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('#map-downloads-sheet [data-row-meta]')).toBeNull();
  });
});

describe('orphan dimming and the pale rule (SNOW-645 review)', () => {
  // Starts from [data-row-rule], not [data-downloads-delete] — the delete
  // button's nearest `<li>` ancestor is the OVERFLOW MENU's own item
  // wrapper (<li role="none"> inside the "…" menu), not the row itself;
  // [data-row-rule] sits directly in the row's own <li> with nothing
  // else between.
  function orphanRow() {
    return document.querySelector('#map-downloads-sheet [data-row-rule]').closest('li');
  }

  it('dims the title and size, and pales the rule, for an orphaned row', async () => {
    seed({});
    await loadModule();
    window.pwaBasemapDownloads.areas.mockResolvedValueOnce([
      { id: 'orphan-1', orphaned: true, bytes: 5 * MB, savedAt: undefined },
    ]);
    openSheet();
    await settle();

    const row = orphanRow();
    const label = row.querySelector('[data-row-label]');
    const size = row.querySelector('[data-row-value]');
    const rule = row.querySelector('[data-row-rule]');
    expect(label.classList.contains('text-text-2')).toBe(true);
    expect(label.classList.contains('text-text-1')).toBe(false);
    expect(size.classList.contains('text-text-3')).toBe(true);
    expect(size.classList.contains('text-text-2')).toBe(false);
    expect(rule.classList.contains('opacity-35')).toBe(true);
  });

  it('leaves a normal (non-orphaned) row at full strength — no dimming, no opacity-35', async () => {
    seed({ 'basemap.regions': REGIONS });
    await loadModule();
    openSheet();
    await settle();

    const row = document.querySelector('#map-downloads-sheet li');
    const label = row.querySelector('[data-row-label]');
    const rule = row.querySelector('[data-row-rule]');
    expect(label.classList.contains('text-text-1')).toBe(true);
    expect(rule.classList.contains('opacity-35')).toBe(false);
  });

  it("paints the rule with the RECOVERED basemap when orphanBasemapKey resolves one", async () => {
    seed({});
    await loadModule();
    window.pwaBasemapDownloads.areas.mockResolvedValueOnce([
      { id: 'orphan-1', orphaned: true, bytes: 5 * MB, savedAt: undefined },
    ]);
    window.pwaBasemapDownloads.orphanBasemapKey = vi.fn(async () => 'swisstopo_winter');
    openSheet();
    await settle();

    const rule = orphanRow().querySelector('[data-row-rule]');
    expect(rule.dataset.basemapKey).toBe('swisstopo_winter');
    expect(rule.classList.contains('basemap-identity-fill')).toBe(true);
    expect(rule.classList.contains('bg-sync-off')).toBe(false);
  });

  it('falls back to the neutral bg-sync-off rule when nothing could be inferred', async () => {
    seed({});
    await loadModule();
    window.pwaBasemapDownloads.areas.mockResolvedValueOnce([
      { id: 'orphan-1', orphaned: true, bytes: 5 * MB, savedAt: undefined },
    ]);
    window.pwaBasemapDownloads.orphanBasemapKey = vi.fn(async () => null);
    openSheet();
    await settle();

    const rule = orphanRow().querySelector('[data-row-rule]');
    expect(rule.dataset.basemapKey).toBeUndefined();
    expect(rule.classList.contains('bg-sync-off')).toBe(true);
    // Never the keyless GREEN default a genuinely-downloaded, basemap-
    // unknown row gets — see buildRow's own comment: "downloaded, basemap
    // unknown" is not true of something that never finished.
    expect(rule.classList.contains('basemap-identity-fill')).toBe(false);
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
      document.querySelector('[data-downloads-summary-value]').textContent,
    ).toBe('160 MB');
    expect(document.querySelector('[data-downloads-budget]').value).toBe('1000');
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

    // SNOW-645 review: the bar itself now carries real basemap colours
    // (one segment per basemap_manage_core.js's budgetSegments), so the
    // over-budget signal moved to the TRACK that holds it — a ring, not a
    // solid-red fill.
    const track = document.querySelector('[data-downloads-track]');
    expect(document.querySelector('[data-downloads-over]').hidden).toBe(false);
    expect(track.classList.contains('ring-2')).toBe(true);
    expect(track.classList.contains('ring-status-error-text')).toBe(true);
  });

  it('keeps the over-budget line hidden while under budget', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS, 'basemap.budgetMb': 500 });
    await loadModule();
    openSheet();
    await settle();

    expect(document.querySelector('[data-downloads-over]').hidden).toBe(true);
    const track = document.querySelector('[data-downloads-track]');
    expect(track.classList.contains('ring-2')).toBe(false);
    expect(track.classList.contains('ring-status-error-text')).toBe(false);
  });
});

describe('the add-trigger', () => {
  // SNOW-634: "Download a custom area" moved from the roundel's own click
  // into this sheet, as [data-panel-add] above the list.

  it('hides the sheet and opens framing when online', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    document.querySelector('[data-panel-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).toHaveBeenCalled();
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(true);
    expect(window.MapSheet.toast).not.toHaveBeenCalled();
  });

  it('toasts and leaves the sheet open when offline', async () => {
    // The connection is lost AFTER the sheet painted, so the trigger is
    // still enabled — this is SNOW-637's race guard, not its main path.
    seed({});
    await loadModule();
    openSheet();
    await settle();
    setOnline(false);

    document.querySelector('[data-panel-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).not.toHaveBeenCalled();
    expect(window.MapSheet.toast).toHaveBeenCalledWith(
      "You're offline — connect to download a new area.",
    );
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);
  });

  // SNOW-637: rendering while offline disables the trigger outright, so the
  // toast above is only ever reached by a connection lost mid-sheet.

  it('renders the trigger disabled and relabelled when already offline', async () => {
    seed({});
    setOnline(false);
    await loadModule();
    openSheet();
    await settle();

    const add = document.querySelector('[data-panel-add]');
    expect(add.hasAttribute('disabled')).toBe(true);
    expect(add.getAttribute('aria-disabled')).toBe('true');
    expect(add.textContent).toBe('Downloading needs a connection');
  });

  it('renders the trigger enabled and unlabelled when online', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();

    const add = document.querySelector('[data-panel-add]');
    expect(add.hasAttribute('disabled')).toBe(false);
    expect(add.hasAttribute('aria-disabled')).toBe(false);
    expect(add.textContent).toBe('Download a custom area');
  });

  it('cannot reach framing while offline, even if the click lands', async () => {
    seed({});
    setOnline(false);
    await loadModule();
    openSheet();
    await settle();

    document.querySelector('[data-panel-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).not.toHaveBeenCalled();
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(false);
  });

  it('disables an open sheet the moment the connection drops', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();
    expect(
      document.querySelector('[data-panel-add]').hasAttribute('disabled'),
    ).toBe(false);

    setOnline(false);
    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }),
    );
    await settle();

    expect(
      document.querySelector('[data-panel-add]').hasAttribute('disabled'),
    ).toBe(true);
  });

  it('re-enables it again when the connection comes back', async () => {
    seed({});
    setOnline(false);
    await loadModule();
    openSheet();
    await settle();

    setOnline(true);
    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', { detail: { online: true } }),
    );
    await settle();

    const add = document.querySelector('[data-panel-add]');
    expect(add.hasAttribute('disabled')).toBe(false);
    expect(add.textContent).toBe('Download a custom area');
  });

  it('does not re-render a closed sheet on a connectivity change', async () => {
    seed({});
    await loadModule();

    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }),
    );
    await settle();

    // Never opened, so the listener has nothing to refresh: the sheet is
    // still the empty container buildFixture made, with no cloned body in
    // it. Asserted on the sheet rather than by counting pwaDb reads —
    // vi.resetModules() leaves each earlier test's module instance still
    // listening on `document`, and those render into their own detached
    // sheets against whatever db stub is current.
    const sheet = document.getElementById('map-downloads-sheet');
    expect(sheet.hidden).toBe(true);
    expect(sheet.children.length).toBe(0);
  });

  // SNOW-748: the interface being up is only half of "can this download
  // happen". The other half is the network mode — the user can force offline
  // mode from the header toggle while the radio is up, and the service worker
  // then refuses the run outright (sw.js's _warmCache guard). A trigger that
  // reads navigator.onLine alone stays enabled and dispatches into that
  // refusal, which is the bug these two pin.

  it('disables the trigger under a forced offline mode, with the radio up', async () => {
    seed({});
    setOnline(true);
    window.pwaConnectivity = { isOnline: () => false };
    await loadModule();
    openSheet();
    await settle();

    const add = document.querySelector('[data-panel-add]');
    expect(window.navigator.onLine).toBe(true);
    expect(add.hasAttribute('disabled')).toBe(true);
    // Disabled and explained, never hidden — a missing control reads as a
    // bug, a disabled one reads as "not right now".
    expect(add.textContent).toBe('Downloading needs a connection');
  });

  it('cannot reach framing under a forced offline mode, even if the click lands', async () => {
    seed({});
    setOnline(true);
    await loadModule();
    openSheet();
    await settle();
    // The mode is forced AFTER the paint, so the trigger is still enabled —
    // the click handler's own guard is what has to catch this one.
    window.pwaConnectivity = { isOnline: () => false };

    document.querySelector('[data-panel-add]').click();
    await settle();

    expect(window.pwaCustomAreaDownload.openFraming).not.toHaveBeenCalled();
    expect(window.MapSheet.toast).toHaveBeenCalled();
  });

  it('does nothing when the custom-area bridge has not loaded', async () => {
    seed({});
    await loadModule();
    openSheet();
    await settle();
    delete window.pwaCustomAreaDownload;

    document.querySelector('[data-panel-add]').click();
    await settle();

    // Optional chaining swallows the missing bridge; the sheet still
    // hides, same as the online path above — nothing here throws.
    expect(document.getElementById('map-downloads-sheet').hidden).toBe(true);
  });
});

describe('renaming an area (SNOW-635; inline since SNOW-658)', () => {
  /** The row for the first (largest) custom area.
   *
   * @returns {HTMLElement}
   */
  function customRow() {
    return document.querySelector('[data-downloads-list-custom] li');
  }

  /** Type into an open editor and finish the edit.
   *
   * @param {HTMLElement} row The row being edited.
   * @param {string} value What the user typed.
   * @param {string} key 'Enter' or 'Escape'; anything else blurs instead.
   * @returns {void}
   */
  function finishEdit(row, value, key) {
    const input = row.querySelector('[data-row-rename-input]');
    input.value = value;
    if (key === 'Enter' || key === 'Escape') {
      input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      return;
    }
    input.dispatchEvent(new FocusEvent('blur'));
  }

  it('shows Rename only on a renameable (custom) row, never on a region', async () => {
    seed({ 'basemap.regions': REGIONS, 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    const rows = Array.from(document.querySelectorAll('#map-downloads-sheet li'));
    const custom = rows.find((li) => li.textContent.includes('Custom area'));
    const region = rows.find((li) => li.textContent.includes('Aletsch'));
    expect(custom.querySelector('[data-downloads-rename]')).toBeTruthy();
    expect(custom.hasAttribute('data-row-renameable')).toBe(true);
    expect(region.querySelector('[data-downloads-rename]')).toBeNull();
  });

  it('strips the whole inline-edit affordance from a region row', async () => {
    // Not just the pencil: the editor and the row marker go too, or
    // inline_rename.js still recognises the row and a region reads as
    // editable. SNOW-658: the label itself is no longer part of this — it
    // stopped being a rename trigger on every row, so there is nothing on
    // it to strip.
    seed({ 'basemap.regions': REGIONS });
    await loadModule();
    openSheet();
    await settle();

    const region = document.querySelector('[data-downloads-list-region] li');
    expect(region.hasAttribute('data-row-renameable')).toBe(false);
    expect(region.querySelector('[data-row-rename-input]')).toBeNull();
  });

  it('opens the editor seeded with the current (numbered default) label', async () => {
    seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    customRow().querySelector('[data-downloads-rename]').click();

    const input = customRow().querySelector('[data-row-rename-input]');
    expect(input.hidden).toBe(false);
    expect(input.value).toBe('Custom area 1');
    // The label steps aside for it rather than sitting above it.
    expect(customRow().querySelector('[data-row-label]').hidden).toBe(true);
  });

  it('does not open the editor from a click on the label (SNOW-658)', async () => {
    // The label was a second trigger for a day. Hugo: "We have inline
    // editing & the pencil - choose one." Both renameable panels dropped
    // it in the same change, since both go through inline_rename.js.
    seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    customRow().querySelector('[data-row-label]').click();

    expect(customRow().querySelector('[data-row-rename-input]').hidden).toBe(true);
    expect(customRow().querySelector('[data-row-label]').hidden).toBe(false);
  });

  it('writes the trimmed name back on Enter and re-renders showing it', async () => {
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    customRow().querySelector('[data-downloads-rename]').click();
    finishEdit(customRow(), '  Home run  ', 'Enter');
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBe('Home run');
    expect(rowLabels()).toEqual(['Home run']);
  });

  it('commits on blur too — leaving the field is not a cancel', async () => {
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    customRow().querySelector('[data-downloads-rename]').click();
    finishEdit(customRow(), 'Home run', 'blur');
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBe('Home run');
  });

  it('writes nothing on Escape, and does not then commit on the blur', async () => {
    // The regression this guards: hiding a focused input fires blur, and
    // blur commits — so a cancel that unbinds too late saves the very
    // edit the user just refused.
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    const row = customRow();
    row.querySelector('[data-downloads-rename]').click();
    finishEdit(row, 'Home run', 'Escape');
    row.querySelector('[data-row-rename-input]').dispatchEvent(new FocusEvent('blur'));
    await settle();

    expect(rows.get('basemap.customAreas')[0].name).toBeUndefined();
    expect(rowLabels()).toEqual(['Custom area 1']);
  });

  it('writes nothing when the field is emptied', async () => {
    // The design's own commit(): a trimmed-empty input leaves the title
    // unchanged. An area with no name at all has none to fall back on.
    const rows = seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();

    customRow().querySelector('[data-downloads-rename]').click();
    finishEdit(customRow(), '   ', 'Enter');
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

    // Rows render largest-first; the first area (120 MB) sorts first.
    customRow().querySelector('[data-downloads-rename]').click();
    finishEdit(customRow(), 'Home run', 'Enter');
    await settle();

    const stored = rows.get('basemap.customAreas');
    expect(stored.find((a) => a.id === 'custom-a1').name).toBe('Home run');
    expect(stored.find((a) => a.id === 'custom-b2').name).toBeUndefined();
    expect(rowLabels()).toEqual(['Home run', 'Custom area 2']);
  });

  it('toasts, and keeps the old label, when the write does not land', async () => {
    seed({ 'basemap.customAreas': CUSTOM_AREAS });
    await loadModule();
    openSheet();
    await settle();
    window.pwaBasemapDownloads.rename = vi.fn(async () => false);

    customRow().querySelector('[data-downloads-rename]').click();
    finishEdit(customRow(), 'Home run', 'Enter');
    await settle();

    expect(window.MapSheet.toast).toHaveBeenCalledTimes(1);
    expect(window.MapSheet.toast.mock.calls[0][0]).toContain("couldn't be saved");
    expect(rowLabels()).toEqual(['Custom area 1']);
  });
});

describe('deleting an area', () => {
  // SNOW-645 review: rows are grouped by kind now (REGIONS before CUSTOM
  // AREAS), so a bare "first [data-downloads-delete] in the document"
  // would land on Aletsch (the region), not custom-a1 — a silent change
  // of which area every test below actually deletes. Scoped to the
  // custom-areas group to keep deleting THAT area (120 MB, the one every
  // assertion below was written against).
  async function openAndDeleteFirst() {
    openSheet();
    await settle();
    document
      .querySelector('[data-downloads-list-custom] [data-downloads-delete]')
      .click();
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
      document.querySelector('[data-downloads-summary-value]').textContent,
    ).toBe('40.0 MB');
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
    // SNOW-658: the roundel that opens this sheet is deliberately NOT
    // refreshed. SNOW-634 refreshed it because a delete could take it from
    // "done" back to "idle"; it carries no downloads state at all now (it
    // says whether ITS overlay is on the map), so a delete is not its
    // business — asserted rather than dropped, because re-adding the call
    // would be re-adding the state.
    expect(window.pwaCustomAreaDownload.refresh).not.toHaveBeenCalled();
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
    expect(rowLabels()).toEqual(['Aletsch', 'Custom area 1']);
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
    // The failure mutates the SAME button that was clicked (the custom
    // area's — see openAndDeleteFirst), not a fresh re-render, so the
    // assertion has to stay scoped to it too.
    const button = document.querySelector(
      '[data-downloads-list-custom] [data-downloads-delete]',
    );
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
