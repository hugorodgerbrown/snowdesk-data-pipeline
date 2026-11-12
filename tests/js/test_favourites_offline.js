/*
 * tests/js/test_favourites_offline.js — Vitest unit tests for
 * static/js/favourites_offline.js, the card path.
 *
 * A favourite card swap (favourites:card) writes its record through into
 * data:favourites, and a failed card request repaints from it. SNOW-1481
 * retired the roster half with the map sheet's pins list (favourites:list):
 * the sheet lists Places, online only, so a failed Places request is left
 * to favourites.js's own failure line.
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
const PLACES_PATH = '/favourites/partials/places/';

/**
 * One cached record, trimmed to the fields renderOfflineCard reads. A null
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
 * Replay one favourite-card swap carrying `record` as its sidecar, and
 * resolve once the write-through pass has finished.
 *
 * The meta:sync row is deleted first and waited on afterwards because
 * `_syncFromDom` writes it last.
 *
 * @param {object} cached
 * @returns {Promise<void>}
 */
async function replayCard(cached) {
  const root = document.createElement('div');
  const sidecar = document.createElement('script');
  sidecar.type = 'application/json';
  sidecar.id = 'favourite-card-cache';
  sidecar.textContent = JSON.stringify(cached);
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
 * Fail `uuid`'s card request and resolve once the offline fallback has
 * repainted `target`.
 *
 * @param {HTMLElement} target
 * @param {string} uuid
 * @returns {Promise<void>}
 */
async function failCardRequest(target, uuid) {
  document.body.dispatchEvent(
    new CustomEvent('htmx:sendError', {
      detail: { requestConfig: { path: `/favourites/partials/${uuid}/card/` }, target },
    }),
  );
  await waitFor(() => target.querySelectorAll('[data-testid="favourite-card"]').length > 0);
}

/** A fresh element in the page to paint into. */
function newTarget() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  return target;
}

beforeEach(async () => {
  const rows = await window.pwaDb.getAll(STORE);
  for (const row of rows) await window.pwaDb.delete(STORE, row.uuid);
  document.body.innerHTML = '';
});

describe('write-through', () => {
  it('caches the record a card swap carries', async () => {
    await replayCard(record('fav-a', 'Verbier'));
    const stored = await window.pwaDb.get(STORE, 'fav-a');
    expect(stored.name).toBe('Verbier');
  });

  it("merges a card onto the record already cached, the card's fields winning", async () => {
    await window.pwaDb.put(STORE, { ...record('fav-a', 'Old name'), extra: 'kept' });
    await replayCard(record('fav-a', 'Verbier'));
    const stored = await window.pwaDb.get(STORE, 'fav-a');
    expect(stored.name).toBe('Verbier');
    expect(stored.extra).toBe('kept');
  });

  it('ignores a roster sidecar, whose list is gone (SNOW-1481)', async () => {
    const root = document.createElement('div');
    root.innerHTML =
      '<script type="application/json" id="favourites-roster-cache">' +
      JSON.stringify([record('fav-z', 'Zermatt')]) +
      '</script>';
    document.body.appendChild(root);
    document.body.dispatchEvent(
      new CustomEvent('htmx:afterOnLoad', { detail: { target: root } }),
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await window.pwaDb.get(STORE, 'fav-z')).toBeUndefined();
  });
});

