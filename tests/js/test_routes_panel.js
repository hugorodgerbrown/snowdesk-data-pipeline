/*
 * tests/js/test_routes_panel.js — Vitest DOM tests for SNOW-686's routes
 * panel (static/js/routes.js).
 *
 * The fourth UGC panel, built to the shape SNOW-634 set for downloads and
 * SNOW-658 generalised: the roundel opens a panel listing the user's own
 * imported routes, and adding one is an action inside it.
 *
 * What is worth asserting, and why:
 *
 *   - the rows come from routes:list over HTMX, so each arrives with its own
 *     delete wiring rather than a JS-built copy of it;
 *   - the request goes to the URL the surface handed the module VERBATIM,
 *     query string and all. The server appends ``?variant=map`` so the sheet
 *     gets the lean row template; a module that rebuilt the path from the URL
 *     name would silently drop it and render the default variant's markup.
 *     What each template contains is asserted server-side, in
 *     tests/routes/test_views.py;
 *   - the add CTA opens the FILE PICKER — the panel's one real difference
 *     from favourites, which arms a place-picker on the map instead;
 *   - the upload posts multipart to routes:create and never sets a
 *     Content-Type, because the boundary is the browser's to generate;
 *   - upload is ONLINE-ONLY (it does not go through window.pwaMutationQueue —
 *     see routes.js's header), so an offline tap is refused with a line that
 *     says so rather than silently queued and dropped;
 *   - each status route_create distinguishes gets its own message, because
 *     "it didn't work" does not tell the user whether to pick another file,
 *     delete a route, or wait;
 *   - the add CTA is DELEGATED on the sheet, because the body is re-cloned on
 *     every open — a per-element listener would be bound to an element the
 *     next open throws away;
 *   - SNOW-830: Remove is one item inside the row's "…" menu now, so it
 *     asks first — a delegated `submit` confirm rather than `hx-confirm`,
 *     because the message is translated and names the row. The same
 *     block proves Share and Rename still resolve from inside a menu,
 *     which is the premise the markup change rests on;
 *
 *   - a failed list load says so. This panel opens offline and its list does
 *     not load offline, and falling through to the server partial's own "You
 *     have no saved routes yet." would be a wrong statement about the user's
 *     own data.
 *
 *   - the switch reflects window.pwaRoutesOverlay.isEnabled() on open,
 *     rather than a flag of this module's own that could drift from it.
 *     isEnabled(), NOT isVisible(): the switch states what the user asked
 *     for, and the roundel's ring states whether it reached the map. Painting
 *     the switch from paint would show the user's own setting flipping itself
 *     off whenever an enable found nothing to draw (SNOW-687, following the
 *     SNOW-658 review).
 *
 * SNOW-686 shipped this panel before its map layer existed, so this file
 * asserted the INVERSE — that no switch was rendered at all, a switch wired
 * to nothing being a worse lie than an absent one. SNOW-687 added the layer
 * and the bridge; the assertions below are that inversion.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the anonymous branch
 * needs its own file (test_routes_panel_anonymous.js) — the same
 * per-module-instance-state reason the favourites panel's suites are split.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/inline_rename.js';
import '../../static/js/row_rename_commit.js';
import '../../static/js/row_focus.js';
import '../../static/js/row_removed.js';
import '../../static/js/map_sheet.js';
import '../../static/js/share.js';

// As apps.public.views._routes_context builds it — the map sheet asks for the
// lean row variant.
const LIST_URL = '/routes/partials/list/?variant=map';
const CREATE_URL = '/routes/partials/create/';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="true"
          data-routes-upload-eligible="true"
          data-route-share-url-template="/routes/__UUID__/share/"
          data-signin-url="/sign-in/"
          data-route-create-url="${CREATE_URL}"
          data-route-list-url="${LIST_URL}"
          data-route-rename-url-template="/routes/partials/__UUID__/rename/"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div>
      <div data-routes-rows><p>Loading your routes…</p></div>
      <button type="button" data-panel-add>Add a route</button>
      <label for="map-routes-overlay-toggle">Display on the map</label>
      <input id="map-routes-overlay-toggle" type="checkbox" role="switch">
    </div>
  </template>
  <form id="route-upload-form" hidden>
    <input type="hidden" name="csrfmiddlewaretoken" value="tok">
    <input type="file" id="route-upload-input" name="file" accept=".gpx,application/gpx+xml">
  </form>
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class"
       class="hidden">
    <span data-toast-body></span>
  </div>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/routes.js');

const btn = document.getElementById('route-add-btn');
const sheet = document.getElementById('route-sheet');
const uploadInput = document.getElementById('route-upload-input');

/** The overlay switch inside the currently-rendered panel body. */
function overlaySwitch() {
  return sheet.querySelector('#map-routes-overlay-toggle');
}

