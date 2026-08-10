/*
 * tests/js/test_favourites_panel_failed_load.js — what the map sheet ends up
 * showing when the favourites list request fails (SNOW-658).
 *
 * Two modules listen for the same htmx failure on the same element, and both
 * write to it:
 *
 *   favourites.js         writes "Your favourites couldn't be loaded…".
 *   favourites_offline.js repaints the cached favourite cards from
 *                         data:favourites (SNOW-418).
 *
 * The intended outcome is cached data when there is any, and the failure line
 * only when there is none — a user with pins cached should see them, and a
 * user with nothing cached must not be told "you have no favourites" by a
 * request that merely failed.
 *
 * That outcome is currently produced by two facts neither module states, so
 * it is pinned here rather than left to hold by accident (see the comment at
 * the failure handler in static/js/favourites.js):
 *
 *   1. favourites_offline.js binds on document.body, favourites.js on
 *      document — so the cache handler runs first as the event bubbles;
 *   2. it then awaits IndexedDB, so its write lands a microtask later, after
 *      favourites.js has synchronously written the failure line.
 *
 * Both modules are driven through their REAL listeners with real bubbling
 * htmx events (htmx dispatches with bubbles: true), never by calling either
 * module's internals — the ordering is the thing under test, and calling in
 * directly would bypass it.
 *
 * Assertions wait for the settled DOM: the winning write is asynchronous, so
 * reading immediately after dispatch would only ever see the failure line.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

// As apps.public.views._favourites_context builds it. The query string is
// deliberate: favourites_offline.js matches the list request by path
// substring, and ?variant=map must not stop it matching.
const LIST_URL = '/favourites/partials/list/?variant=map';
const STORE = 'data:favourites';
const FAILURE_LINE = "couldn't be loaded";

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="/favourites/partials/create/"
          data-favourite-list-url="${LIST_URL}"
          data-favourite-rename-url-template="/favourites/partials/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/partials/__UUID__/delete/"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-list-template">
    <div>
      <div data-favourites-rows><p>Loading your favourites…</p></div>
      <button type="button" data-panel-add>Add a favourite</button>
      <input id="map-favourites-overlay-toggle" type="checkbox" role="switch">
    </div>
  </template>
  <template id="favourite-create-template">
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
    </form>
  </template>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

// db.js first: it assigns window.pwaDb, which favourites_offline.js reads
// through. Load order between the other two is irrelevant — the ordering that
// decides this behaviour is listener DEPTH (body vs document), not
// registration order.
await import('../../static/js/db.js');
await import('../../static/js/favourites_offline.js');
await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

/** The rows container inside the currently-rendered panel body. */
function rows() {
  return sheet.querySelector('[data-favourites-rows]');
}

/** One roster record, trimmed to the fields renderOfflineCard reads. */
function record(uuid, name) {
  return {
    uuid,
    name,
    latitude: 46.1,
    longitude: 7.2,
    elevation: 1500,
    region: null,
    rating: null,
    generated_at: '2026-08-10T08:00:00+00:00',
    unsafe_after_seconds: null,
  };
}

/** Poll `predicate` until truthy, or fail after `timeoutMs`. */
async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('condition never became true');
}

/**
 * Seed data:favourites by replaying a successful list swap carrying `records`
 * as its roster sidecar — the same write-through path a real page uses.
 *
 * @param {Array<object>} records
 * @returns {Promise<void>}
 */
async function seedCache(records) {
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
  // _syncFromDom stamps meta:sync last, so its reappearance means every put
  // has landed.
  await waitFor(async () => (await window.pwaDb.get('meta:sync', 'favourites')) != null);
  root.remove();
}

/**
 * Fail the sheet's list request, as htmx would: dispatched from the request's
 * own element, bubbling, so BOTH listeners see it.
 *
 * @param {string} eventName
 * @returns {void}
 */
function failListRequest(eventName) {
  rows().dispatchEvent(
    new CustomEvent(eventName, {
      bubbles: true,
      detail: { requestConfig: { path: LIST_URL }, target: rows() },
    }),
  );
}

beforeEach(async () => {
  for (const row of await window.pwaDb.getAll(STORE)) {
    await window.pwaDb.delete(STORE, row.uuid);
  }
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('a failed list load with favourites cached', () => {
  it.each(['htmx:responseError', 'htmx:sendError'])(
    'settles on the cached cards, not the failure line, on %s',
    async (eventName) => {
      await seedCache([record('fav-a', 'Verbier'), record('fav-b', 'Zermatt')]);
      btn.click();

      failListRequest(eventName);

      await waitFor(
        () => rows().querySelectorAll('[data-testid="favourite-card"]').length === 2,
      );
      const titles = Array.from(
        rows().querySelectorAll('[data-testid="favourite-card-title"]'),
      ).map((el) => el.textContent);
      expect(titles.sort()).toEqual(['Verbier', 'Zermatt']);
      // The failure line was written first and has been replaced: cached data
      // wins deliberately, because it is the more useful answer.
      expect(rows().textContent).not.toContain(FAILURE_LINE);
    },
  );
});

describe('a failed list load with nothing cached', () => {
  it.each(['htmx:responseError', 'htmx:sendError'])(
    'leaves the failure line standing on %s',
    async (eventName) => {
      btn.click();

      failListRequest(eventName);
      // The cache handler runs to its early return through two awaits
      // (dbReady, then getAll), so the DOM is only settled a macrotask later
      // — assert too early and this test would pass whatever it did next.
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(rows().textContent).toContain(FAILURE_LINE);
      // Never the server partial's "You have no saved favourites yet." — an
      // empty cache says nothing about how many pins the user has.
      expect(rows().querySelectorAll('[data-testid="favourite-card"]')).toHaveLength(0);
      expect(rows().textContent).not.toContain('Loading your favourites');
    },
  );
});
