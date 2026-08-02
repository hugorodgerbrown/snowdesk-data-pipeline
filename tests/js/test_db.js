/*
 * tests/js/test_db.js — Vitest unit tests for static/js/db.js (SNOW-375, SNOW-495).
 *
 * Ports 9 of the 10 cases from the retired `tests/e2e/test_pwa_db.py`
 * (Playwright) onto the Vitest + jsdom + fake-indexeddb harness added in
 * SNOW-495 — the tenth (Reset Required) lives in its own file,
 * `test_db_reset_required.js`, because db.js's `_resetRequired` flag is a
 * one-way latch for the module's lifetime and Vitest only guarantees a
 * fresh module per test FILE, not per test.
 *
 * `db.js` is a browser IIFE that assigns a frozen, non-configurable
 * `window.pwaDb` — importing it for side effects runs the IIFE once per
 * file. `beforeEach` deletes the underlying IndexedDB database for a clean
 * slate; this fires db.js's own `onversionchange` handler (which drops its
 * memoised connection), the in-process equivalent of the Playwright
 * `_delete_db` helper. The Playwright-only page-navigation and
 * background-`/api`-feed-blocking scaffolding is dropped — there is no page
 * or map.js here, so the sync-log tests call the API directly.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/db.js';

const DB_NAME = 'snowdesk-pwa-v1';
const STATIC_STORES = ['queue:mutations', 'queue:events', 'meta:sync', 'meta:app'];
// SNOW-418 (schema v2) — added alongside the four version-1 stores above.
const V2_STORES = [...STATIC_STORES, 'data:favourites'];
// SNOW-482 (schema v3) — added alongside the five version-2 stores above.
const V3_STORES = [...V2_STORES, 'log:sync'];
// SNOW-492 (schema v4) — added alongside the six version-3 stores above.
const V4_STORES = [...V3_STORES, 'data:map_overlays'];

/**
 * Delete the PWA database and wait for the deletion to actually complete.
 *
 * Mirrors `tests/e2e/test_pwa_db.py::_delete_db` — deliberately does not
 * resolve on `onblocked` (a pending, not yet completed, delete): db.js
 * yields its memoised connection on `versionchange`, so a blocked delete
 * unblocks itself and `onsuccess` still fires.
 */
function deleteDb() {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.deleteDatabase(DB_NAME);
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
    } catch (_e) {
      resolve();
    }
  });
}

beforeEach(async () => {
  await deleteDb();
});

// ---------------------------------------------------------------------------
// 1. Fresh open creates every static store at version 4.
// ---------------------------------------------------------------------------

describe('fresh open', () => {
  it('creates all seven stores at version 4', async () => {
    const db = await window.pwaDb.open();
    expect(db.version).toBe(4);
    expect(Array.from(db.objectStoreNames).sort()).toEqual([...V4_STORES].sort());
  });
});

// ---------------------------------------------------------------------------
// 2. put / get / delete / getAll / count / clear round-trip on queue:events.
// ---------------------------------------------------------------------------

