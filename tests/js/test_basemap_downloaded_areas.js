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
