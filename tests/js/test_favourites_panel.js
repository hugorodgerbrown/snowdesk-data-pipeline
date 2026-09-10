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
 *     the user's own data;
 *
 *   - SNOW-886: saving a pin SHOWS it. The create rendered a confirmation
 *     card and stopped there — the favourites layer stayed off if that is
 *     how the user had it, so the new pin (and the optimistic marker the
 *     same handler dispatches) landed on a map drawing no favourites at
 *     all, which reads as a save that failed. The overlay goes on and the
 *     camera moves, the same two steps pressing an existing row takes,
 *     with the ORDER asserted because only the order can be got wrong
 *     silently.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the anonymous branch
 * needs its own file (test_favourites_panel_anonymous.js) — the same
 * per-module-instance-state reason test_report_gate_*.js are split.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_focus.js';
import '../../static/js/row_removed.js';
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
    </div>
  </template>
  <template id="favourite-create-template">
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      <input type="hidden" name="lat">
      <input type="hidden" name="lon">
    </form>
  </template>
  <template id="favourite-confirmation-template">
    <div>
      <p>Pin saved!</p>
      <p data-favourite-pending hidden>Saved — will sync when you're back online.</p>
    </div>
  </template>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

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

// SNOW-904 removed this file's "the overlay switch" block. The panel no
// longer carries a "Display on the map" switch — every layer is switched
// from the map's layers menu, whose row drives the same
// window.pwaFavouritesOverlay bridge. Its coverage lives in
// tests/js/test_map_layers_menu.js.

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
    container.innerHTML =
      '<ul><li id="favourite-a1b2"><form data-row-remove><button></button></form></li></ul>';
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

  it('re-reads the list, so the empty state arrives with the last row', () => {
    // The row's own hx-swap empties one <li> and nothing else, so a panel
    // that has just lost its last pin keeps rendering as a list of none:
    // favourites:list's empty state is a server-side clause and only a
    // fresh response can carry it. Hugo, deleting the last row: it
    // "doesn't show until you refresh the page". The refetch is also what
    // re-emits the roster sidecar favourites_offline.js reconciles its
    // cached copy against.
    btn.click();
    const container = rows();
    container.innerHTML =
      '<ul><li id="favourite-a1b2"><form data-row-remove><button></button></form></li></ul>';
    const button = container.querySelector('button');
    globalThis.htmx.ajax.mockClear();

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: button } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
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

describe('framing a favourite from its row', () => {
  // The panel's share of window.pwaRowFocus (covered on its own in
  // test_row_focus.js) is which overlay is ours and how this sheet closes.
  // A missing wire is invisible from either side, so it is asserted here.
  //
  // A favourite is a dropped pin or a saved resort — one model, so one
  // control: Hugo's "routes, resorts, and observations" names two of the
  // three things this single row can be.

  /** Put one row carrying `target` into the open panel. */
  function renderRow(target) {
    btn.click();
    rows().innerHTML = `
      <ul><li data-row-renameable>
        <button data-row-label data-row-focus="${target}">Mont Fort</button>
      </li></ul>`;
    return sheet.querySelector('[data-row-focus]');
  }

  beforeEach(() => {
    window.pwaMapFocus = { point: vi.fn(), bounds: vi.fn() };
  });

  afterEach(() => {
    delete window.pwaMapFocus;
  });

  it('flies to the pin and closes the panel', () => {
    renderRow('7.5,46.1').click();

    expect(window.pwaMapFocus.point).toHaveBeenCalledWith(7.5, 46.1);
    expect(sheet.hidden).toBe(true);
  });

  it('leaves an already-enabled overlay alone', () => {
    // This overlay defaults ON, so the usual press has nothing to switch.
    renderRow('7.5,46.1').click();

    expect(overlay.show).not.toHaveBeenCalled();
  });

  it('switches the overlay on when the user had it off', () => {
    overlay.isEnabled.mockReturnValue(false);

    renderRow('7.5,46.1').click();

    expect(overlay.show).toHaveBeenCalledOnce();
  });

  it('does not start the create flow', () => {
    // The name and the add CTA are both clicks on the same delegated
    // handler; the name has to stop it before the CTA test.
    renderRow('7.5,46.1').click();

    expect(window.PlacePicker.activate).not.toHaveBeenCalled();
  });
});


describe('the sheet-level bridge (SNOW-803)', () => {
  it('exposes window.pwaFavouritesSheet.open(), which opens the panel like a tap', () => {
    expect(Object.isFrozen(window.pwaFavouritesSheet)).toBe(true);
    window.pwaFavouritesSheet.open();
    expect(window.pwaFavouritesSheet.isOpen()).toBe(true);
    expect(sheet.hidden).toBe(false);
    expect(sheet.querySelector('[data-favourites-rows]')).not.toBeNull();
    window.pwaFavouritesSheet.close();
    expect(window.pwaFavouritesSheet.isOpen()).toBe(false);
  });
});

describe('saving a pin shows it on the map (SNOW-886)', () => {
  /** @type {{point: Function, bounds: Function}} */
  let focus;

  /**
   * Submit the create form the surface partial renders, exactly as the
   * Save button does — the handler is delegated from `document` and reads
   * the form's own fields, so a real submit event is the whole gesture.
   *
   * @param {?string} lat The form's latitude, or '' for an unplaced pin.
   * @param {?string} lon The form's longitude.
   * @returns {void}
   */
  function saveFavourite(lat, lon) {
    sheet.innerHTML = `
      <form id="favourite-create-form">
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
        <input type="hidden" name="lat" value="${lat}">
        <input type="hidden" name="lon" value="${lon}">
        <input type="text" name="name" value="Col des Gentianes">
      </form>`;
    sheet.querySelector('#favourite-create-form').dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    );
  }

  beforeEach(() => {
    // The queue's own behaviour is tests/js/test_mutation_queue.js's; what
    // matters here is that the create gets past it to the two moves below.
    window.pwaMutationQueue = { enqueue: vi.fn(() => Promise.resolve()) };
    window.pwaDb = { isResetRequired: () => false };
    window.pwaTelemetry = { emit: vi.fn() };
    focus = { point: vi.fn(), bounds: vi.fn() };
    window.pwaMapFocus = focus;
  });

  afterEach(() => {
    delete window.pwaMutationQueue;
    delete window.pwaDb;
    delete window.pwaTelemetry;
    delete window.pwaMapFocus;
  });

  it('flies to the pin it just saved', () => {
    saveFavourite('46.1', '7.2');

    // [lon, lat] — the map's order, not the form's.
    expect(focus.point).toHaveBeenCalledWith(7.2, 46.1);
  });

  it('switches the favourites layer on when the user has it off', () => {
    overlay.isEnabled = vi.fn(() => false);

    saveFavourite('46.1', '7.2');

    expect(overlay.show).toHaveBeenCalled();
  });

  it('leaves the layer alone when it is already on', () => {
    // isEnabled(), the persisted preference — the same distinction the
    // panel's own switch reads.
    saveFavourite('46.1', '7.2');

    expect(overlay.show).not.toHaveBeenCalled();
    expect(focus.point).toHaveBeenCalled();
  });

  it('switches the layer on before it moves the camera', () => {
    // Arriving first and enabling second would show the user an empty map
    // and then paint the pin into it, which is the flicker the row-press
    // path was written to avoid.
    const order = [];
    overlay.isEnabled = vi.fn(() => false);
    overlay.show = vi.fn(() => order.push('overlay'));
    focus.point = vi.fn(() => order.push('camera'));

    saveFavourite('46.1', '7.2');

    expect(order).toEqual(['overlay', 'camera']);
  });

  it('moves nothing when the form carries no usable coordinate', () => {
    // The same !isNaN guard the optimistic marker is drawn behind: a
    // coordinate too broken to draw a marker at is too broken to fly to,
    // and NaN would take the camera somewhere arbitrary.
    saveFavourite('', '');

    expect(focus.point).not.toHaveBeenCalled();
  });

  it('still renders the confirmation card', () => {
    // The card is the panel's answer to "did that work" and is why the
    // reveal deliberately does NOT close the sheet.
    saveFavourite('46.1', '7.2');

    expect(sheet.textContent).toContain('Pin saved!');
  });
});
