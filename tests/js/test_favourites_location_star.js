/*
 * tests/js/test_favourites_location_star.js — the "Your location" sheet's
 * star saves the fix through favourites.js's one coordinate create
 * (SNOW-1395).
 *
 * map_place_sheet.js turns the star (aria-pressed) when the queue can take
 * the save; favourites.js's document listener then queues the same create
 * the panel's form posts, with an empty name, and dispatches the optimistic
 * marker. The queue is stubbed — tests/js/test_mutation_queue.js covers it.
 * The module reads `#favourite-add-btn` once at load, so the fixture is
 * built before the import (as tests/js/test_favourites.js documents).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const CREATE_URL = '/favourites/create/';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="${CREATE_URL}"
          data-favourite-places-url="/favourites/partials/places/"
          data-favourite-rename-url-template="/favourites/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/__UUID__/delete/"></button>
  <div id="map"></div>
  <div id="favourite-sheet" hidden></div>
  <div id="place-sheet"></div>
  <template id="favourite-create-template"></template>
  <template id="favourite-confirmation-template"><div></div></template>
`;

await import('../../static/js/favourites.js');

const host = /** @type {HTMLElement} */ (document.getElementById('place-sheet'));

/**
 * Tap a location star, turned or not by the place sheet first.
 *
 * @param {{pressed?: boolean, lat?: string}} [opts]
 * @returns {void}
 */
function tapStar({ pressed = true, lat = '46.090500' } = {}) {
  host.innerHTML = `<button type="button" data-location-star data-lat="${lat}" data-lon="7.273700"
    aria-pressed="${pressed ? 'true' : 'false'}"></button>`;
  /** @type {HTMLButtonElement} */ (host.querySelector('[data-location-star]')).click();
}

beforeEach(() => {
  window.pwaTelemetry = { emit: vi.fn() };
  window.pwaMutationQueue = { enqueue: vi.fn(() => Promise.resolve()) };
  window.pwaRowFocus = { reveal: vi.fn() };
});

afterEach(() => {
  delete window.pwaTelemetry;
  delete window.pwaMutationQueue;
  delete window.pwaRowFocus;
  host.replaceChildren();
});

describe('the location star', () => {
  it('queues a dropped pin at the fix through the create endpoint', () => {
    const pending = vi.fn();
    document.addEventListener('snowdesk:favourite-pending', pending, { once: true });
    tapStar();
    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    const call = window.pwaMutationQueue.enqueue.mock.calls[0][0];
    expect(call.url).toBe(CREATE_URL);
    expect(call.headers['HX-Request']).toBe('true');
    const body = new URLSearchParams(call.body);
    expect(body.get('lat')).toBe('46.0905');
    expect(body.get('lon')).toBe('7.2737');
    expect(body.get('name')).toBe('');
    expect(pending.mock.calls[0][0].detail).toMatchObject({ lat: 46.0905, lon: 7.2737 });
  });

  it('records the save and shows the favourites layer', () => {
    tapStar();
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.favourite.created', { offline: false });
    expect(window.pwaRowFocus.reveal).toHaveBeenCalledTimes(1);
  });

  it('saves nothing when the place sheet refused to turn the star', () => {
    tapStar({ pressed: false });
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('saves nothing for a star with no usable coordinate', () => {
    tapStar({ lat: 'north' });
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });
});
