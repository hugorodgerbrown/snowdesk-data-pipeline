/*
 * tests/js/test_favourites_panel_share.js — Vitest DOM tests for sharing a
 * saved pin from the map panel (SNOW-887).
 *
 * Its own file rather than a describe block in test_favourites_panel.js for
 * the reason that file's siblings already exist: favourites.js captures its
 * config (STRINGS, IS_ELIGIBLE, the URL templates) ONCE when the IIFE runs,
 * so a fixture that differs from another file's cannot share a module
 * instance with it.
 *
 * The share itself is window.pwaShare's and is covered by
 * tests/js/test_share.js. What is worth asserting HERE is the wiring this
 * module owns:
 *
 *   - the payload is what the SERVER put on the button, passed through
 *     untouched and as 'text' — a "<name> - <link>" string is not a bare
 *     URL, and navigator.canShare rejects one that claims to be;
 *   - only two of the four outcomes speak. A share the platform TOOK says
 *     nothing (it showed its own sheet); a share the user DISMISSED says
 *     nothing either, and a "Copied." toast after a dismissal is the exact
 *     bug static/js/share.js's header documents;
 *   - telemetry fires;
 *   - there is NO mint step — no fetch, unlike the route share, because a
 *     three word address is already a permanent public URL.
 *
 * window.pwaShare is frozen and cannot be spied on, so these stub the
 * PLATFORM APIs underneath the real helper, as tests/js/test_routes_panel.js
 * does.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';
import '../../static/js/share.js';

const LIST_URL = '/favourites/partials/list/?variant=map';
const UUID = '11111111-2222-3333-4444-555555555555';
const SHARE_TEXT = 'Mont Fort - https://w3w.co/filled.count.soap';

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
    </div>
  </template>
  <template id="favourite-create-template">
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
    </form>
  </template>
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class"
       class="hidden">
    <span data-toast-body></span>
  </div>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');
const realNavigator = window.navigator;

/**
 * Replace window.navigator for one test.
 *
 * favourites.js reaches the platform through window.pwaShare, which reads
 * `window.navigator` at call time — so this is what the module under test
 * actually sees.
 *
 * @param {object} parts Navigator members to expose.
 */
function stubNavigator(parts) {
  window.navigator = Object.assign({ onLine: true }, parts);
}

/**
 * Open the panel and put one shareable row inside it.
 *
 * Stands in for what favourites:list swaps in. The row's real shape is
 * favourites/partials/_favourite_row_menu_items.html's, asserted
 * server-side in tests/favourites/test_views.py — including that the text
 * is the name and the short link.
 *
 * @param {string} [text] The share payload the server would have written.
 * @returns {HTMLElement} The Share control.
 */
function openPanelWithShareRow(text = SHARE_TEXT) {
  btn.click();
  const rows = sheet.querySelector('[data-favourites-rows]');
  rows.innerHTML = `
    <ul><li id="favourite-${UUID}">
      <button type="button"
              data-favourite-share="${UUID}"
              data-favourite-share-text="${text}">Share</button>
    </li></ul>
  `;
  return rows.querySelector('[data-favourite-share]');
}

/** @returns {string} Whatever the toast is currently showing. */
function toastText() {
  return document.getElementById('map-sheet-toast').textContent.trim();
}

describe('sharing a saved pin (SNOW-887)', () => {
  beforeEach(() => {
    window.pwaTelemetry = { emit: vi.fn() };
    globalThis.fetch = vi.fn();
    document.getElementById('map-sheet-toast').classList.add('hidden');
    document.querySelector('[data-toast-body]').textContent = '';
  });

  afterEach(() => {
    window.navigator = realNavigator;
    vi.restoreAllMocks();
    delete window.pwaTelemetry;
    sheet.hidden = true;
    sheet.replaceChildren();
  });

  it('hands the server-composed text to the platform as text', async () => {
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() => expect(share).toHaveBeenCalled());
    const payload = share.mock.calls[0][0];
    // `text`, not `url`: the payload contains a URL rather than being one.
    expect(payload.text).toBe(SHARE_TEXT);
    expect(payload.url).toBeUndefined();
  });

  it('says nothing when the platform took it', async () => {
    stubNavigator({ share: vi.fn(() => Promise.resolve()) });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() =>
      expect(window.pwaTelemetry.emit).toHaveBeenCalled(),
    );
    // The platform showed its own sheet; a toast would be a second
    // confirmation of something the user watched happen.
    expect(toastText()).toBe('');
  });

  it('says nothing when the user dismissed the sheet', async () => {
    const abort = Object.assign(new Error('dismissed'), { name: 'AbortError' });
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({
      share: vi.fn(() => Promise.reject(abort)),
      clipboard: { writeText },
    });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() =>
      expect(window.pwaTelemetry.emit).toHaveBeenCalled(),
    );
    // THE BUG share.js DOCUMENTS: they declined, so nothing is copied and
    // nothing is said. A "Copied." here would tell them the thing they
    // just refused happened anyway.
    expect(writeText).not.toHaveBeenCalled();
    expect(toastText()).toBe('');
  });

  it('toasts when it fell back to the clipboard', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({ clipboard: { writeText } });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() => expect(toastText()).toBe('Copied.'));
    expect(writeText).toHaveBeenCalledWith(SHARE_TEXT);
  });

  it('toasts when neither the sheet nor the clipboard worked', async () => {
    stubNavigator({});
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() =>
      expect(toastText()).toBe("That couldn't be shared. Try again."),
    );
  });

  it('emits its own telemetry event', async () => {
    stubNavigator({ share: vi.fn(() => Promise.resolve()) });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() =>
      expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
        'map.favourite.shared',
        {},
      ),
    );
  });

  it('mints nothing — there is no endpoint behind this share', async () => {
    stubNavigator({ share: vi.fn(() => Promise.resolve()) });
    const control = openPanelWithShareRow();

    control.click();

    await vi.waitFor(() =>
      expect(window.pwaTelemetry.emit).toHaveBeenCalled(),
    );
    // Unlike the route share: a three word address is already a permanent
    // public URL, so there is no token to POST for.
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
