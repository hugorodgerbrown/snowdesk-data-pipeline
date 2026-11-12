/*
 * tests/js/test_map_downloads_sync_all.js — Vitest tests for
 * static/js/map_downloads_sync_all.js (SNOW-1420).
 *
 * The Downloads sheet's answer to "will this work offline?": one verdict
 * per stored area, read from the record alone, and one Sync all over every
 * area on this device. The sheet's painting of both is covered in
 * tests/js/test_map_downloads_manager.js; this file covers the module's
 * own contract with real cores (offline_journey_core.js,
 * offline_ready_core.js, basemap_download_core.js) and stubs only what
 * jsdom has no copy of: IndexedDB (`window.pwaDb`), the map's sync bridge
 * (`window.pwaBasemapDownloads.syncArea`) and the mutation queue.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const TODAY = new Date('2026-11-04T09:00:00.000Z');
const YESTERDAY = '2026-11-03T09:00:00.000Z';
const PRINCIPAL = 'user-7';

/**
 * Stand-in for `window.pwaDb`, holding the two stored area lists and the
 * mutation queue.
 *
 * @param {{regions?: Array<Object>, customAreas?: Array<Object>, mutations?: Array<Object>}} seed
 */
function installDb(seed) {
  const meta = new Map([
    ['basemap.regions', seed.regions || []],
    ['basemap.customAreas', seed.customAreas || []],
  ]);
  window.pwaDb = {
    get: vi.fn(async (_store, key) => (meta.has(key) ? { key, value: meta.get(key) } : undefined)),
    getAll: vi.fn(async () => seed.mutations || []),
  };
}

/** The page's account, as base.html renders it. */
function setPrincipal(value) {
  document.head.innerHTML = `<meta name="pwa-user-id" content="${value}">`;
}

async function loadModule() {
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/offline_journey_core.js');
  await import('../../static/js/offline_ready_core.js');
  await import('../../static/js/map_downloads_sync_all.js');
  return window.pwaDownloadsSyncAll;
}

/** A record every stage of which says yes, for the signed-in principal. */
function readyRecord(extra) {
  return {
    contentAt: TODAY.toISOString(),
    verified: { map: 'yes', content: 'yes', principal: PRINCIPAL, at: TODAY.toISOString() },
    ...extra,
  };
}

beforeEach(() => {
  setPrincipal(PRINCIPAL);
  window.pwaMutationQueue = { drain: vi.fn(async () => {}) };
});

afterEach(() => {
  delete window.pwaDb;
  delete window.pwaBasemapDownloads;
  delete window.pwaMutationQueue;
  delete window.pwaDownloadsSyncAll;
  document.head.innerHTML = '';
});

describe('records', () => {
  it('keys regions by their bucket id and custom areas by their own id', async () => {
    installDb({
      regions: [{ region_id: 'CH-2101', name: 'Aletsch' }],
      customAreas: [{ id: 'custom-a1', name: 'Home' }],
    });
    const syncAll = await loadModule();

    const byId = await syncAll.records();

    const regionId = self.pwaBasemapDownloadCore.areaIdForRegion('CH-2101');
    expect([...byId.keys()]).toEqual([regionId, 'custom-a1']);
    expect(byId.get(regionId).name).toBe('Aletsch');
  });

  it('answers an empty map when IndexedDB will not', async () => {
    window.pwaDb = { get: vi.fn(async () => Promise.reject(new Error('blocked'))) };
    const syncAll = await loadModule();

    expect((await syncAll.records()).size).toBe(0);
  });
});

