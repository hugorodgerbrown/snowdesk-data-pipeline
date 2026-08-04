/*
 * tests/js/test_favourites_offline.js — Vitest unit tests for
 * static/js/favourites_offline.js's write-through reconcile (finding M13 in
 * docs/code-reviews/2026-08-03-js-review.md).
 *
 * The roster sidecar the server renders alongside the favourites list is the
 * user's whole current list (apps/favourites/views.py::favourite_list), so a
 * uuid the cache holds but the roster omits is a favourite that no longer
 * exists. Deletion is online-only `hx-post` with no cache-eviction step, so
 * without a reconcile on write-through the deleted pin survives in
 * data:favourites and keeps rendering in the offline fallback.
 *
 * Both entry points are wired to document.body events at load time and are
 * fire-and-forget, so the tests drive them with hand-built HTMX events and
 * wait on the meta:sync stamp `_syncFromDom` writes last.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';

// db.js first: it assigns window.pwaDb, which every favourites_offline.js
// entry point reads through.
await import('../../static/js/db.js');
await import('../../static/js/favourites_offline.js');

const STORE = 'data:favourites';
const LIST_PATH = '/favourites/partials/list/';

/**
 * One roster record, trimmed to the fields renderOfflineCard reads. A null
 * region renders the "no bulletin coverage" branch, keeping these tests off
 * the rating/freshness paths covered elsewhere.
 *
 * @param {string} uuid
 * @param {string} name
 * @returns {object}
 */
function record(uuid, name) {
  return {
    uuid,
    name,
    latitude: 46.1,
    longitude: 7.2,
    elevation: 1500,
    region: null,
    rating: null,
    generated_at: '2026-08-03T08:00:00+00:00',
    unsafe_after_seconds: null,
  };
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
 * Replay one favourites-list swap carrying `records` as its roster sidecar,
 * and resolve once the write-through pass has finished.
 *
 * The meta:sync row is deleted first and waited on afterwards because
 * `_syncFromDom` writes it last — its reappearance is the signal that the
 * puts and the reconcile have both completed.
 *
 * @param {Array<object>} records
 * @returns {Promise<void>}
 */
async function replayRoster(records) {
  const root = document.createElement('div');
  const sidecar = document.createElement('script');
  sidecar.type = 'application/json';
  sidecar.id = 'favourites-roster-cache';
  sidecar.textContent = JSON.stringify(records);
  root.appendChild(sidecar);
  document.body.appendChild(root);

  await window.pwaDb.delete('meta:sync', 'favourites');
  document.body.dispatchEvent(
    new CustomEvent('htmx:afterOnLoad', { detail: { target: root } }),
  );
  await waitFor(async () => (await window.pwaDb.get('meta:sync', 'favourites')) != null);
  root.remove();
}

/**
 * Fail a favourites-list request and resolve once the offline fallback has
 * repainted `target`.
 *
 * @param {HTMLElement} target
 * @returns {Promise<void>}
 */
async function failListRequest(target) {
  document.body.dispatchEvent(
    new CustomEvent('htmx:sendError', {
      detail: { requestConfig: { path: LIST_PATH }, target },
    }),
  );
  await waitFor(() => target.querySelectorAll('[data-testid="favourite-card"]').length > 0);
}

beforeEach(async () => {
  const rows = await window.pwaDb.getAll(STORE);
  for (const row of rows) await window.pwaDb.delete(STORE, row.uuid);
  document.body.innerHTML = '';
});

describe('favourites offline cache reconcile', () => {
  it('drops a cached favourite the roster no longer lists', async () => {
    await replayRoster([record('fav-a', 'Verbier'), record('fav-b', 'Zermatt')]);
    // The user deletes "Zermatt" while online; the next list swap carries a
    // roster of one.
    await replayRoster([record('fav-a', 'Verbier')]);

    const target = document.createElement('div');
    document.body.appendChild(target);
    await failListRequest(target);

    const titles = Array.from(
      target.querySelectorAll('[data-testid="favourite-card-title"]'),
    ).map((el) => el.textContent);
    expect(titles).toEqual(['Verbier']);
  });

  it('keeps every favourite the roster still lists', async () => {
    await replayRoster([record('fav-a', 'Verbier'), record('fav-b', 'Zermatt')]);
    await replayRoster([record('fav-a', 'Verbier'), record('fav-b', 'Zermatt')]);

    const target = document.createElement('div');
    document.body.appendChild(target);
    await failListRequest(target);

    const titles = Array.from(
      target.querySelectorAll('[data-testid="favourite-card-title"]'),
    ).map((el) => el.textContent);
    expect(titles.sort()).toEqual(['Verbier', 'Zermatt']);
  });

  it('leaves the cache alone for a swap with no roster sidecar', async () => {
    await replayRoster([record('fav-a', 'Verbier'), record('fav-b', 'Zermatt')]);

    // A card swap carries only favourite-card-cache — it says nothing about
    // which favourites exist, so it must not evict the rest of the roster.
    const root = document.createElement('div');
    const sidecar = document.createElement('script');
    sidecar.type = 'application/json';
    sidecar.id = 'favourite-card-cache';
    sidecar.textContent = JSON.stringify(record('fav-a', 'Verbier'));
    root.appendChild(sidecar);
    document.body.appendChild(root);

    await window.pwaDb.delete('meta:sync', 'favourites');
    document.body.dispatchEvent(
      new CustomEvent('htmx:afterOnLoad', { detail: { target: root } }),
    );
    await waitFor(async () => (await window.pwaDb.get('meta:sync', 'favourites')) != null);

    const uuids = (await window.pwaDb.getAll(STORE)).map((row) => row.uuid);
    expect(uuids.sort()).toEqual(['fav-a', 'fav-b']);
  });
});

describe('translated card copy (SNOW-620)', () => {
  /*
   * The card is built entirely in JS — it stands in for a server partial
   * that could not be fetched — so none of its copy ever passed through a
   * template. The strings template mounted here is what
   * includes/_favourites_offline_strings.html renders on every page.
   *
   * The module reads its strings at import time and the import happened at
   * the top of this file, so a template mounted now is too late. That makes
   * these tests the FALLBACK path by construction — which is the path a
   * real page hits if the include is ever dropped, and the one that decides
   * whether the card renders words or `undefined`.
   */

  it('renders the altitude with the value substituted into the string', async () => {
    // %(metres)s is substituted by name, so a locale may put it anywhere.
    await replayRoster([record('u-alt', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    await failListRequest(target);

    const altitude = target.querySelector('[data-testid="favourite-card-altitude"]');
    expect(altitude.textContent).toBe('1500 m altitude');
  });

  it('renders the no-coverage line as words, not undefined', async () => {
    await replayRoster([record('u-cov', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    await failListRequest(target);

    const line = target.querySelector('[data-testid="favourite-card-no-coverage"]');
    expect(line.textContent).toBe('No bulletin coverage for this location.');
  });

  it('renders the cached-as-of stamp with its time substituted', async () => {
    await replayRoster([record('u-stamp', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    await failListRequest(target);

    const asOf = target.querySelector('[data-testid="favourite-card-cached-as-of"]');
    expect(asOf.textContent).toMatch(/^Showing cached data — as of \d{2}:\d{2}$/);
  });
});
