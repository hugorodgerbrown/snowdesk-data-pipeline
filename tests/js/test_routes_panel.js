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
 *   - a failed list load says so. This panel opens offline and its list does
 *     not load offline, and falling through to the server partial's own "You
 *     have no saved routes yet." would be a wrong statement about the user's
 *     own data.
 *
 * There is no overlay-switch assertion anywhere in this file, unlike
 * test_favourites_panel.js: the panel has no "Display on the map" switch
 * because there is no routes layer until SNOW-687.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the anonymous branch
 * needs its own file (test_routes_panel_anonymous.js) — the same
 * per-module-instance-state reason the favourites panel's suites are split.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/inline_rename.js';
import '../../static/js/map_sheet.js';

// As apps.public.views._routes_context builds it — the map sheet asks for the
// lean row variant.
const LIST_URL = '/routes/partials/list/?variant=map';
const CREATE_URL = '/routes/partials/create/';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="true"
          data-signin-url="/sign-in/"
          data-route-create-url="${CREATE_URL}"
          data-route-list-url="${LIST_URL}"
          data-route-rename-url-template="/routes/partials/__UUID__/rename/"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div>
      <div data-routes-rows><p>Loading your routes…</p></div>
      <button type="button" data-panel-add>Add a route</button>
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
  // Online by default; the offline tests override it.
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
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

  it('renders no overlay switch — there is no routes layer yet (SNOW-687)', () => {
    btn.click();

    expect(sheet.querySelector('input[role="switch"]')).toBeNull();
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
    rows.innerHTML = '<ul><li><form id="rm"></form></li></ul>';
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