describe('verdict', () => {
  it('says ready when every stage is yes for this account', async () => {
    const syncAll = await loadModule();

    expect(syncAll.verdict(readyRecord(), TODAY)).toEqual({ verdict: 'ready', reason: '' });
  });

  it('gives the bulletins as the reason when they are from another day', async () => {
    const syncAll = await loadModule();

    expect(syncAll.verdict(readyRecord({ contentAt: YESTERDAY }), TODAY)).toEqual({
      verdict: 'refresh',
      reason: 'conditions',
    });
  });

  it("gives the trips and pins as the reason when this account's things fell short", async () => {
    const syncAll = await loadModule();
    const record = readyRecord();
    record.verified.content = 'no';

    expect(syncAll.verdict(record, TODAY)).toEqual({ verdict: 'refresh', reason: 'things' });
  });

  it("reads another account's things answer as unchecked, never as a yes", async () => {
    setPrincipal('user-8');
    const syncAll = await loadModule();

    expect(syncAll.verdict(readyRecord(), TODAY).verdict).not.toBe('ready');
  });

  it('says the map is missing when the last check found it short', async () => {
    const syncAll = await loadModule();
    const record = readyRecord();
    record.verified = { map: 'no', reason: 'tiles', at: TODAY.toISOString() };

    expect(syncAll.verdict(record, TODAY).verdict).toBe('open-map');
  });

  it('says "not checked" for an unchecked map, even with stale bulletins', async () => {
    // verdictFor alone would rank the stale bulletins first and say
    // "refresh", which claims the map is there when nothing has looked.
    const syncAll = await loadModule();

    expect(syncAll.verdict({ contentAt: YESTERDAY }, TODAY)).toEqual({
      verdict: 'unknown',
      reason: '',
    });
  });

  it('says "not checked" for an area with no record', async () => {
    const syncAll = await loadModule();

    expect(syncAll.verdict(undefined, TODAY).verdict).toBe('unknown');
  });

  // SNOW-1483: the dot reads terrain, as the inventory's Terrain row does.
  it('gives terrain as the reason for an area saved without it that should hold it', async () => {
    const syncAll = await loadModule();
    window.pwaBasemapDownloads = { terrainExpected: vi.fn(() => true) };
    const record = readyRecord();

    expect(syncAll.verdict(record, TODAY)).toEqual({ verdict: 'refresh', reason: 'terrain' });
    expect(window.pwaBasemapDownloads.terrainExpected).toHaveBeenCalledWith(record);
    // The row's inventory says the same thing, from the same flag.
    const rows = window.pwaOfflineJourneyCore.inventoryFor(record, TODAY, {
      terrainAvailable: true,
      signedIn: true,
      principal: PRINCIPAL,
    });
    expect(rows.find((row) => row.key === 'terrain').status).toBe('missing');
    expect(rows.find((row) => row.key === 'map').status).toBe('yes');
  });

  it('says ready for an area with its terrain, or one that should hold none', async () => {
    const syncAll = await loadModule();
    window.pwaBasemapDownloads = { terrainExpected: () => true };
    expect(syncAll.verdict(readyRecord({ terrain: true }), TODAY).verdict).toBe('ready');

    window.pwaBasemapDownloads = { terrainExpected: () => false };
    expect(syncAll.verdict(readyRecord(), TODAY).verdict).toBe('ready');
  });

  it('keeps a missing map red whatever its terrain', async () => {
    const syncAll = await loadModule();
    window.pwaBasemapDownloads = { terrainExpected: () => true };
    const record = readyRecord();
    record.verified = { map: 'no', reason: 'tiles', at: TODAY.toISOString() };

    expect(syncAll.verdict(record, TODAY).verdict).toBe('open-map');
  });
});

