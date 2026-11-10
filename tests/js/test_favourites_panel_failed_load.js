/*
 * tests/js/test_favourites_panel_failed_load.js — what the map sheet ends up
 * showing when its Places request fails (SNOW-658; Places only since
 * SNOW-1481).
 *
 * favourites.js writes "Your places couldn't be loaded…" into the rows when
 * the request fails. Until SNOW-1481 a second module, favourites_offline.js,
 * repainted the cached favourite cards over that line for the pins list
 * (favourites:list). That list is gone: Places is online only, as Today is,
 * so offline it says it couldn't be loaded rather than showing cached pins
 * — whatever data:favourites still holds.
 *
 * Both modules are driven through their REAL listeners with real bubbling
 * htmx events (htmx dispatches with bubbles: true), never by calling either
 * module's internals: the offline module still listens on document.body for
 * every failed favourites request, and the thing under test is that it
 * leaves this one alone.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const PLACES_URL = '/favourites/partials/places/';
const STORE = 'data:favourites';
const FAILURE_LINE = "couldn't be loaded";

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="/favourites/partials/create/"
          data-favourite-places-url="${PLACES_URL}"
          data-favourite-rename-url-template="/favourites/partials/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/partials/__UUID__/delete/"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-list-template">
    <div>
      <div data-favourites-rows><p>Loading your favourites…</p></div>
      <button type="button" data-panel-add>Add a favourite</button>
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
// through.
await import('../../static/js/db.js');
await import('../../static/js/favourites_offline.js');
await import('../../static/js/favourites.js');
await import('../../static/js/saved_panel.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

/** The rows container inside the currently-rendered panel body. */
function rows() {
  return sheet.querySelector('[data-favourites-rows]');
}

/** One cached record, trimmed to the fields the offline card reads. */
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

/** Let both modules' handlers, the offline one's awaits included, settle. */
async function settle() {
  for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Fail the sheet's Places request, as htmx would: dispatched from the
 * request's own element, bubbling, so BOTH listeners see it.
 *
 * @param {string} eventName
 * @returns {void}
 */
function failPlacesRequest(eventName) {
  rows().dispatchEvent(
    new CustomEvent(eventName, {
      bubbles: true,
      detail: { requestConfig: { path: PLACES_URL }, target: rows() },
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

describe('a failed Places load', () => {
  it.each(['htmx:responseError', 'htmx:sendError'])(
    'says so on %s, even with favourites cached on the device',
    async (eventName) => {
      await window.pwaDb.put(STORE, record('fav-a', 'Verbier'));
      btn.click();

      failPlacesRequest(eventName);
      await settle();

      expect(rows().textContent).toContain(FAILURE_LINE);
      expect(rows().querySelectorAll('[data-testid="favourite-card"]')).toHaveLength(0);
      expect(rows().textContent).not.toContain('Loading your favourites');
    },
  );

  it.each(['htmx:responseError', 'htmx:sendError'])(
    'says so on %s with nothing cached',
    async (eventName) => {
      btn.click();

      failPlacesRequest(eventName);
      await settle();

      expect(rows().textContent).toContain(FAILURE_LINE);
    },
  );
});
