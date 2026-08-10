/*
 * tests/js/test_favourites_panel.js — Vitest DOM tests for SNOW-658's
 * favourites panel (static/js/favourites.js).
 *
 * The roundel used to jump straight into pin placement, and the "Favourites"
 * map layer was toggled from a row in the layers menu. This ticket collapses
 * the two: the roundel opens a PANEL listing the user's own pins, adding one
 * is an action inside it, and the panel owns the overlay switch.
 *
 * What is worth asserting, and why:
 *
 *   - the rows come from favourites:list over HTMX, so each arrives with its
 *     own rename/delete wiring rather than a JS-built copy of it;
 *   - the request goes to the URL the surface handed the module VERBATIM,
 *     query string and all. The server appends ``?variant=map`` so the sheet
 *     gets the lean row template (no in-page card panel, no "view on the map"
 *     link); a module that rebuilt the path from the URL name would silently
 *     drop it and the sheet would render the manage page's markup. What that
 *     template contains is asserted server-side, in
 *     tests/favourites/test_views.py::TestFavouriteList;
 *   - the add CTA and the switch are DELEGATED on the sheet, because the body
 *     is re-cloned on every open — a per-element listener would be bound to
 *     an element the next open throws away;
 *   - the switch reflects window.pwaFavouritesOverlay.isEnabled() on open,
 *     rather than a flag of this module's own that could drift from it;
 *   - a failed list load says so. This panel opens offline and its list does
 *     not load offline, and falling through to the server partial's own
 *     "You have no saved favourites yet." would be a wrong statement about
 *     the user's own data.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the anonymous branch
 * needs its own file (test_favourites_panel_anonymous.js) — the same
 * per-module-instance-state reason test_report_gate_*.js are split.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

// As apps.public.views._favourites_context builds it — the map sheet asks for
// the lean row variant (SNOW-658).
const LIST_URL = '/favourites/partials/list/?variant=map';

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
      <label for="map-favourites-overlay-toggle">Display on the map</label>
      <input id="map-favourites-overlay-toggle" type="checkbox" role="switch">
    </div>
  </template>
  <template id="favourite-create-template">
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      <input type="hidden" name="lat">
      <input type="hidden" name="lon">
    </form>
  </template>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

/** The switch inside the currently-rendered panel body. */
function overlaySwitch() {
  return sheet.querySelector('#map-favourites-overlay-toggle');
}

/**
 * Close the panel and open it again.
 *
 * SNOW-658: the roundel toggles, so a re-open is two taps — the first
 * closes what is on screen. It used to be one, because a second tap simply
 * re-opened an already-open sheet.
 *
 * @returns {void}
 */
function reopen() {
  btn.click();
  btn.click();
}

/** The rows container inside the currently-rendered panel body. */
function rows() {
  return sheet.querySelector('[data-favourites-rows]');
}

let overlay;

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  overlay = {
    show: vi.fn(),
    hide: vi.fn(),
    isEnabled: vi.fn(() => true),
  };
  window.pwaFavouritesOverlay = overlay;
  window.PlacePicker = { activate: vi.fn(), deactivate: vi.fn() };
  window.snowdeskMapState = { map: { getCenter: () => ({ lat: 46.1, lng: 7.2 }) } };
});

