/*
 * tests/js/test_reset_data_summary_core.js — Vitest unit tests for
 * static/js/reset_data_summary_core.js (SNOW-860).
 *
 * The module answers one question — what does "Reset local data" delete,
 * and how much of it is there — and the whole value of the answer is that
 * it is not optimistic. A breakdown that omits a category, understates a
 * figure, or renders a negative one is worse than the bare sentence it
 * replaced, because the user now believes they have been told.
 *
 * So the cases here are mostly the awkward ones: the base layer whose
 * record has not landed, the browser with no `storage.estimate()`, and
 * the origin reporting less usage than we have recorded downloads for.
 *
 * The "reads `queue:mutations`, not `queue:events`" assertion lives in
 * tests/js/test_reset_data_summary.js, not here: this module is handed
 * the count as a number, so the store it came from is only observable in
 * the DOM half that reads it.
 */

import { beforeAll, describe, expect, it } from 'vitest';

const MB = 1024 * 1024;

let core;

beforeAll(async () => {
  await import('../../static/js/reset_data_summary_core.js');
  core = window.pwaResetDataSummaryCore;
});

describe('the downloaded-maps category', () => {
  it('lists the user areas alone, in the order they arrive', () => {
    const summary = core.summarise({
      rows: [
        { id: 'region-ch-4115', label: 'Martigny', bytes: 10 * MB },
        { id: 'custom-a1', label: 'Custom area 1', bytes: 5 * MB },
      ],
      baseLayers: [{ id: 'base-swisstopo_winter', name: 'Overview map', bytes: 2 * MB }],
    });

    expect(summary.maps.items.map((item) => item.label)).toEqual(['Martigny', 'Custom area 1']);
  });

  it('totals the downloads alone, leaving the shared tiles out (SNOW-1138)', () => {
    // Folded in, "Downloaded maps" read a larger figure than the Manage
    // downloads sheet's own total for the same device.
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 10 * MB }],
      baseLayers: [{ id: 'base-swisstopo_winter', name: 'Overview map', bytes: 2 * MB }],
    });

    expect(summary.maps.bytes).toBe(10 * MB);
    expect(summary.shared).toEqual({ bytes: 2 * MB, present: true });
  });

  it('states the same downloads total as the sheet’s budgetSummary (SNOW-1138)', async () => {
    // The sheet's "Using X of Y" is `budgetSummary` over its list, which
    // excludes the base layers and holds 0 bytes for an account-only row.
    // Same areas in, same figure out — the claim docs/offline-map.md makes.
    await import('../../static/js/basemap_download_core.js');
    await import('../../static/js/basemap_manage_core.js');
    const download = window.pwaBasemapDownloadCore;
    const manage = window.pwaBasemapManageCore;
    const areas = manage.reconcileAreas(
      [
        { id: 'region-ch-4115', name: 'Martigny', bytes: 10 * MB, savedAt: '', basemapKey: 'a' },
        { id: 'base-swisstopo_winter', name: 'Overview', bytes: 7 * MB, basemapKey: 'swisstopo_winter' },
      ],
      ['region-ch-4115', 'base-swisstopo_winter', 'region-ch-9999'],
      { 'region-ch-9999': 3 * MB },
      [{ area_id: 'region-fr-1', region_id: 'fr-1', name: 'Chamonix' }],
    );
    const sheetList = areas.filter((area) => !download.isBaseLayerAreaId(area.id));
    const sheetTotal = manage.budgetSummary(sheetList, 500 * MB).usedBytes;

    const summary = core.summarise({
      rows: manage.manageRows(areas, {
        isCustomAreaId: download.isCustomAreaId,
        isBaseLayerAreaId: download.isBaseLayerAreaId,
      }),
      baseLayers: areas.filter((area) => download.isBaseLayerAreaId(area.id)),
    });

    expect(sheetTotal).toBe(13 * MB);
    expect(summary.maps.bytes).toBe(sheetTotal);
    expect(summary.shared.bytes).toBe(7 * MB);
  });

  it('sums every base layer into one shared figure', () => {
    // One per basemap is how they are STORED; a device with two basemaps
    // painted two rows reading "Overview map" and differing only in size,
    // which names the app's internals and answers nothing the user asked.
    // The reset takes all or none, so one figure is the whole truth.
    const summary = core.summarise({
      rows: [],
      baseLayers: [
        { id: 'base-swisstopo_winter', name: 'Overview map', bytes: 2.7 * MB },
        { id: 'base-openfreemap', name: 'Overview map', bytes: 12.6 * MB },
      ],
    });

    expect(summary.maps.items).toEqual([]);
    expect(summary.shared.present).toBe(true);
    expect(summary.shared.bytes).toBeCloseTo(15.3 * MB);
  });

  it('reports no shared tiles when there are no base layers', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 10 * MB }],
      baseLayers: [],
    });

    expect(summary.shared).toEqual({ bytes: 0, present: false });
  });

  it('counts a base layer whose record has not landed, at 0 bytes', () => {
    // SNOW-863: the bucket is written by the service worker's warm and the
    // record by the page once that resolves. A reader who closes the tab in
    // between holds complete tiles and no size for them. The tiles are on
    // the device either way, so the line has to appear — an unsized line
    // is a small lie, an omitted one is a bigger one.
    const summary = core.summarise({
      rows: [],
      baseLayers: [{ id: 'base-swisstopo_winter', name: 'Overview map' }],
    });

    expect(summary.shared).toEqual({ bytes: 0, present: true });
    expect(summary.maps.bytes).toBe(0);
  });

  it('drops an account-only row — this reset cannot delete it', () => {
    // SNOW-749 rows carry `onDevice`. An area recorded against the account
    // but not downloaded here survives the wipe, so counting it would
    // promise a deletion that does not happen.
    const summary = core.summarise({
      rows: [
        { id: 'region-ch-4115', label: 'Martigny', bytes: 10 * MB },
        { id: 'region-fr-1', label: 'Chamonix', bytes: 40 * MB, onDevice: false },
      ],
    });

    expect(summary.maps.items.map((item) => item.label)).toEqual(['Martigny']);
    expect(summary.maps.bytes).toBe(10 * MB);
  });

  it('falls back to the id when a row has no label, and never to NaN bytes', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', bytes: 'not a number' }],
    });

    expect(summary.maps.items[0].label).toBe('region-ch-4115');
    expect(summary.maps.items[0].bytes).toBe(0);
  });

  it('reads as empty when nothing was passed at all', () => {
    const summary = core.summarise();

    expect(summary.maps.items).toEqual([]);
    expect(summary.maps.bytes).toBe(0);
    expect(summary.totalBytes).toBe(0);
  });
});

