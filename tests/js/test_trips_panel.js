/*
 * tests/js/test_trips_panel.js — Vitest DOM tests for the installed phone
 * app's Saved → Trips sheet (static/js/trips_panel.js, SNOW-1479).
 *
 * What is worth asserting, and why:
 *
 *   - the rows come from trips:panel_list over HTMX, on EVERY open, into a
 *     body re-cloned from #trips-sheet-template, so a closed sheet keeps no
 *     stale row; what the rows contain (order, the organiser's menu, the
 *     participant's bare row) is asserted server-side, in
 *     tests/trips/test_panel_views.py;
 *   - a failed load says so, never "no trips yet", which would be a wrong
 *     statement about the reader's own data;
 *   - Share mints the trip's link at the uuid on the control, from the URL
 *     template on the rows container, and only the clipboard and failure
 *     outcomes speak;
 *   - Rename hands row_rename_commit.js the trips:rename template with
 *     ``__UUID__`` in it, and the row's own uuid attribute;
 *   - a Delete that swapped its row out re-reads the list, whose empty state
 *     only a fresh response carries;
 *   - the bridge, window.pwaTripsSheet, is frozen and opens and closes.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/row_removed.js';
import '../../static/js/map_sheet.js';

const ZERO = '00000000-0000-0000-0000-000000000000';
const UUID = '11111111-2222-3333-4444-555555555555';
const LIST_URL = '/trips/partials/list/';

document.body.innerHTML = `
  <div id="trips-sheet" hidden></div>
  <div id="map-sheet-toast" class="hidden"><span data-toast-body></span></div>
  <input type="hidden" name="csrfmiddlewaretoken" value="tok">
  <template id="trips-sheet-template">
    <div>
      <div data-trips-rows
           data-trips-url="${LIST_URL}"
           data-trips-share-url-template="/trips/${ZERO}/share/"
           data-trips-rename-url-template="/trips/partials/${ZERO}/rename/">
        <p>Loading your trips…</p>
      </div>
    </div>
  </template>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/trips_panel.js');

const sheet = document.getElementById('trips-sheet');
const rows = () => sheet.querySelector('[data-trips-rows]');
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
});

afterEach(() => {
  if (window.pwaTripsSheet.isOpen()) window.pwaTripsSheet.close();
  delete window.pwaShare;
  delete window.pwaRowRenameCommit;
  vi.restoreAllMocks();
});

describe('opening the sheet', () => {
  it('loads the rows from trips:panel_list into the rows container', () => {
    window.pwaTripsSheet.open();

    expect(sheet.hidden).toBe(false);
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    const [method, url, opts] = globalThis.htmx.ajax.mock.calls[0];
    expect(method).toBe('GET');
    expect(url).toBe(LIST_URL);
    expect(opts.target).toBe(rows());
    expect(opts.swap).toBe('innerHTML');
  });

  it('re-clones the body and re-reads the list on every open', () => {
    window.pwaTripsSheet.open();
    rows().innerHTML = '<div id="stale-row"></div>';
    window.pwaTripsSheet.close();

    window.pwaTripsSheet.open();

    expect(document.getElementById('stale-row')).toBeNull();
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(2);
  });
});

describe('a list load that fails', () => {
  it('says so, rather than claiming there are no trips', () => {
    window.pwaTripsSheet.open();

    document.dispatchEvent(
      new CustomEvent('htmx:responseError', { detail: { target: rows() } }),
    );

    expect(rows().textContent).toContain("Your trips couldn't be loaded");
  });

  it('ignores a failure aimed at some other target', () => {
    window.pwaTripsSheet.open();

    document.dispatchEvent(
      new CustomEvent('htmx:sendError', { detail: { target: document.body } }),
    );

    expect(rows().textContent).toContain('Loading your trips');
  });
});

describe("an organiser's Share", () => {
  it("mints the link at the row's uuid and hands it to the share sheet", async () => {
    const createShare = vi.fn(() => Promise.resolve('https://x.test/t/abc'));
    const shareOrCopy = vi.fn(() => Promise.resolve('shared'));
    window.pwaShare = { createShare, shareOrCopy };
    window.pwaTripsSheet.open();
    rows().innerHTML = `<button data-trip-panel-share="${UUID}">Share</button>`;

    rows().querySelector('[data-trip-panel-share]').click();
    await settle();

    expect(createShare).toHaveBeenCalledWith(`/trips/${UUID}/share/`, 'tok');
    expect(shareOrCopy).toHaveBeenCalledWith('https://x.test/t/abc');
  });

  it('says so when the link was copied instead', async () => {
    const toast = vi.spyOn(window.MapSheet, 'toast');
    window.pwaShare = {
      createShare: () => Promise.resolve('https://x.test/t/abc'),
      shareOrCopy: () => Promise.resolve('copied'),
    };
    window.pwaTripsSheet.open();
    rows().innerHTML = `<button data-trip-panel-share="${UUID}">Share</button>`;

    rows().querySelector('[data-trip-panel-share]').click();
    await settle();

    expect(toast).toHaveBeenCalledWith('Link copied.');
  });

  it('says so when minting the link fails', async () => {
    const toast = vi.spyOn(window.MapSheet, 'toast');
    window.pwaShare = {
      createShare: () => Promise.reject(new Error('offline')),
      shareOrCopy: vi.fn(),
    };
    window.pwaTripsSheet.open();
    rows().innerHTML = `<button data-trip-panel-share="${UUID}">Share</button>`;

    rows().querySelector('[data-trip-panel-share]').click();
    await settle();

    expect(window.pwaShare.shareOrCopy).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith("That couldn't be shared. Try again.");
  });
});

describe("an organiser's Rename", () => {
  it('hands the inline editor the trips:rename template and the uuid hook', () => {
    const handleClick = vi.fn(() => true);
    window.pwaRowRenameCommit = { handleClick };
    window.pwaTripsSheet.open();
    rows().innerHTML = `<button data-row-rename data-trip-rename="${UUID}">Rename</button>`;

    rows().querySelector('[data-trip-rename]').click();

    expect(handleClick).toHaveBeenCalledTimes(1);
    const options = handleClick.mock.calls[0][1];
    expect(options.uuidAttribute).toBe('data-trip-rename');
    expect(options.urlTemplate).toBe('/trips/partials/__UUID__/rename/');
    expect(options.csrfToken()).toBe('tok');
  });

  it('re-reads the list once the new name is saved', () => {
    const handleClick = vi.fn((_event, options) => {
      options.onCommitted();
      return true;
    });
    window.pwaRowRenameCommit = { handleClick };
    window.pwaTripsSheet.open();
    globalThis.htmx.ajax.mockClear();
    rows().innerHTML = `<button data-row-rename data-trip-rename="${UUID}">Rename</button>`;

    rows().querySelector('[data-trip-rename]').click();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
  });
});

describe("an organiser's Delete", () => {
  it('re-reads the list once the row has swapped out', () => {
    window.pwaTripsSheet.open();
    rows().innerHTML = '<form data-row-remove id="delete"></form>';
    globalThis.htmx.ajax.mockClear();
    const xhr = {};
    const form = document.getElementById('delete');

    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: form } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, elt: form, successful: true } }),
    );

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
  });
});

describe('the bridge', () => {
  it('is frozen, and opens and closes the sheet', () => {
    expect(Object.isFrozen(window.pwaTripsSheet)).toBe(true);
    window.pwaTripsSheet.open();
    expect(window.pwaTripsSheet.isOpen()).toBe(true);
    window.pwaTripsSheet.close();
    expect(window.pwaTripsSheet.isOpen()).toBe(false);
    expect(sheet.hidden).toBe(true);
  });
});
