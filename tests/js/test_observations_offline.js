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
 * The Reset Required case has its own file
 * (test_observations_offline_reset_required.js) for the reason
 * test_db_reset_required.js does: db.js assigns a frozen, non-configurable
 * window.pwaDb, and Vitest gives a fresh module graph per FILE — so the only
 * way to hand this module a DB in that state is not to import the real one.
 */

import { beforeEach, describe, expect, it } from 'vitest';

// db.js first: it assigns window.pwaDb, which every entry point below reads
// through.
await import('../../static/js/db.js');
await import('../../static/js/observations_offline.js');

const STORE = 'data:panel_rows';
const KEY = 'observations';
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

let rows;

beforeEach(async () => {
  await window.pwaDb.delete(STORE, KEY);
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
});

describe('the public surface', () => {
  it('is frozen, like every other window.pwa* bridge', () => {
    expect(Object.isFrozen(window.pwaObservationsOffline)).toBe(true);
  });
});
