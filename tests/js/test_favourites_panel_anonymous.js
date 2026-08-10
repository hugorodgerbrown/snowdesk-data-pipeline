/*
 * tests/js/test_favourites_panel_anonymous.js — the anonymous branch of
 * SNOW-658's favourites panel (static/js/favourites.js).
 *
 * Separate file because `IS_ELIGIBLE` is captured once when the IIFE runs —
 * the same per-module-instance-state reason test_report_gate_*.js are split.
 * See tests/js/test_favourites_panel.js for the signed-in path.
 *
 * The panel replaced a sheet that opened straight onto a sign-in CTA, so the
 * CTA has to survive the move — and the overlay switch has to survive it
 * too. A control that disappears for a visitor reads as a bug rather than as
 * a permission boundary, and the sign-in path is right there beside it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const SIGNIN_URL = '/accounts/sign-in/';
const LIST_URL = '/favourites/partials/list/';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="false"
          data-signin-url="${SIGNIN_URL}"
          data-favourite-list-url="${LIST_URL}"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-list-template">
    <div>
      <div data-favourites-rows><p>Loading your favourites…</p></div>
      <button type="button" data-panel-add>Add a favourite</button>
      <input id="map-favourites-overlay-toggle" type="checkbox" role="switch">
    </div>
  </template>
  <template id="favourite-create-template"></template>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  window.pwaFavouritesOverlay = {
    show: vi.fn(),
    hide: vi.fn(),
    isVisible: vi.fn(() => false),
  };
  btn.click();
});

afterEach(() => {
  delete window.pwaFavouritesOverlay;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('anonymous tap on the favourites roundel', () => {
  it('opens the panel', () => {
    expect(sheet.hidden).toBe(false);
  });

  it('offers the sign-in CTA in place of the list', () => {
    expect(sheet.textContent).toContain('Sign in');
    expect(sheet.querySelector(`a[href="${SIGNIN_URL}"]`)).not.toBeNull();
  });

  it('never requests the list — the endpoint 403s for a visitor', () => {
    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });

  it('drops the add CTA, which has nothing to save to', () => {
    expect(sheet.querySelector('[data-panel-add]')).toBeNull();
  });

  it('keeps the overlay switch — a hidden control reads as a bug', () => {
    expect(sheet.querySelector('#map-favourites-overlay-toggle')).not.toBeNull();
  });

  it('carries a dismiss control, so the panel is not a dead end (SNOW-474)', () => {
    // The real template's own includes/_sheet_header.html renders it; this
    // fixture's stand-in body does not, so assert on what this module itself
    // must not have removed: the sheet is still closable by Escape.
    expect(sheet.hasAttribute('hidden')).toBe(false);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });
});