describe('press — Sync all', () => {
  it('drains the queue, then syncs every area in the order given', async () => {
    installDb({});
    const order = [];
    window.pwaMutationQueue.drain = vi.fn(async () => order.push('drain'));
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(async (id) => {
        order.push(id);
        return { tiles: 'ok', content: true };
      }),
    };
    const syncAll = await loadModule();

    const result = await syncAll.press(['a', 'b'], () => {});

    expect(order).toEqual(['drain', 'a', 'b']);
    expect(result).toMatchObject({ total: 2, ready: 2, waiting: 0 });
    expect(syncAll.closing(result, ['a', 'b'])).toEqual({
      areas: [
        { id: 'a', outcome: 'ready' },
        { id: 'b', outcome: 'ready' },
      ],
      queue: '',
    });
  });

  it('names the area in flight while it runs, and nothing once it settles', async () => {
    installDb({});
    let release;
    const seen = [];
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(
        () =>
          new Promise((resolve) => {
            release = () => resolve({ tiles: 'ok', content: true });
          }),
      ),
    };
    const syncAll = await loadModule();

    const running = syncAll.press(['a'], () => seen.push(syncAll.state()));
    await vi.waitFor(() => expect(syncAll.state().current).toBe('a'));
    expect(syncAll.state().running).toBe(true);
    expect(syncAll.state().label).toBe('Syncing 1 of 1 areas');

    release();
    await running;

    expect(syncAll.state()).toEqual({
      running: false,
      current: '',
      label: '',
      index: 0,
      count: 0,
      step: null,
      pending: [],
    });
    // One repaint to start, one per step, one to settle.
    expect(seen[0].running).toBe(true);
    expect(seen.at(-1).running).toBe(false);
  });

  it('ignores a second press while one is running', async () => {
    installDb({});
    let release;
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(
        () =>
          new Promise((resolve) => {
            release = () => resolve({ tiles: 'ok', content: true });
          }),
      ),
    };
    const syncAll = await loadModule();

    const first = syncAll.press(['a'], () => {});
    await vi.waitFor(() => expect(syncAll.state().current).toBe('a'));

    expect(await syncAll.press(['a'], () => {})).toBeNull();
    release();
    await first;
    expect(window.pwaBasemapDownloads.syncArea).toHaveBeenCalledTimes(1);
  });

  it('says what fell short: areas not brought up to date, and edits still waiting', async () => {
    installDb({ mutations: [{ status: 'pending' }] });
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(async (id) => (id === 'a' ? { tiles: 'ok', content: true } : { tiles: 'ok', content: false })),
    };
    const syncAll = await loadModule();

    const result = await syncAll.press(['a', 'b'], () => {});

    expect(syncAll.closing(result, ['a', 'b'])).toEqual({
      areas: [
        { id: 'a', outcome: 'ready' },
        { id: 'b', outcome: 'unfinished' },
      ],
      queue: '1 change still waiting to send',
    });
  });

  it('settles as not finished when the sync bridge throws', async () => {
    installDb({});
    window.pwaBasemapDownloads = { syncArea: vi.fn(async () => Promise.reject(new Error('x'))) };
    const syncAll = await loadModule();

    const result = await syncAll.press(['a'], () => {});

    expect(syncAll.state().running).toBe(false);
    expect(syncAll.closing(result, ['a']).areas).toEqual([{ id: 'a', outcome: 'unfinished' }]);
  });

  // SNOW-1487: the run says which area of how many it is on, and how far.
  it('says which area of how many it is on, its step and counts, and which wait', async () => {
    installDb({});
    let release;
    let stage;
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(async (id, onStage) => {
        if (id !== 'b') return { tiles: 'none', tilesHeld: true, content: true, steps: [] };
        stage = onStage;
        return new Promise((resolve) => {
          release = () => resolve({ tiles: 'none', tilesHeld: true, content: true, steps: [] });
        });
      }),
    };
    const syncAll = await loadModule();
    const changes = [];

    const running = syncAll.press(['a', 'b', 'c'], (change) => changes.push(change));
    await vi.waitFor(() => expect(syncAll.state().current).toBe('b'));
    stage('map', { done: 0, total: 528 });
    stage('map', { done: 212, total: 528 });

    expect(syncAll.state()).toMatchObject({
      running: true,
      current: 'b',
      label: 'Syncing 2 of 3 areas',
      index: 2,
      count: 3,
      step: { name: 'map', done: 212, total: 528 },
      pending: ['c'],
    });
    // A move of the counts alone is a progress repaint.
    expect(changes.at(-1)).toEqual({ progress: true });

    release();
    await running;
  });

  it('a failed addition is "did not finish", and a stop "stopped"', async () => {
    const syncAll = await loadModule();
    const step = { step: 'basemap', reason: 'network', done: 0, total: 10 };

    expect(syncAll.outcomeOf({ tiles: 'none', content: true, steps: [step] })).toBe('unfinished');
    expect(syncAll.outcomeOf({ tiles: 'none', content: true, steps: [] })).toBe('ready');
    expect(syncAll.outcomeOf({ tiles: 'none', content: false, steps: [], cancelled: true })).toBe('stopped');
    expect(syncAll.outcomeOf({ tiles: 'absent', content: false })).toBe('absent');
  });

  it('Stop ends the area in flight and skips the rest', async () => {
    installDb({});
    let seenSignal;
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(
        (_id, _onStage, signal) =>
          new Promise((resolve) => {
            seenSignal = signal;
            signal.addEventListener('abort', () =>
              resolve({ tiles: 'unknown', content: false, steps: [], cancelled: true }),
            );
          }),
      ),
    };
    const syncAll = await loadModule();

    const running = syncAll.press(['a', 'b', 'c'], () => {});
    await vi.waitFor(() => expect(syncAll.state().current).toBe('a'));
    syncAll.stop();
    const result = await running;

    expect(seenSignal.aborted).toBe(true);
    expect(window.pwaBasemapDownloads.syncArea).toHaveBeenCalledTimes(1);
    expect(result.skipped).toEqual(['b', 'c']);
    expect(syncAll.closing(result, ['a', 'b', 'c']).areas.map((area) => area.outcome)).toEqual([
      'stopped',
      'stopped',
      'stopped',
    ]);
    expect(syncAll.state().running).toBe(false);
  });

  it('stops when the page goes out of sight', async () => {
    installDb({});
    let seenSignal;
    window.pwaBasemapDownloads = {
      syncArea: vi.fn(
        (_id, _onStage, signal) =>
          new Promise((resolve) => {
            seenSignal = signal;
            signal.addEventListener('abort', () => resolve({ tiles: 'unknown', content: false, cancelled: true }));
          }),
      ),
    };
    const syncAll = await loadModule();

    const running = syncAll.press(['a'], () => {});
    await vi.waitFor(() => expect(syncAll.state().current).toBe('a'));
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    try {
      document.dispatchEvent(new Event('visibilitychange'));
      await running;
    } finally {
      Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    }

    expect(seenSignal.aborted).toBe(true);
  });

  it('does nothing on a page with no sync bridge', async () => {
    installDb({});
    const syncAll = await loadModule();

    expect(await syncAll.press(['a'], () => {})).toBeNull();
    expect(syncAll.state().running).toBe(false);
  });
});

