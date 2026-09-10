/*
 * tests/js/test_observations_offline.js — the field-observation panel's
 * offline row store (SNOW-661, static/js/observations_offline.js).
 *
 * The module is storage only: it writes what came back from
 * ``observations:list`` and reads it out again. Everything it does is
 * jsdom's to hold — one htmx lifecycle event and an IndexedDB round trip —
 * so this is the whole of its coverage and none of it belongs in a browser.
 * What ``report.js`` then paints from a hit is covered in
 * tests/js/test_report_panel.js.
 *
 * The two cases worth being careful about are the ones that make the cache
 * safe rather than useful: a swap of somebody else's container must not be
 * written here (SNOW-722's ownership lesson, which cost the routes panel a
 * list of favourites), and a row cached under one account must not read
 * back into another's session on a shared browser.
 *
 * The warm is exercised here rather than in test_panel_rows_cache.js
 * because the assertion is about what lands in IndexedDB, and this file is
 * where the DB harness lives. It is the path that decides whether the
 * feature works at all for the user it was written for — someone who loads
 * the map with signal and opens the panel without it — so it is asserted
 * end to end, through the real panel_rows_cache.js rather than by calling
 * ``write()`` directly.
 *
 * The Reset Required case has its own file
 * (test_observations_offline_reset_required.js) for the reason
 * test_db_reset_required.js does: db.js assigns a frozen, non-configurable
 * window.pwaDb, and Vitest gives a fresh module graph per FILE — so the only
 * way to hand this module a DB in that state is not to import the real one.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// db.js first: it assigns window.pwaDb, which every entry point below reads
// through. panel_rows_cache.js next, because the module under test
// registers its warm handler against window.pwaPanelRows at parse time —
// the same document order the page's deferred script tags give it.
await import('../../static/js/db.js');
await import('../../static/js/panel_rows_cache.js');
await import('../../static/js/observations_offline.js');

const STORE = 'data:panel_rows';
const KEY = 'observations';
const LIST_URL = '/observations/partials/list/';
const ROWS = '<ul><li id="observation-a1b2">Whumpfing</li></ul>';

/**
 * Replay one htmx swap against ``target``, the way htmx 2.0.4 raises it.
 *
 * @param {Element} target The swap's target container.
 * @param {{body?: string, isError?: boolean}} [options]
 * @returns {void}
 */
function replaySwap(target, options) {
  const opts = options || {};
  document.body.dispatchEvent(
    new CustomEvent('htmx:beforeSwap', {
      detail: {
        target: target,
        serverResponse: 'body' in opts ? opts.body : ROWS,
        isError: !!opts.isError,
        shouldSwap: !opts.isError,
      },
    }),
  );
}

/** Poll `predicate` until truthy, or fail after `timeoutMs`. */
async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`condition never became true (last=${JSON.stringify(last)})`);
}

/**
 * Let the write-through pass settle. The listener is fire-and-forget, so
 * there is nothing to await but the row's arrival.
 *
 * @returns {Promise<object>}
 */
function settled() {
  return waitFor(() => window.pwaDb.get(STORE, KEY));
}

/**
 * Run one warm through the real panel_rows_cache.js against a stubbed
 * fetch.
 *
 * ``requestIdleCallback`` is stubbed to run its callback immediately rather
 * than driving the module's setTimeout fallback with fake timers: the DB
 * write this test is about is asserted with the real clock, and the
 * fallback branch is covered in test_panel_rows_cache.js.
 *
 * @param {string} key The panel key to warm.
 * @param {{ok?: boolean, body?: string, offline?: boolean}} [options]
 *   ``ok: false`` for a non-2xx response, ``offline: true`` for a fetch
 *   that rejects.
 * @returns {Promise<void>}
 */