/** The panel's add CTA, inside the currently-rendered body. */
function addCta() {
  return sheet.querySelector('[data-panel-add]');
}

/** Put one File on the hidden input and fire the change the user's pick fires.
 *
 * jsdom's FileList is read-only, so the property is redefined rather than
 * assigned — the module reads `files[0]` and clears `value`, both of which
 * this satisfies.
 *
 * @param {File|null} file The chosen file, or null for "picker dismissed".
 * @returns {void}
 */
function choose(file) {
  Object.defineProperty(uploadInput, 'files', {
    configurable: true,
    value: file ? [file] : [],
  });
  uploadInput.dispatchEvent(new Event('change', { bubbles: true }));
}

/** A minimal .gpx File. */
function gpxFile() {
  return new File(['<gpx></gpx>'], 'haute-route.gpx', {
    type: 'application/gpx+xml',
  });
}

/** The toast's current text. */
function toastText() {
  return document.getElementById('map-sheet-toast').textContent;
}

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200 }));
  window.pwaTelemetry = { emit: vi.fn() };
  // The map.js bridge, stubbed: routes.js reaches the overlay only through
  // this frozen shape, so the panel's whole contract with the layer is the
  // four methods below. isEnabled defaults false to match the overlay's own
  // default (opt-in, unlike favourites).
  window.pwaRoutesOverlay = {
    show: vi.fn(),
    hide: vi.fn(),
    isEnabled: vi.fn(() => false),
    isVisible: vi.fn(() => false),
  };
  // Online by default; the offline tests override it.
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
  delete window.pwaRoutesOverlay;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('opening and closing the panel', () => {
  it('opens the sheet on the list panel', () => {
    btn.click();

    expect(sheet.hidden).toBe(false);
    expect(sheet.querySelector('[data-routes-rows]')).not.toBeNull();
    expect(addCta()).not.toBeNull();
  });

  it('closes on a second tap of the roundel', () => {
    btn.click();
    btn.click();

    expect(sheet.hidden).toBe(true);
  });

  it('re-clones the body on every open, so no stale row survives', () => {
    btn.click();
    sheet.querySelector('[data-routes-rows]').innerHTML = '<p>stale</p>';
    btn.click();
    btn.click();

    expect(sheet.querySelector('[data-routes-rows]').textContent).not.toContain(
      'stale',
    );
  });

});

describe('the overlay switch', () => {
  it('reflects the overlay preference on open, not a flag of its own', () => {
    window.pwaRoutesOverlay.isEnabled = vi.fn(() => true);
    btn.click();
    expect(overlaySwitch().checked).toBe(true);

    // Re-opened after the preference moved elsewhere — the panel re-reads
    // rather than remembering what it drew last time.
    btn.click();
    window.pwaRoutesOverlay.isEnabled = vi.fn(() => false);
    btn.click();
    expect(overlaySwitch().checked).toBe(false);
  });

  it('reads isEnabled(), never isVisible()', () => {
    // The state where the two disagree: the user enabled the overlay and
    // nothing was drawn (offline, nothing cached). The switch must keep
    // showing their choice — the roundel's ring is what reports that it
    // never reached the map. A switch painted from isVisible() would appear
    // to silently undo the setting the user just made.
    window.pwaRoutesOverlay.isEnabled = vi.fn(() => true);
    window.pwaRoutesOverlay.isVisible = vi.fn(() => false);

    btn.click();

    expect(overlaySwitch().checked).toBe(true);
    expect(window.pwaRoutesOverlay.isVisible).not.toHaveBeenCalled();
  });

  it('drives the bridge in both directions', () => {
    btn.click();
    const toggle = overlaySwitch();

    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(window.pwaRoutesOverlay.show).toHaveBeenCalledTimes(1);

    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(window.pwaRoutesOverlay.hide).toHaveBeenCalledTimes(1);
  });

  it('is delegated on the sheet, so it survives a re-clone', () => {
    // The body is replaced wholesale on every open, so the element carrying
    // this switch is thrown away each time. A listener bound to the input
    // itself would work exactly once.
    btn.click();
    btn.click();
    btn.click();

    const toggle = overlaySwitch();
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));

    expect(window.pwaRoutesOverlay.show).toHaveBeenCalledTimes(1);
  });

  it('survives a missing bridge rather than throwing', () => {
    // map.js publishes the bridge at parse time, but routes.js loads from
    // its own surface partial and does not depend on the map bundle being
    // present at all (the panel opens on pages the map does not). An absent
    // bridge must leave the panel usable, not break the open.
    delete window.pwaRoutesOverlay;

    expect(() => btn.click()).not.toThrow();
    expect(overlaySwitch().checked).toBe(false);

    const toggle = overlaySwitch();
    toggle.checked = true;
    expect(() =>
      toggle.dispatchEvent(new Event('change', { bubbles: true })),
    ).not.toThrow();
  });
});