describe('one content run at a time (SNOW-1416)', () => {
  /** A stand-in for pwaBasemapDownloads.contentRun. */
  function contentRun(initial) {
    const run = {
      owner: initial || '',
      areaId: '',
      take: vi.fn(async () => {
        run.owner = 'user';
        return true;
      }),
      release: vi.fn((owner) => {
        if (run.owner === owner) run.owner = '';
      }),
      current: () => run.owner,
      area: () => run.areaId || '',
    };
    return run;
  }

  it('takes the run as the user, and gives it back once it settles', async () => {
    installDb({});
    const run = contentRun();
    const seen = [];
    window.pwaBasemapDownloads = {
      contentRun: run,
      syncArea: vi.fn(async () => {
        seen.push(run.current());
        return { tiles: 'ok', content: true };
      }),
    };
    const syncAll = await loadModule();

    await syncAll.press(['a'], () => {});

    expect(run.take).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(['user']);
    expect(run.release).toHaveBeenCalledWith('user');
    expect(run.current()).toBe('');
  });

  it('gives the run back when the sync throws', async () => {
    installDb({});
    const run = contentRun();
    window.pwaBasemapDownloads = {
      contentRun: run,
      syncArea: vi.fn(async () => Promise.reject(new Error('x'))),
    };
    const syncAll = await loadModule();

    await syncAll.press(['a'], () => {});

    expect(run.release).toHaveBeenCalledWith('user');
  });

  it('runs nothing when another user run holds it', async () => {
    installDb({});
    const run = contentRun();
    run.take = vi.fn(async () => false);
    window.pwaBasemapDownloads = { contentRun: run, syncArea: vi.fn() };
    const syncAll = await loadModule();

    expect(await syncAll.press(['a'], () => {})).toBeNull();
    expect(window.pwaBasemapDownloads.syncArea).not.toHaveBeenCalled();
    expect(syncAll.state().running).toBe(false);
  });

  it("reports the background sync's area, so its row shows it syncing", async () => {
    installDb({});
    const run = contentRun('auto');
    run.areaId = 'custom-a1';
    window.pwaBasemapDownloads = { contentRun: run, syncArea: vi.fn() };
    const syncAll = await loadModule();

    expect(syncAll.state()).toMatchObject({ running: false, current: 'custom-a1', label: '' });
  });
});