async function runWarm(key, options) {
  const opts = options || {};
  const response = {
    ok: opts.ok !== false,
    text: () => Promise.resolve('body' in opts ? opts.body : ROWS),
  };
  vi.stubGlobal('requestIdleCallback', (fn) => fn());
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      opts.offline
        ? Promise.reject(new Error('offline'))
        : Promise.resolve(response),
    ),
  );
  window.pwaPanelRows.warm(key, LIST_URL);
  await Promise.resolve();
  vi.unstubAllGlobals();
}

let rows;

beforeEach(async () => {
  await window.pwaDb.delete(STORE, KEY);
  // The cache is module-level and outlives a test, and a warm for a key it
  // already holds is a no-op.
  window.pwaPanelRows.invalidate(KEY);
  window.pwaPanelRows.invalidate('routes');
  document.body.innerHTML =
    '<div data-report-rows></div><div data-routes-rows></div>';
  rows = document.querySelector('[data-report-rows]');
});

describe('write-through', () => {
  it('persists the rendered rows of a successful list swap', async () => {
    replaySwap(rows);

    const record = await settled();
    expect(record.body).toBe(ROWS);
    // Verbatim response text, not the swapped innerHTML — the markup the
    // server translated is the whole point of caching the body.
    expect(typeof record.cached_at).toBe('string');
  });

  it('does not persist an error response', async () => {
    replaySwap(rows, { body: '<p>Server error</p>', isError: true });

    // Nothing to wait ON — assert the absence after the microtask queue the
    // listener would have written in has drained.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, KEY)).toBeUndefined();
  });

  it('does not persist a swap of some other panel', async () => {
    // SNOW-722: this listener is bound to document.body and is offered every
    // swap on the page. The routes panel's rows arriving here would repaint
    // the observations panel with somebody else's list.
    replaySwap(document.querySelector('[data-routes-rows]'));

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, KEY)).toBeUndefined();
  });
});

describe('the idle warm', () => {
  it('persists the rows without the panel ever being opened', async () => {
    // The user this feature is for: the map loaded with signal, the panel
    // opened without it. report.js warms at module init on every map page
    // load, and that warm raises no htmx event — so if it does not write
    // through here, nothing does until an online open.
    await runWarm(KEY);

    const record = await settled();
    expect(record.body).toBe(ROWS);
    expect(record.principal).toBeNull();
  });

  it("persists nothing for another panel's key", async () => {
    await runWarm('routes');

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, KEY)).toBeUndefined();
  });

  it('persists nothing for a non-2xx response', async () => {
    await runWarm(KEY, { ok: false, body: '<p>Server error</p>' });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, KEY)).toBeUndefined();
  });

  it('persists nothing when the fetch fails outright', async () => {
    await runWarm(KEY, { offline: true });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, KEY)).toBeUndefined();
  });
});

describe('read back', () => {
  it('returns the cached rows', async () => {
    replaySwap(rows);
    await settled();

    const record = await window.pwaObservationsOffline.read();
    expect(record.body).toBe(ROWS);
  });

  it('returns null when nothing is cached', async () => {
    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });

  it('returns null for a row cached under another account', async () => {
    // The anonymous session this harness runs as writes principal: null, so
    // a row stamped with an account is one this session must not read.
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: ROWS,
      cached_at: new Date().toISOString(),
      principal: 'user-42',
    });

    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });

  it('returns null for a row carrying no principal at all', async () => {
    // A row written before this partitioning existed belongs to nobody, so
    // it matches nobody — including the anonymous session this harness runs
    // as, whose own principal is null. Normalising the absent key to null
    // would hand somebody else's rows to an anonymous reader, which is the
    // SNOW-493 fault the account-specific overlay rows already avoid by
    // comparing the stored value untouched.
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: ROWS,
      cached_at: new Date().toISOString(),
    });

    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });
});

describe('the public surface', () => {
  it('is frozen, like every other window.pwa* bridge', () => {
    expect(Object.isFrozen(window.pwaObservationsOffline)).toBe(true);
  });
});