describe('loading the rows', () => {
  it('fetches routes:list over HTMX into the rows container', () => {
    btn.click();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    const [method, url, opts] = globalThis.htmx.ajax.mock.calls[0];
    expect(method).toBe('GET');
    expect(opts.target).toBe(sheet.querySelector('[data-routes-rows]'));
    expect(opts.swap).toBe('innerHTML');
    // Verbatim, ?variant=map included — see this file's header.
    expect(url).toBe(LIST_URL);
  });

  it('says so when the list does not load, rather than looking empty', () => {
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');

    document.dispatchEvent(
      new CustomEvent('htmx:sendError', { detail: { target: rows } }),
    );

    expect(rows.textContent).toContain("couldn't be loaded");
  });

  it('ignores a failure aimed at somebody else’s target', () => {
    // Every HTMX request on the map page runs through this document-level
    // listener — the other three panels share the page.
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');

    document.dispatchEvent(
      new CustomEvent('htmx:responseError', {
        detail: { target: document.createElement('div') },
      }),
    );

    expect(rows.textContent).not.toContain("couldn't be loaded");
  });
});

describe('the add CTA opens the file picker', () => {
  it('clicks the hidden file input', () => {
    const picker = vi.spyOn(uploadInput, 'click');
    btn.click();

    addCta().click();

    expect(picker).toHaveBeenCalledTimes(1);
  });

  it('is delegated on the sheet, so it survives a re-clone', () => {
    const picker = vi.spyOn(uploadInput, 'click');
    btn.click();
    btn.click();
    btn.click();

    addCta().click();

    expect(picker).toHaveBeenCalledTimes(1);
  });

  it('refuses offline before opening the picker, not after the choice', () => {
    // Making somebody find a file and THEN telling them it cannot be sent
    // wastes the one action they took. Upload is online-only by design —
    // see routes.js's header on why the mutation queue is not used.
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    const picker = vi.spyOn(uploadInput, 'click');
    btn.click();

    addCta().click();

    expect(picker).not.toHaveBeenCalled();
    expect(toastText()).toContain('online');
  });
});