describe('the unsent-changes category', () => {
  it('warns only when there is something queued', () => {
    expect(core.summarise({ mutationCount: 0 }).unsent).toEqual({ count: 0, warn: false });
    expect(core.summarise({ mutationCount: 3 }).unsent).toEqual({ count: 3, warn: true });
  });

  it('treats an unreadable count as zero rather than as a warning', () => {
    // A device that cannot read its own queue must not be told it is about
    // to lose an unknown number of changes — that is a scare, not a fact.
    expect(core.summarise({ mutationCount: undefined }).unsent.warn).toBe(false);
    expect(core.summarise({ mutationCount: -4 }).unsent.count).toBe(0);
  });
});

describe('the cached-pages category', () => {
  it('takes the shared tiles off the origin usage too', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      baseLayers: [{ id: 'base-swisstopo_winter', bytes: 20 * MB }],
      storageEstimate: { usage: 130 * MB },
    });

    expect(summary.cached.bytes).toBe(10 * MB);
  });

  it('is the origin usage minus what the downloads account for', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      storageEstimate: { usage: 130 * MB, quota: 2000 * MB },
    });

    expect(summary.cached.known).toBe(true);
    expect(summary.cached.bytes).toBe(30 * MB);
  });

  it('floors at zero when the browser reports less than we recorded', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      storageEstimate: { usage: 60 * MB },
    });

    expect(summary.cached.bytes).toBe(0);
  });

  it('says the size is unknown rather than guessing when estimate() is absent', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      storageEstimate: null,
    });

    expect(summary.cached.known).toBe(false);
    expect(summary.cached.bytes).toBeNull();
  });

  it('treats a non-numeric usage the same as no estimate at all', () => {
    const summary = core.summarise({ storageEstimate: { quota: 1000 } });

    expect(summary.cached.known).toBe(false);
    expect(summary.cached.bytes).toBeNull();
  });
});

describe('the total pwa_reset.js quotes', () => {
  it('is the maps, the shared tiles and the cached remainder', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      baseLayers: [{ id: 'base-swisstopo_winter', name: 'Overview map', bytes: 20 * MB }],
      storageEstimate: { usage: 130 * MB },
    });

    expect(summary.totalBytes).toBe(130 * MB);
    expect(summary.totalIsPartial).toBe(false);
  });

  it('falls back to the maps alone, flagged partial, with no estimate', () => {
    const summary = core.summarise({
      rows: [{ id: 'region-ch-4115', label: 'Martigny', bytes: 100 * MB }],
      storageEstimate: null,
    });

    expect(summary.totalBytes).toBe(100 * MB);
    expect(summary.totalIsPartial).toBe(true);
  });
});