describe('a failed Places request (SNOW-1481)', () => {
  it('is left alone, so Places says it could not be loaded', async () => {
    await window.pwaDb.put(STORE, record('fav-a', 'Verbier'));
    const target = newTarget();
    target.innerHTML = '<p>Your places couldn\'t be loaded — check your connection.</p>';
    const before = target.innerHTML;

    document.body.dispatchEvent(
      new CustomEvent('htmx:sendError', {
        detail: { requestConfig: { path: PLACES_PATH }, target },
      }),
    );
    // A card failure dispatched afterwards, on its own target, gives the
    // Places one more than enough time to have done any damage.
    await failCardRequest(newTarget(), 'fav-a');

    expect(target.innerHTML).toBe(before);
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
    await replayCard(record('u-alt', 'Verbier'));
    const target = newTarget();

    await failCardRequest(target, 'u-alt');

    const altitude = target.querySelector('[data-testid="favourite-card-altitude"]');
    expect(altitude.textContent).toBe('1500 m altitude');
  });

  it('renders a retired resort as no longer listed (SNOW-1404)', async () => {
    await replayCard({ ...record('u-ret', 'Gone Hill'), retired: true });
    const target = newTarget();

    await failCardRequest(target, 'u-ret');

    const line = target.querySelector('[data-testid="favourite-card-retired"]');
    expect(line.textContent).toBe('No longer listed');
    expect(target.querySelector('[data-testid="favourite-card-altitude"]')).toBeNull();
  });

  it('renders the no-coverage line as words, not undefined', async () => {
    await replayCard(record('u-cov', 'Verbier'));
    const target = newTarget();

    await failCardRequest(target, 'u-cov');

    const line = target.querySelector('[data-testid="favourite-card-no-coverage"]');
    expect(line.textContent).toBe('No bulletin coverage for this location.');
  });

  it('renders the cached-as-of stamp with its time substituted', async () => {
    // SNOW-1139: a card cached today carries the time alone.
    await replayCard({ ...record('u-stamp', 'Verbier'), generated_at: new Date().toISOString() });
    const target = newTarget();

    await failCardRequest(target, 'u-stamp');

    const asOf = target.querySelector('[data-testid="favourite-card-cached-as-of"]');
    expect(asOf.textContent).toMatch(/^Showing cached data — as of \d{2}:\d{2}$/);
  });

  it("dates yesterday's cache, so it cannot read as today's (SNOW-1139)", async () => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    yesterday.setHours(9, 0, 0, 0);
    const iso = yesterday.toISOString();
    await replayCard({ ...record('u-old', 'Verbier'), generated_at: iso });
    const target = newTarget();

    await failCardRequest(target, 'u-old');

    const asOf = target.querySelector('[data-testid="favourite-card-cached-as-of"]');
    const day = window.pwaCachedStampCore.formatDay(iso);
    expect(day).not.toBe('');
    expect(asOf.textContent).toBe(`Showing cached data — as of ${day} 09:00`);
  });
});

describe('the stand-in card ranks its title like the server card', () => {
  /*
   * This card stands in for favourites/partials/_favourite_card.html in
   * the same slot, so it takes the same heading level the server would
   * have sent — apps.favourites.views.favourite_card sends h2.
   */

  it('is an h2 for a failed card request', async () => {
    await replayCard(record('fav-a', 'Verbier'));
    const target = newTarget();

    await failCardRequest(target, 'fav-a');

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.tagName).toBe('H2');
  });
});

describe('the offline fallback only claims favourites requests (SNOW-722)', () => {
  /*
   * The handlers are bound to document.body, so they are offered every
   * failed HTMX request on the page — and the suffix match is a bare
   * substring. routes.js paints its own panel — from its own cache
   * (routes_offline.js, SNOW-950) or its own failure line — and neither
   * may be overwritten from here.
   */

  const ROUTES_PATH = '/routes/partials/list/';

  it('leaves a failed routes-list request untouched', async () => {
    await replayCard(record('fav-a', 'Verbier'));
    const rows = newTarget();
    rows.setAttribute('data-routes-rows', '');
    rows.innerHTML = '<p data-testid="routes-offline">Routes need a connection</p>';
    const before = rows.innerHTML;

    document.body.dispatchEvent(
      new CustomEvent('htmx:sendError', {
        detail: { requestConfig: { path: ROUTES_PATH }, target: rows },
      }),
    );
    await failCardRequest(newTarget(), 'fav-a');

    expect(rows.innerHTML).toBe(before);
  });

  it('still repaints a failed favourites card request', async () => {
    await replayCard(record('fav-a', 'Verbier'));
    const target = newTarget();

    await failCardRequest(target, 'fav-a');

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.textContent).toBe('Verbier');
  });
});
