/*
 * tests/js/test_basemap_downloaded_areas.js — Vitest unit tests for
 * static/js/basemap_downloaded_areas.js.
 *
 * SNOW-1417: the one reader of what the device downloaded carries each
 * area's saved SAC tours boxes, which the tours layer reads offline.
 */

import { beforeAll, describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const BOX = '/api/ski-tours.geojson?bbox=7,46,7.2,46.2';

/** A `pwaDb` stub over `meta:app` rows. */
function installDb(rows) {
  window.pwaDb = {
    get: async (_store, key) => (key in rows ? { key, value: rows[key] } : undefined),
    put: async () => {},
  };
}

beforeAll(async () => {
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
  });
  await import('../../static/js/basemap_downloaded_areas.js');
});

describe('downloadedAreas (SNOW-1417)', () => {
  it("carries each area's saved tours boxes, region and custom alike", async () => {
    installDb({
      'basemap.regions': [{ region_id: 'CH-4115', name: 'Martigny', items: { tourBoxes: [BOX] } }],
      'basemap.customAreas': [{ id: 'custom-a1', name: 'Mine', bbox: [7, 46, 7.2, 46.2] }],
    });

    const areas = await window.pwaBasemapAreas.downloadedAreas();

    expect(areas.map((a) => [a.id, a.tourBoxes])).toEqual([
      [window.pwaBasemapDownloadCore.areaIdForRegion('CH-4115'), [BOX]],
      ['custom-a1', []],
    ]);
  });
});

describe('downloadedAreas (SNOW-1453)', () => {
  const DEM = 'https://tiles.example/dem/{z}/{x}/{y}.webp';

  beforeAll(async () => {
    await import('../../static/js/basemap_manage_core.js');
  });

  it("carries each area's elevation stamp through the reconciliation, '' for none", async () => {
    installDb({
      'basemap.regions': [{ region_id: 'CH-4115', name: 'Martigny', terrain: true, elevation: DEM }],
      'basemap.customAreas': [{ id: 'custom-a1', name: 'Mine', bbox: [7, 46, 7.2, 46.2] }],
    });
    window.pwaDownloadsSync = { accountAreas: async () => [] };

    const areas = await window.pwaBasemapAreas.downloadedAreas();

    expect(areas.map((a) => [a.id, a.elevation])).toEqual([
      [window.pwaBasemapDownloadCore.areaIdForRegion('CH-4115'), DEM],
      ['custom-a1', ''],
    ]);
  });

  it('asks the account for nothing with deviceOnly', async () => {
    installDb({ 'basemap.regions': [], 'basemap.customAreas': [] });
    let asked = 0;
    window.pwaDownloadsSync = {
      accountAreas: async () => {
        asked += 1;
        return [{ area_id: 'custom-elsewhere', name: 'Elsewhere' }];
      },
    };

    const held = await window.pwaBasemapAreas.downloadedAreas({ deviceOnly: true });
    expect(asked).toBe(0);
    expect(held.map((a) => a.id)).not.toContain('custom-elsewhere');

    await window.pwaBasemapAreas.downloadedAreas();
    expect(asked).toBe(1);
  });
});

describe('downloadedAreas (SNOW-1478)', () => {
  const MAPS = [
    { key: 'swisstopo_winter', template: [['s']], deps: ['https://s/style.json'] },
    { key: 'openfreemap_liberty', template: [['o']], deps: ['https://o/style.json'] },
  ];

  beforeAll(async () => {
    await import('../../static/js/basemap_manage_core.js');
  });

  it('carries every map an area holds, region and custom alike, and none for a record before it', async () => {
    installDb({
      'basemap.regions': [
        { region_id: 'CH-4115', name: 'Martigny', basemapKey: 'swisstopo_winter', basemaps: MAPS },
        { region_id: 'CH-1000', name: 'Valais', basemapKey: 'openfreemap_liberty' },
      ],
      'basemap.customAreas': [{ id: 'custom-a1', name: 'Mine', bbox: [7, 46, 7.2, 46.2], basemaps: MAPS }],
    });
    window.pwaDownloadsSync = { accountAreas: async () => [] };

    const areas = await window.pwaBasemapAreas.downloadedAreas();
    const byId = Object.fromEntries(areas.map((a) => [a.id, a]));
    const core = window.pwaBasemapDownloadCore;

    expect(byId[core.areaIdForRegion('CH-4115')].basemaps).toEqual(MAPS);
    expect(byId['custom-a1'].basemaps).toEqual(MAPS);
    expect(byId[core.areaIdForRegion('CH-1000')].basemaps).toBeFalsy();
    // The legacy record still reads as one map, through the core.
    expect(core.recordBasemaps(byId[core.areaIdForRegion('CH-1000')]).map((m) => m.key)).toEqual([
      'openfreemap_liberty',
    ]);
  });
});
