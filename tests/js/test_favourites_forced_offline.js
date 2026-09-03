/*
 * tests/js/test_favourites_forced_offline.js — saving a pin under a
 * user-forced offline mode is a queued save, and the confirmation must say so
 * (SNOW-748).
 *
 * `favourites.js` reveals the confirmation card's "will sync when you're back
 * online" line, and stamps its `map.favourite.created` telemetry `offline`
 * property, from whether the app was offline at the moment of the tap. Both
 * read `navigator.onLine`, which stays TRUE under a mode the user forced from
 * the account menu's offline-mode toggle — so a save that was in fact queued
 * told the
 * user it had already been made, and the telemetry recorded it as an online
 * save. The mutation itself was always queued; only the account of it was
 * wrong.
 *
 * The create submit is exercised through a real `submit` event on a form
 * matching what `_favourites_surface.html` renders, since the handler is
 * delegated from `document` and reads the form's own fields. The queue and
 * `MapSheet.canQueueMutations` are stubbed — what is under test is which
 * question this module asks about connectivity, not the queue's own
 * behaviour, which tests/js/test_mutation_queue.js covers.
 *
 * The module captures `#favourite-add-btn`, `#favourite-sheet` and
 * `#favourite-create-template` once at load, so the fixture is built before
 * the dynamic import and no test replaces it — the same constraint
 * tests/js/test_favourites.js documents.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const CREATE_URL = '/favourites/create/';
const RESORT_CREATE_URL = '/favourites/create/resort/';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="${CREATE_URL}"
          data-favourite-list-url="/favourites/partials/list/?variant=map"
          data-favourite-rename-url-template="/favourites/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/__UUID__/delete/"></button>
  <div id="map" data-resort-create-url="${RESORT_CREATE_URL}"></div>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-create-template"></template>
  <template id="favourite-confirmation-template">
    <div>
      <p>Pin saved!</p>
      <p data-favourite-pending hidden>Saved — will sync when you're back online.</p>
    </div>
  </template>
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class" class="hidden">
    <span data-toast-body></span>
  </div>
`;

await import('../../static/js/favourites.js');

const sheet = document.getElementById('favourite-sheet');

/**
 * Submit the create form the surface partial renders, exactly as the Save
 * button does.
 *
 * @returns {void}
 */
function saveFavourite() {
  sheet.innerHTML = `
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      <input type="hidden" name="lat" value="46.1">
      <input type="hidden" name="lon" value="7.2">
      <input type="text" name="name" value="Col des Gentianes">
    </form>`;
  sheet.querySelector('#favourite-create-form').dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true }),
  );
}

/**
 * Tap the star in a resort pin's popup, which queues a create of its own —
 * the second of the two call sites that stamp the telemetry property.
 *
 * @returns {void}
 */
function starResort() {
  const star = document.createElement('button');
  star.setAttribute('data-resort-star', '');
  star.dataset.resortSlug = 'verbier';
  star.dataset.resortLat = '46.1';
  star.dataset.resortLon = '7.2';
  star.dataset.resortName = 'Verbier';
  star.dataset.favourited = 'false';
  sheet.appendChild(star);
  star.click();
}

/** True while the confirmation card's "will sync" line is on screen. */
function pendingLineVisible() {
  const line = sheet.querySelector('[data-favourite-pending]');
  return !!line && !line.hasAttribute('hidden');
}

/** The `offline` property of the single map.favourite.created event emitted. */
function createdOfflineProperty() {
  const call = window.pwaTelemetry.emit.mock.calls.find(
    (args) => args[0] === 'map.favourite.created',
  );
  return call ? call[1].offline : undefined;
}

beforeEach(() => {
  window.pwaTelemetry = { emit: vi.fn() };
  window.pwaMutationQueue = { enqueue: vi.fn(() => Promise.resolve()) };
  window.pwaDb = { isResetRequired: () => false };
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
  delete window.pwaMutationQueue;
  delete window.pwaDb;
  delete window.pwaConnectivity;
  sheet.replaceChildren();
});

describe('saving a pin under a forced offline mode (SNOW-748)', () => {
  it('queues the save and says it will sync, with the interface up', () => {
    window.pwaConnectivity = { isOnline: () => false };
    // The distinguishing fact: nothing about the interface says offline, so
    // the pre-SNOW-748 read of `navigator.onLine` claimed the save had gone.
    expect(navigator.onLine).toBe(true);

    saveFavourite();

    // Nothing about the queueing itself changes — the save is captured
    // exactly as it was, which is the whole point of the queue.
    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(window.pwaMutationQueue.enqueue.mock.calls[0][0].url).toBe(CREATE_URL);
    expect(pendingLineVisible()).toBe(true);
    expect(createdOfflineProperty()).toBe(true);
  });

  it('reports a straight save under the auto mode', () => {
    window.pwaConnectivity = { isOnline: () => true };

    saveFavourite();

    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(pendingLineVisible()).toBe(false);
    expect(createdOfflineProperty()).toBe(false);
  });

  it('falls back to the interface on a page where pwa_offline.js has not run', () => {
    // No `window.pwaConnectivity` at all — the historical behaviour, which
    // the swap must preserve rather than treat as "offline".
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);

    saveFavourite();

    expect(pendingLineVisible()).toBe(true);
    expect(createdOfflineProperty()).toBe(true);
  });
});

describe('starring a resort under a forced offline mode (SNOW-748)', () => {
  it('records the queued save as offline, with the interface up', () => {
    window.pwaConnectivity = { isOnline: () => false };
    expect(navigator.onLine).toBe(true);

    starResort();

    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(window.pwaMutationQueue.enqueue.mock.calls[0][0].url).toBe(RESORT_CREATE_URL);
    expect(createdOfflineProperty()).toBe(true);
  });

  it('records a straight save under the auto mode', () => {
    window.pwaConnectivity = { isOnline: () => true };

    starResort();

    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(createdOfflineProperty()).toBe(false);
  });
});