describe('round trip on queue:events', () => {
  it('put returns a key, get / getAll return the row, delete removes it', async () => {
    const db = window.pwaDb;
    const store = 'queue:events';
    const key1 = await db.put(store, { event: 'pwa.sw.installed', ts: 1 });
    const key2 = await db.put(store, { event: 'pwa.sw.activated', ts: 2 });
    const one = await db.get(store, key1);
    const all = await db.getAll(store);
    const before = await db.count(store);
    await db.delete(store, key1);
    const after = await db.count(store);
    await db.clear(store);
    const cleared = await db.count(store);

    expect(key1).toBe(1);
    expect(key2).toBe(2);
    expect(one).toEqual({ event: 'pwa.sw.installed', ts: 1, id: 1 });
    expect(all.length).toBe(2);
    expect(before).toBe(2);
    expect(after).toBe(1);
    expect(cleared).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 3. context() shape.
// ---------------------------------------------------------------------------

describe('context()', () => {
  it('returns the seven envelope-context keys with sane defaults', () => {
    const ctx = window.pwaDb.context();

    expect(Object.keys(ctx).sort()).toEqual(
      [
        'session_id',
        'user_id',
        'client_version',
        'platform',
        'install_state',
        'sw_state',
        'connection',
      ].sort(),
    );
    // session_id is generated (random-ish string, non-empty).
    expect(typeof ctx.session_id).toBe('string');
    expect(ctx.session_id).not.toBe('');
    // user_id: anonymous by default in the test env.
    expect([null, '']).toContain(ctx.user_id);
    // client_version comes from <meta name="pwa-app-version">.
    expect(typeof ctx.client_version).toBe('string');
    // platform: web / ios / android — jsdom's default UA reads as web.
    expect(['web', 'ios', 'android']).toContain(ctx.platform);
    // install_state: standalone / browser — jsdom is browser.
    expect(ctx.install_state).toBe('browser');
    // sw_state: activated / none. jsdom has no serviceWorker, so 'none'.
    expect(['activated', 'none']).toContain(ctx.sw_state);
    // connection: unknown in jsdom (no NetworkInformation API).
    expect(typeof ctx.connection).toBe('string');
  });

  it('is stable within a page (module) lifetime', () => {
    const a = window.pwaDb.context();
    const b = window.pwaDb.context();

    expect(a).toBe(b);
    expect(a.session_id).toBe(b.session_id);
  });
});

// ---------------------------------------------------------------------------
// 4. Schema upgrade — a pre-existing v1 DB gains data:favourites (SNOW-418).
// ---------------------------------------------------------------------------

/**
 * Open a raw (not-via-db.js) connection at ``version`` with exactly
 * ``storeNames`` created, seed one row into `queue:events`, then close the
 * connection. Mirrors the manual `indexedDB.open` seeding blocks in the
 * retired Playwright tests.
 */
function seedLegacyDb(version, storeNames) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, version);
    req.onupgradeneeded = (evt) => {
      const d = evt.target.result;
      for (const name of storeNames) {
        if (name === 'data:favourites') {
          d.createObjectStore(name, { keyPath: 'uuid' });
        } else {
          d.createObjectStore(name, { keyPath: 'id', autoIncrement: true });
        }
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }).then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction('queue:events', 'readwrite');
        const putReq = tx.objectStore('queue:events').put({ event: 'pre-existing' });
        putReq.onsuccess = () => {
          db.close();
          resolve(putReq.result);
        };
        putReq.onerror = () => reject(putReq.error);
      }),
  );
}

/**
 * Open the DB through db.js and read back row 1 of `queue:events`.
 *
 * A raw connection opened by `seedLegacyDb` above may still be settling its
 * `close()` when db.js's upgrade transaction starts — db.js surfaces that as
 * a distinct `idb_blocked` rejection precisely so callers can retry (the
 * same contract a real second-tab consumer relies on), so retry until the
 * block clears rather than treating it as a genuine failure.
 */
async function openViaDbJsAndRead() {
  let db = null;
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      db = await window.pwaDb.open();
      break;
    } catch (err) {
      if (String(err && err.message) !== 'idb_blocked') throw err;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  if (!db) throw new Error('idb_blocked did not clear within retry budget');
  const row = await window.pwaDb.get('queue:events', 1);
  return { version: db.version, names: Array.from(db.objectStoreNames).sort(), row };
}

describe('schema upgrades', () => {
  it('a pre-existing v1 DB (four stores) gains every missing store', async () => {
    const seeded = await seedLegacyDb(1, STATIC_STORES);
    expect(seeded).toBe(1);

    const result = await openViaDbJsAndRead();
    expect(result.version).toBe(4);
    expect(result.names).toEqual([...V4_STORES].sort());
    expect(result.row).toEqual({ id: 1, event: 'pre-existing' });
  });

  it('a pre-existing v2 DB (five stores) gains log:sync + data:map_overlays', async () => {
    const seeded = await seedLegacyDb(2, V2_STORES);
    expect(seeded).toBe(1);

    const result = await openViaDbJsAndRead();
    expect(result.version).toBe(4);
    expect(result.names).toEqual([...V4_STORES].sort());
    expect(result.row).toEqual({ id: 1, event: 'pre-existing' });
  });

  it('a pre-existing v3 DB (six stores) gains data:map_overlays', async () => {
    const seeded = await seedLegacyDb(3, V3_STORES);
    expect(seeded).toBe(1);

    const result = await openViaDbJsAndRead();
    expect(result.version).toBe(4);
    expect(result.names).toEqual([...V4_STORES].sort());
    expect(result.row).toEqual({ id: 1, event: 'pre-existing' });
  });
});

