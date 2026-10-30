/*
 * tests/js/test_basemap_area_removal.js — Vitest tests for
 * static/js/basemap_area_removal.js (SNOW-1308), the one removal of a
 * downloaded area that the map's Manage downloads sheet and the /offline/
 * panels share.
 *
 * The database is the real one — db.js against fake-indexeddb, from
 * tests/js/setup.js — because the removal rewrites two whole-array rows and
 * a stub would not show a lost entry. `caches` does not exist in jsdom, so
 * it is stubbed to the one call the removal makes.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_download_core.js';
import '../../static/js/basemap_downloaded_areas.js';
import '../../static/js/basemap_area_removal.js';

let removal;
let deleted;
let posted;

beforeAll(async () => {
  await import('../../static/js/db.js');
  removal = window.pwaBasemapAreaRemoval;
});

beforeEach(async () => {
  deleted = [];
  posted = [];
  Object.defineProperty(window, 'caches', {
    value: {
      delete: vi.fn(async (name) => {
        deleted.push(name);
        return true;
      }),
    },
    configurable: true,
    writable: true,
  });
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { controller: { postMessage: (message) => posted.push(message) } },
    configurable: true,
  });
  await window.pwaDb.put('meta:app', {
    key: 'basemap.regions',
    value: [
      { region_id: 'CH-4115', name: 'Martigny' },
      { region_id: 'CH-4116', name: 'Sion' },
    ],
  });
  await window.pwaDb.put('meta:app', {
    key: 'basemap.customAreas',
    value: [{ id: 'custom-a', ordinal: 1 }, { id: 'custom-b', ordinal: 2 }],
  });
});

afterEach(() => {
  delete navigator.serviceWorker;
});

/** One stored row's value. */
async function stored(key) {
  const row = await window.pwaDb.get('meta:app', key);
  return row ? row.value : undefined;
}

describe('one removal', () => {
  it('deletes the bucket and the recorded region, and leaves the rest', async () => {
    await removal.evict(['region-CH-4115']);

    expect(deleted).toEqual(['snowdesk-basemap-pinned-region-CH-4115']);
    expect((await stored('basemap.regions')).map((r) => r.region_id)).toEqual(['CH-4116']);
    expect(await stored('basemap.customAreas')).toHaveLength(2);
  });

  it('removes two custom areas in one call without losing either write', async () => {
    // Both rows are whole arrays: a per-id read-filter-write would leave
    // one of the two alive (SNOW-635 review).
    await removal.evict(['custom-a', 'custom-b']);

    expect(await stored('basemap.customAreas')).toEqual([]);
  });

  it('deletes an orphaned bucket that no record accounts for', async () => {
    await removal.evict(['region-CH-9999']);

    expect(deleted).toEqual(['snowdesk-basemap-pinned-region-CH-9999']);
    expect(await stored('basemap.regions')).toHaveLength(2);
  });

  it('tells the worker its pinned-bucket list is stale', async () => {
    await removal.evict(['custom-a']);

    expect(posted).toEqual([{ type: 'pinned-buckets-changed' }]);
  });

  it('runs the caller’s per-bucket hook — the map forgets its measurement there', async () => {
    const onBucketDeleted = vi.fn();

    await removal.evict(['custom-a', 'region-CH-4115'], { onBucketDeleted });

    expect(onBucketDeleted.mock.calls.map((call) => call[0]).sort()).toEqual([
      'custom-a',
      'region-CH-4115',
    ]);
  });

  it('carries on past a bucket that will not delete', async () => {
    window.caches.delete.mockRejectedValueOnce(new Error('quota'));

    expect(await removal.evict(['region-CH-4115'])).toBe(true);
    expect((await stored('basemap.regions')).map((r) => r.region_id)).toEqual(['CH-4116']);
  });

  it('does nothing for no ids', async () => {
    expect(await removal.evict([])).toBe(false);
    expect(deleted).toEqual([]);
    expect(posted).toEqual([]);
  });

  it('says it is available only where what it needs is loaded', () => {
    expect(removal.available()).toBe(true);
    const areas = window.pwaBasemapAreas;
    window.pwaBasemapAreas = undefined;
    try {
      expect(removal.available()).toBe(false);
    } finally {
      window.pwaBasemapAreas = areas;
    }
  });
});