describe('uploading a chosen file', () => {
  it('posts it as multipart to routes:create', async () => {
    btn.click();

    choose(gpxFile());
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(CREATE_URL);
    expect(opts.method).toBe('POST');
    expect(opts.body).toBeInstanceOf(FormData);
    expect(opts.body.get('file').name).toBe('haute-route.gpx');
    // route_create is @require_htmx, and this is a plain fetch.
    expect(opts.headers['HX-Request']).toBe('true');
    expect(opts.headers['X-CSRFToken']).toBe('tok');
  });

  it('never sets Content-Type — the boundary is the browser’s to generate', async () => {
    btn.click();

    choose(gpxFile());
    await Promise.resolve();

    const [, opts] = globalThis.fetch.mock.calls[0];
    expect(opts.headers['Content-Type']).toBeUndefined();
  });

  it('does not go through the mutation queue', async () => {
    // Deliberately online-only: the queue persists JSON bodies in IndexedDB
    // and a multipart file is a different shape. SNOW-504's resort toggle is
    // the precedent.
    const enqueue = vi.fn();
    window.pwaMutationQueue = { enqueue: enqueue };
    btn.click();

    choose(gpxFile());
    await Promise.resolve();

    expect(enqueue).not.toHaveBeenCalled();
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    delete window.pwaMutationQueue;
  });

  it('re-reads the list on success rather than swapping the response in', async () => {
    // route_create answers with the DEFAULT variant's row (_route.html),
    // which is the wrong shape for this panel — so the truth is refetched.
    btn.click();

    choose(gpxFile());
    await Promise.resolve();
    await Promise.resolve();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(2);
    expect(globalThis.htmx.ajax.mock.calls[1][1]).toBe(LIST_URL);
  });

  it('tells the map, so the new route is drawn without a reload', async () => {
    // The overlay's source is already installed for anybody with the layer
    // switched on, so nothing else would ever put the uploaded line on it.
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);
    btn.click();

    choose(gpxFile());
    await Promise.resolve();
    await Promise.resolve();

    expect(changed).toHaveBeenCalled();
    document.removeEventListener('snowdesk:routes-changed', changed);
  });

  it('emits telemetry for the upload', async () => {
    btn.click();

    choose(gpxFile());
    await Promise.resolve();
    await Promise.resolve();

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.route.created', {});
  });

  it('clears the input, so re-picking the same file fires again', async () => {
    btn.click();

    choose(gpxFile());
    await Promise.resolve();

    // A `change` event only fires when the value actually changes, so a
    // stale value left here would make a retry of the same file a no-op.
    expect(uploadInput.value).toBe('');
  });

  it('does nothing when the picker is dismissed without a file', () => {
    btn.click();

    choose(null);

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe('an upload the server refuses', () => {
  /**
   * Choose a file against a stubbed failure status and settle the promises.
   *
   * @param {number} status The status route_create answers with.
   * @returns {Promise<void>}
   */
  async function uploadFailing(status) {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: status }));
    btn.click();
    choose(gpxFile());
    await Promise.resolve();
    await Promise.resolve();
  }

  it('tells the user to pick another file on 400', async () => {
    await uploadFailing(400);
    expect(toastText()).toContain('GPX');
  });

  it('tells the user to delete a route on 409', async () => {
    await uploadFailing(409);
    expect(toastText()).toContain('limit');
  });

  it('names the size problem on 413', async () => {
    await uploadFailing(413);
    expect(toastText()).toContain('too large');
  });

  it('falls back to a generic retry line on anything else', async () => {
    await uploadFailing(429);
    expect(toastText()).toContain('Try again');
  });

  it('toasts when the request itself fails', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('network')));
    btn.click();

    choose(gpxFile());
    await Promise.resolve();
    await Promise.resolve();

    expect(toastText()).toContain('Try again');
  });

  it('does not re-read the list when the upload failed', async () => {
    await uploadFailing(400);

    // One call for the open, and no second one — a refetch here would
    // suggest something had changed.
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
  });
});

describe('a row removed from the panel', () => {
  it('emits telemetry when the row’s own Remove form succeeds', () => {
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = '<ul><li><form id="rm" data-row-remove></form></li></ul>';
    const xhr = {};

    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr: xhr, elt: rows.querySelector('#rm') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', {
        detail: { xhr: xhr, successful: true },
      }),
    );

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.route.deleted', {});
  });

  it('re-reads the list, so the empty state arrives with the last row', () => {
    // The row's own hx-swap empties one <li> and nothing else, so a panel
    // that has just lost its last route keeps rendering as a list of none:
    // routes:list's empty state is a server-side clause and only a fresh
    // response can carry it. Hugo, deleting the last row: it "doesn't show
    // until you refresh the page".
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = '<ul><li><form id="rm" data-row-remove></form></li></ul>';
    globalThis.htmx.ajax.mockClear();
    const xhr = {};

    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr: xhr, elt: rows.querySelector('#rm') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', {
        detail: { xhr: xhr, successful: true },
      }),
    );

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
  });

  it('tells the map, so the deleted route’s line goes with the row', () => {
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = '<ul><li><form id="rm" data-row-remove></form></li></ul>';
    const xhr = {};

    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr: xhr, elt: rows.querySelector('#rm') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', {
        detail: { xhr: xhr, successful: true },
      }),
    );

    expect(changed).toHaveBeenCalled();
    document.removeEventListener('snowdesk:routes-changed', changed);
  });

  it('ignores an HTMX request from outside the panel’s rows', () => {
    // The mark rides the request's own xhr, so a request from one of the
    // other three panels on this page cannot be mistaken for this one's.
    btn.click();
    const xhr = {};

    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr: xhr, elt: document.createElement('form') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', {
        detail: { xhr: xhr, successful: true },
      }),
    );

    expect(window.pwaTelemetry.emit).not.toHaveBeenCalled();
  });
});