afterEach(() => {
  delete window.pwaFavouritesOverlay;
  delete window.PlacePicker;
  delete window.snowdeskMapState;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('tapping the roundel opens the panel, not the create form', () => {
  it('opens the sheet on the list body', () => {
    btn.click();

    expect(sheet.hidden).toBe(false);
    expect(rows()).not.toBeNull();
    expect(sheet.querySelector('#favourite-create-form')).toBeNull();
  });

  it('loads the rows from favourites:list, into the rows container', () => {
    btn.click();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    const [method, url, opts] = globalThis.htmx.ajax.mock.calls[0];
    expect(method).toBe('GET');
    // Verbatim, query string included — the ?variant=map the server put on
    // it is what makes the sheet get the lean row template.
    expect(url).toBe(LIST_URL);
    expect(url).toContain('variant=map');
    expect(opts.target).toBe(rows());
    expect(opts.swap).toBe('innerHTML');
  });

  it('does not arm the place-picker — nothing is being placed yet', () => {
    btn.click();
    expect(window.PlacePicker.activate).not.toHaveBeenCalled();
  });

  it('re-clones the body on every open, so a stale row cannot survive', () => {
    btn.click();
    rows().innerHTML = '<div id="stale-row"></div>';

    reopen();

    expect(document.getElementById('stale-row')).toBeNull();
    expect(rows()).not.toBeNull();
  });
});

describe('the add CTA', () => {
  it('shows the create form and arms the place-picker at the map centre', () => {
    btn.click();
    sheet.querySelector('[data-panel-add]').click();

    const form = sheet.querySelector('#favourite-create-form');
    expect(form).not.toBeNull();
    expect(form.querySelector('[name="lat"]').value).toBe('46.1');
    expect(form.querySelector('[name="lon"]').value).toBe('7.2');
    expect(window.PlacePicker.activate).toHaveBeenCalledTimes(1);
    expect(window.PlacePicker.activate.mock.calls[0][0].occludedBy).toBe(sheet);
  });

  it('still works after a re-open, because the listener is on the sheet', () => {
    btn.click();
    reopen();

    sheet.querySelector('[data-panel-add]').click();

    expect(sheet.querySelector('#favourite-create-form')).not.toBeNull();
  });
});

describe('the overlay switch', () => {
  it('opens reflecting the overlay itself, not a flag of its own', () => {
    overlay.isEnabled.mockReturnValue(false);
    btn.click();
    expect(overlaySwitch().checked).toBe(false);

    overlay.isEnabled.mockReturnValue(true);
    reopen();
    expect(overlaySwitch().checked).toBe(true);
  });

  it('drives show()/hide() on the bridge', () => {
    btn.click();
    const toggle = overlaySwitch();

    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(overlay.show).toHaveBeenCalledTimes(1);

    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(overlay.hide).toHaveBeenCalledTimes(1);
  });

  it('still works after a re-open, because the listener is on the sheet', () => {
    btn.click();
    reopen();

    const toggle = overlaySwitch();
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));

    expect(overlay.hide).toHaveBeenCalledTimes(1);
  });
});

describe('a list load that fails', () => {
  it.each(['htmx:responseError', 'htmx:sendError'])(
    'replaces the loading line with a failure line on %s',
    (eventName) => {
      btn.click();
      const container = rows();

      document.dispatchEvent(
        new CustomEvent(eventName, { detail: { target: container } }),
      );

      // Never the server partial's "You have no saved favourites yet." — the
      // request failed, which says nothing about how many pins exist.
      expect(container.textContent).toContain("couldn't be loaded");
      expect(container.textContent).not.toContain('Loading your favourites');
    },
  );

  it('ignores a failure aimed at some other target', () => {
    btn.click();
    const container = rows();

    document.dispatchEvent(
      new CustomEvent('htmx:responseError', {
        detail: { target: document.body },
      }),
    );

    expect(container.textContent).toContain('Loading your favourites');
  });
});

describe('removing a row from the panel', () => {
  it('tells the map its pins changed, so the deleted one goes', () => {
    // SNOW-658: the row's Remove is a plain HTMX form (nothing in this
    // module handles it), but nothing else refetches the map's own pins
    // either — deleting from the panel used to leave the pin on the map
    // until a reload, while deleting from the pin popup did not.
    btn.click();
    const container = rows();
    container.innerHTML = '<ul><li id="favourite-a1b2"><button></button></li></ul>';
    const button = container.querySelector('button');

    const changed = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', changed);

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: button } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:favourites-changed', changed);
  });

  it('stays quiet for a request that came from outside the rows', () => {
    btn.click();

    const changed = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', changed);

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: document.body } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(changed).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:favourites-changed', changed);
  });
});
