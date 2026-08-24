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


describe('the stand-in card ranks its title like the server card', () => {
  /*
   * This card stands in for favourites/partials/_favourite_card.html in
   * the same slot, so it takes the same heading level the server would
   * have sent — otherwise a pin's outline depth would depend on whether
   * the request reached the server. The server picks by caller
   * (``heading_tag``); this module picks by where it is painting.
   */

  const CARD_PATH = '/favourites/partials/fav-a/card/';

  it('is an h3 in the account hub list, under the "My favourites" h2', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    await failListRequest(target);

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.tagName).toBe('H3');
  });

  it('is an h2 in the map sheet, whose panel title is not a heading', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    // The map swaps the same endpoint's ?variant=map response into this
    // container (static/js/favourites.js), inside a panel headed by a
    // <span> — so a card in it has no section heading to rank under.
    const target = document.createElement('div');
    target.setAttribute('data-favourites-rows', '');
    document.body.appendChild(target);

    await failListRequest(target);

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.tagName).toBe('H2');
  });

  it('is an h3 for a failed single-card request — only the hub asks for one', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    document.body.dispatchEvent(
      new CustomEvent('htmx:sendError', {
        detail: { requestConfig: { path: CARD_PATH }, target },
      }),
    );
    await waitFor(
      () => target.querySelectorAll('[data-testid="favourite-card"]').length > 0,
    );

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.tagName).toBe('H3');
  });
});

describe('the offline fallback only claims favourites requests (SNOW-722)', () => {
  /*
   * The handlers are bound to document.body, so they are offered every
   * failed HTMX request on the page — and the suffix match is a bare
   * substring. ``/routes/partials/list/`` contains ``/partials/list/``, so
   * offline the routes panel was wiped and repainted with favourite cards
   * under its own "TRACKS" heading. routes.js deliberately has no offline
   * cache and renders its own "can't load offline" line instead.
   */

  const ROUTES_PATH = '/routes/partials/list/';

  /** The routes panel's rows container, with the markup routes.js leaves. */
  function routesRows() {
    const rows = document.createElement('div');
    rows.setAttribute('data-routes-rows', '');
    rows.innerHTML = '<p data-testid="routes-offline">Routes need a connection</p>';
    document.body.appendChild(rows);
    return rows;
  }

  it('leaves a failed routes-list request untouched', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    const rows = routesRows();
    const before = rows.innerHTML;

    document.body.dispatchEvent(
      new CustomEvent('htmx:sendError', {
        detail: { requestConfig: { path: ROUTES_PATH }, target: rows },
      }),
    );

    // A favourites failure dispatched afterwards, on its own target, gives
    // the routes one more than enough time to have done its damage: both
    // take the same async path through the same IndexedDB store.
    const favouritesTarget = document.createElement('div');
    document.body.appendChild(favouritesTarget);
    await failListRequest(favouritesTarget);

    expect(rows.innerHTML).toBe(before);
    expect(rows.querySelectorAll('[data-testid="favourite-card"]').length).toBe(0);
  });

  it('still repaints a failed favourites-list request', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    await failListRequest(target);

    const titles = Array.from(
      target.querySelectorAll('[data-testid="favourite-card-title"]'),
    ).map((el) => el.textContent);
    expect(titles).toEqual(['Verbier']);
  });

  it('still repaints a failed favourites card request', async () => {
    await replayRoster([record('fav-a', 'Verbier')]);
    const target = document.createElement('div');
    document.body.appendChild(target);

    document.body.dispatchEvent(
      new CustomEvent('htmx:sendError', {
        detail: {
          requestConfig: { path: '/favourites/partials/fav-a/card/' },
          target,
        },
      }),
    );
    await waitFor(
      () => target.querySelectorAll('[data-testid="favourite-card"]').length > 0,
    );

    const title = target.querySelector('[data-testid="favourite-card-title"]');
    expect(title.textContent).toBe('Verbier');
  });
});