describe('framing a route from its row', () => {
  // The panel's share of window.pwaRowFocus (covered on its own in
  // test_row_focus.js) is three lines: which overlay is ours, how this
  // sheet closes, and that the delegated handler asks about the name
  // before anything else. A missing wire is invisible from either side, so
  // it is asserted here.

  /** Put one row carrying `target` into the open panel. */
  function renderRow(target) {
    btn.click();
    sheet.querySelector('[data-routes-rows]').innerHTML = `
      <ul><li data-row-renameable>
        <button data-row-label data-row-focus="${target}">Haute Route</button>
      </li></ul>`;
    return sheet.querySelector('[data-row-focus]');
  }

  beforeEach(() => {
    window.pwaMapFocus = { point: vi.fn(), bounds: vi.fn() };
  });

  afterEach(() => {
    delete window.pwaMapFocus;
  });

  it('fits the route’s bbox and closes the panel', () => {
    // Closing matters most on a phone, where the sheet is a full-width
    // bottom dock covering the map it just flew.
    renderRow('7.1,46.0,7.3,46.2').click();

    expect(window.pwaMapFocus.bounds).toHaveBeenCalledWith([7.1, 46.0, 7.3, 46.2]);
    expect(sheet.hidden).toBe(true);
  });

  it('switches the routes overlay on first', () => {
    // The overlay defaults OFF (opt-in, unlike favourites), so without this
    // the common case is a flight to an empty map.
    renderRow('7.1,46.0,7.3,46.2').click();

    expect(window.pwaRoutesOverlay.show).toHaveBeenCalledOnce();
  });

  it('does not treat the name as a rename', () => {
    // The two controls are the name and the pencil, and SNOW-658 settled
    // that they stay different controls.
    const row = renderRow('7.1,46.0,7.3,46.2');
    row.click();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Forced-offline gating (SNOW-748) — the account menu's offline-mode toggle
// can put the
// app in an offline mode while the interface stays up. An upload is
// online-only, and "online" has to mean the mode the user chose, not the
// state of the radio: `navigator.onLine` is true throughout the first two
// cases below. The `auto` case is the regression guard — a swap that refused
// every upload would satisfy the forced ones on its own.
// ---------------------------------------------------------------------------

describe('forced-offline gating (SNOW-748)', () => {
  afterEach(() => {
    delete window.pwaConnectivity;
  });

  it('refuses the picker under a forced mode, with the interface up', () => {
    window.pwaConnectivity = { isOnline: () => false };
    expect(navigator.onLine).toBe(true);
    const picker = vi.spyOn(uploadInput, 'click');
    btn.click();

    addCta().click();

    expect(picker).not.toHaveBeenCalled();
    expect(toastText()).toContain('online');
  });

  it('refuses the upload itself under a forced mode', () => {
    // The second gate, in `upload()` — the picker may already have been open
    // when the toggle was pressed, so the choice has to be refused too rather
    // than posted over a connection the user asked the app not to spend.
    btn.click();
    window.pwaConnectivity = { isOnline: () => false };

    choose(gpxFile());

    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(toastText()).toContain('online');
  });

  it('still opens the picker and posts under the auto mode', async () => {
    window.pwaConnectivity = { isOnline: () => true };
    const picker = vi.spyOn(uploadInput, 'click');
    btn.click();

    addCta().click();
    expect(picker).toHaveBeenCalledTimes(1);

    choose(gpxFile());
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(globalThis.fetch.mock.calls[0][0]).toBe(CREATE_URL);
  });
});

// ---------------------------------------------------------------------------
// A refused claim, and a claim made somewhere else.
//
// Both are gaps between the panel and the two things it does not own: HTMX's
// own default response handling, and the map popup's Save.
// ---------------------------------------------------------------------------

describe('a claim the server refused', () => {
  /** Put one pending (unclaimed) row into the open panel.
   *
   * The shape routes:list renders for a followed-but-unclaimed share: the
   * row's DOM id is `route-share-<token>`, which is also the `hx-target`
   * its own Save form posts to.
   *
   * @param {string} token The share token, as the server writes it.
   * @returns {HTMLElement} The row element.
   */
  function renderPendingRow(token) {
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = `<ul><li id="route-share-${token}">
      <form data-row-claimed hx-post="/routes/partials/share/${token}/claim/"
            hx-target="#route-share-${token}" hx-swap="outerHTML"></form>
    </li></ul>`;
    return rows.querySelector(`#route-share-${token}`);
  }

  /** Fire htmx's own beforeSwap for a response aimed at `target`.
   *
   * `shouldSwap: false` is what htmx 2 puts in the detail for the whole
   * 4xx/5xx range, which is the behaviour under test.
   *
   * @param {HTMLElement} target The element htmx would swap into.
   * @param {number} status The response status.
   * @returns {object} The detail, after every listener has seen it.
   */
  function beforeSwap(target, status) {
    const detail = {
      xhr: { status: status },
      target: target,
      shouldSwap: false,
      isError: true,
    };
    document.dispatchEvent(new CustomEvent('htmx:beforeSwap', { detail: detail }));
    return detail;
  }

  it('swaps the 409 limit body htmx would have discarded', () => {
    // route_share_claim renders _route_limit.html on a 409 and htmx throws
    // it away, so a user at their saved-route cap pressed Save and saw
    // nothing happen at all.
    const row = renderPendingRow('tok');

    expect(beforeSwap(row, 409).shouldSwap).toBe(true);
  });

  it('does the same for every other status the endpoint explains', () => {
    const row = renderPendingRow('tok');

    for (const status of [403, 404, 429]) {
      expect(beforeSwap(row, status).shouldSwap).toBe(true);
    }
  });

  it('leaves a status the endpoint does not author alone', () => {
    // A 5xx body is a proxy's error page, which explains less in a route
    // row than the row the user was already looking at.
    const row = renderPendingRow('tok');

    expect(beforeSwap(row, 500).shouldSwap).toBe(false);
  });

  it('leaves another surface’s target alone', () => {
    // Four panels share this document, and forcing the whole 4xx range to
    // swap globally would push every one of their error bodies onto the
    // page.
    renderPendingRow('tok');
    const other = document.createElement('div');
    other.id = 'favourite-abc';
    sheet.appendChild(other);

    expect(beforeSwap(other, 409).shouldSwap).toBe(false);
  });

  it('leaves a row outside this panel alone', () => {
    // The same id, rendered by the account page's own list — which has no
    // claim form and no handler for one.
    renderPendingRow('tok');
    const elsewhere = document.createElement('li');
    elsewhere.id = 'route-share-tok';

    expect(beforeSwap(elsewhere, 409).shouldSwap).toBe(false);
  });

  it('keeps isError true, so the failure does not re-read the list', () => {
    // htmx derives detail.successful from it, and window.pwaRowRemoved's
    // claim watcher fires on a successful request — clearing it would
    // refetch the list straight over the message just swapped in, leaving
    // the user with no explanation again.
    const row = renderPendingRow('tok');

    expect(beforeSwap(row, 409).isError).toBe(true);
  });
});

describe('a route claimed from the map popup', () => {
  // The popup's Save (static/js/map.js's appendRouteClaimCta) claims with a
  // plain fetch, so none of the HTMX mark-pairs above ever see it. A panel
  // left open behind the popup went on listing the route as pending, with a
  // Save that would 404, until it was closed and reopened.

  it('re-reads the rows when the map announces the change', () => {
    btn.click();
    globalThis.htmx.ajax.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
  });

  it('costs nothing while the panel is shut', () => {
    // Closing hides the sheet without emptying it, so the rows container is
    // still findable — and the next open re-clones the body and re-reads the
    // list anyway.
    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });
});

describe('sharing a row (SNOW-764)', () => {
  // window.pwaShare is frozen, so its functions cannot be spied on (the
  // same property every window.pwa* bridge has). The platform APIs
  // underneath it are stubbed instead, which has the better property of
  // exercising the real helper — the branch that must NOT copy after a
  // cancelled sheet is the one worth testing end to end.
  const realNavigator = window.navigator;

  /** Install a navigator with the given share/clipboard behaviour.
   *
   * @param {object} parts Partial navigator — `share` and/or `clipboard`.
   * @returns {void}
   */
  function stubNavigator(parts) {
    window.navigator = Object.assign({ onLine: true }, parts);
  }

  afterEach(() => {
    window.navigator = realNavigator;
  });

  /** Put one owned row, carrying a Share control, into the open panel.
   *
   * The row is the shape routes:list renders for the map variant — only
   * the hook the delegated handler reads matters here, since the button is
   * server-rendered and this module never builds one.
   *
   * @param {string} uuid The route's uuid, as the server writes it.
   * @returns {HTMLElement} The Share button.
   */
  function renderShareRow(uuid) {
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = `<ul><li><button type="button"
      data-route-share="${uuid}" aria-label="Share it"></button></li></ul>`;
    return rows.querySelector('[data-route-share]');
  }

  /** Answer the mint endpoint with a share URL.
   *
   * @returns {void}
   */
  function mintSucceeds() {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ url: 'https://snowdesk.app/routes/s/tok/' }),
      }),
    );
  }

  it('mints a link from the uuid on the button, then shares it', async () => {
    mintSucceeds();
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share: share });
    btn.click();

    renderShareRow('abc-123').click();
    await vi.waitFor(() => expect(share).toHaveBeenCalled());

    expect(globalThis.fetch.mock.calls[0][0]).toBe('/routes/abc-123/share/');
    expect(share.mock.calls[0][0].url).toBe('https://snowdesk.app/routes/s/tok/');
  });

  it('says nothing when the native sheet took it', async () => {
    mintSucceeds();
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share: share });
    btn.click();

    renderShareRow('abc-123').click();
    await vi.waitFor(() => expect(share).toHaveBeenCalled());

    expect(toastText()).not.toContain('Link copied');
  });

  it('neither copies nor announces when the user cancels the sheet', async () => {
    // Announcing a copy here would contradict the user's own decision, and
    // performing one would do the thing they just declined.
    mintSucceeds();
    const abort = new Error('cancelled');
    abort.name = 'AbortError';
    const writeText = vi.fn(() => Promise.resolve());
    const share = vi.fn(() => Promise.reject(abort));
    stubNavigator({ share: share, clipboard: { writeText: writeText } });
    btn.click();

    renderShareRow('abc-123').click();
    await vi.waitFor(() => expect(share).toHaveBeenCalled());

    expect(writeText).not.toHaveBeenCalled();
    expect(toastText()).not.toContain('Link copied');
  });

  it('confirms a clipboard copy, which has no other visible sign', async () => {
    mintSucceeds();
    stubNavigator({ clipboard: { writeText: vi.fn(() => Promise.resolve()) } });
    btn.click();

    renderShareRow('abc-123').click();

    await vi.waitFor(() => expect(toastText()).toContain('Link copied'));
  });

  it('reports a failed mint', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 404 }));
    stubNavigator({ share: vi.fn(() => Promise.resolve()) });
    btn.click();

    renderShareRow('abc-123').click();

    await vi.waitFor(() => expect(toastText()).toContain("couldn't be created"));
  });

  it('emits the share telemetry only once a link exists', async () => {
    // A tap that failed to mint has shared nothing.
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 500 }));
    stubNavigator({ share: vi.fn(() => Promise.resolve()) });
    btn.click();

    renderShareRow('abc-123').click();
    await vi.waitFor(() => expect(toastText()).toContain("couldn't be created"));

    expect(window.pwaTelemetry.emit).not.toHaveBeenCalledWith(
      'map.route.shared',
      expect.anything(),
    );
  });

  it('emits it when the link is minted', async () => {
    mintSucceeds();
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share: share });
    btn.click();

    renderShareRow('abc-123').click();
    await vi.waitFor(() => expect(share).toHaveBeenCalled());

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.route.shared', {});
  });
});