// ---------------------------------------------------------------------------
// 5. Legacy meta:app key purge (SNOW-589).
//
// SNOW-570 wrote a `basemap.regions` row to `meta:app` on every successful
// per-region basemap download; SNOW-587 removed the "downloaded areas"
// rings overlay that was its only reader. SNOW-589 removed the write and
// added `_purgeLegacyKeys`, which deletes any row keyed by a name in
// `LEGACY_META_APP_KEYS` the moment the DB is opened, so a row written
// before SNOW-589 doesn't linger in a device's IndexedDB forever.
// ---------------------------------------------------------------------------

/**
 * Open a raw (not-via-db.js) connection at version 4 with every v4 store
 * created, put each of ``rows`` into `meta:app`, then close the connection.
 * Mirrors `seedLegacyDb` above, but at the current schema version — the
 * purge runs on every open, not just an upgrading one, so the row must
 * exist BEFORE db.js's own `open()` call for these tests to be meaningful.
 */
function seedMetaAppRows(rows) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 4);
    req.onupgradeneeded = (evt) => {
      const d = evt.target.result;
      d.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
      d.createObjectStore('queue:events', { keyPath: 'id', autoIncrement: true });
      d.createObjectStore('meta:sync', { keyPath: 'resource' });
      d.createObjectStore('meta:app', { keyPath: 'key' });
      d.createObjectStore('data:favourites', { keyPath: 'uuid' });
      d.createObjectStore('log:sync', { keyPath: 'id', autoIncrement: true });
      d.createObjectStore('data:map_overlays', { keyPath: 'key' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }).then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction('meta:app', 'readwrite');
        for (const row of rows) {
          tx.objectStore('meta:app').put(row);
        }
        tx.oncomplete = () => {
          db.close();
          resolve();
        };
        tx.onerror = () => reject(tx.error);
      }),
  );
}

describe('legacy meta:app key purge', () => {
  it('purges an existing basemap.regions row on open', async () => {
    await seedMetaAppRows([{ key: 'basemap.regions', value: [{ region_id: 'ch-vs' }] }]);

    await window.pwaDb.open();

    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    expect(row).toBeUndefined();
  });

  it('is a no-op on a clean DB with no legacy row', async () => {
    await window.pwaDb.open();

    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    expect(row).toBeUndefined();

    // Other meta:app reads/writes still work — the purge doesn't wedge the
    // store or the connection.
    await window.pwaDb.put('meta:app', { key: 'some.other.key', value: 'still works' });
    const other = await window.pwaDb.get('meta:app', 'some.other.key');
    expect(other).toEqual({ key: 'some.other.key', value: 'still works' });
  });

  it('leaves unrelated meta:app rows alone', async () => {
    await seedMetaAppRows([
      { key: 'basemap.regions', value: [{ region_id: 'ch-vs' }] },
      { key: 'basemap.customArea', value: { bbox: [1, 2, 3, 4] } },
    ]);

    await window.pwaDb.open();

    const legacy = await window.pwaDb.get('meta:app', 'basemap.regions');
    const unrelated = await window.pwaDb.get('meta:app', 'basemap.customArea');
    expect(legacy).toBeUndefined();
    expect(unrelated).toEqual({ key: 'basemap.customArea', value: { bbox: [1, 2, 3, 4] } });
  });
});

// ---------------------------------------------------------------------------
// 6. appendSyncLog / getSyncLog (SNOW-482).
// ---------------------------------------------------------------------------

describe('log:sync helpers', () => {
  it('appendSyncLog trims to the newest 100 rows', async () => {
    for (let i = 1; i <= 105; i++) {
      await window.pwaDb.appendSyncLog({ at: new Date().toISOString(), path: `sync-${i}` });
    }
    const count = await window.pwaDb.count('log:sync');
    const all = await window.pwaDb.getAll('log:sync');
    const paths = all.map((row) => row.path).sort();

    expect(count).toBe(100);
    const expected = Array.from({ length: 100 }, (_, i) => `sync-${i + 6}`).sort();
    expect(paths).toEqual(expected);
  });

  it('getSyncLog reads back rows newest-first, honouring limit', async () => {
    for (let i = 1; i <= 5; i++) {
      await window.pwaDb.appendSyncLog({ at: new Date().toISOString(), path: `sync-${i}` });
    }
    const all = await window.pwaDb.getSyncLog();
    const limited = await window.pwaDb.getSyncLog(2);

    expect(all.map((row) => row.path)).toEqual(['sync-5', 'sync-4', 'sync-3', 'sync-2', 'sync-1']);
    expect(limited.map((row) => row.path)).toEqual(['sync-5', 'sync-4']);
  });
});