describe('the sheet-level bridge (SNOW-803)', () => {
  it('exposes window.pwaRoutesSheet.open(), which opens the panel like a tap', () => {
    expect(Object.isFrozen(window.pwaRoutesSheet)).toBe(true);
    window.pwaRoutesSheet.open();
    expect(window.pwaRoutesSheet.isOpen()).toBe(true);
    expect(sheet.hidden).toBe(false);
    expect(sheet.querySelector('[data-routes-rows]')).not.toBeNull();
    window.pwaRoutesSheet.close();
    expect(window.pwaRoutesSheet.isOpen()).toBe(false);
  });
});


describe("removing a row from inside the row's menu (SNOW-830)", () => {
  // The trash used to be a 44x44 control a user aimed at directly; it is
  // now one item in the row's "…" menu, where a mis-tap costs a route
  // with no way back. The confirm is a delegated `submit` on the sheet
  // rather than `hx-confirm`, because the message is translated and names
  // the row — neither of which a template attribute literal can be.
  // THE CONFIRM IS NOT TESTED HERE, and the four tests that were are
  // gone. They dispatched a synthetic `submit` into a jsdom document with
  // no htmx in it, so the sheet's delegated listener was the only handler
  // on the event and `defaultPrevented` came back true. In a real browser
  // htmx binds to the FORM — a descendant — so its handler ran first and
  // the DELETE was already away; declining the dialogue removed the route
  // anyway. The tests passed for the whole of that bug's life.
  //
  // The confirm is now `hx-confirm` on the form, which htmx owns, and the
  // assertion that it is rendered lives where it can be made against real
  // markup: tests/routes/test_views.py's TestRouteRowMenu. Reproducing
  // htmx's own event ordering in jsdom would be testing htmx, not us.

  /** Put one owned row into the open panel, its controls inside a menu.
   *
   * The shape routes:list renders since SNOW-830 — the four controls
   * moved inside `[data-overflow-menu]` in the same `<li>`, which is what
   * these tests have to prove the delegated readers survived.
   *
   * @param {string} uuid The route's uuid, as the server writes it.
   * @returns {HTMLElement} The row's `<li>`.
   */
  function renderMenuRow(uuid) {
    const rows = sheet.querySelector('[data-routes-rows]');
    rows.innerHTML = `<ul><li id="route-${uuid}" data-row-renameable>
      <span data-row-label>Haute Route</span>
      <input type="text" data-row-rename-input hidden aria-label="Route name">
      <div data-overflow-menu>
        <button type="button" data-overflow-trigger aria-expanded="false"></button>
        <ul role="menu" hidden>
          <li role="none"><button type="button" role="menuitem"
            data-route-share="${uuid}" aria-label="Share Haute Route"></button></li>
          <li role="none"><button type="button" role="menuitem" data-row-rename
            data-route-rename="${uuid}" aria-label="Rename Haute Route"></button></li>
          <li role="none"><form data-row-remove hx-post="/routes/partials/${uuid}/delete/"
            hx-target="#route-${uuid}" hx-swap="outerHTML">
            <button type="submit" role="menuitem" aria-label="Remove Haute Route"></button>
          </form></li>
        </ul>
      </div>
    </li></ul>`;
    return rows.querySelector('li');
  }

  it('still resolves Share from inside the menu, by closest() and not by depth', () => {
    // The whole premise of SNOW-830's markup change: every delegated
    // reader walks UP from the clicked element, so a control nested two
    // levels deeper is the same control.
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ url: 'https://snowdesk.app/routes/s/tok/' }),
      }),
    );
    btn.click();
    const row = renderMenuRow('abc-123');

    row.querySelector('[data-route-share]').click();

    expect(globalThis.fetch.mock.calls[0][0]).toBe('/routes/abc-123/share/');
  });

  it('still opens the inline editor from a Rename inside the menu', () => {
    btn.click();
    const row = renderMenuRow('abc-123');

    row.querySelector('[data-row-rename]').click();

    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(false);
  });
});
