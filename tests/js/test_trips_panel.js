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
 *   - the bridge, window.pwaTripsSheet, is frozen and opens and closes;
 *   - the header's + opens a picker of the reader's own routes, read from
 *     routes:geojson on every open: one link per route to
 *     trips:new?route=<uuid> with its name and meta line (pending shares,
 *     which carry no uuid, skipped); no routes, one item to the Routes tab
 *     (data-saved-go="routes"); a failed read, one line saying so.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/row_removed.js';
import '../../static/js/map_sheet.js';
import '../../static/js/i18n_strings.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/overflow_menu.js';

const ZERO = '00000000-0000-0000-0000-000000000000';
const UUID = '11111111-2222-3333-4444-555555555555';
const LIST_URL = '/trips/partials/list/';
const ROUTES_URL = '/routes/routes.geojson';
const NEW_URL = '/trips/new/';

document.body.innerHTML = `
  <div id="trips-sheet" hidden></div>
  <div id="map-sheet-toast" class="hidden"><span data-toast-body></span></div>
  <input type="hidden" name="csrfmiddlewaretoken" value="tok">
  <template id="trips-strings-template">
    <span data-string="picker-empty">Add a route first</span>
    <span data-string="picker-error">Your routes couldn't be loaded.</span>
  </template>
  <template id="trips-route-picker-item-template">
    <a role="menuitem" data-trip-route-picker-item>
      <span data-trip-route-picker-name></span>
      <span data-trip-route-picker-meta></span>
    </a>
  </template>
  <template id="trips-sheet-template">
    <div>
      <div data-overflow-menu>
        <button type="button" data-overflow-trigger aria-label="Plan a trip"></button>
        <ul role="menu" hidden>
          <li role="none"><p>Plan a trip from</p></li>
          <li role="none" data-trip-route-picker-items
              data-routes-url="${ROUTES_URL}"
              data-trip-new-url="${NEW_URL}"></li>
        </ul>
      </div>
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

describe("the +, a picker of the reader's routes", () => {
  const picker = () => sheet.querySelector('[data-trip-route-picker-items]');
  const plus = () => sheet.querySelector('[data-overflow-trigger]');

  /** @param {object} body The geojson the fetch answers. */
  function answer(body) {
    return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 }),
    );
  }

  it('reads routes:geojson and links each owned route to trips:new', async () => {
    const fetch = answer({
      type: 'FeatureCollection',
      features: [
        { properties: { uuid: UUID, name: 'Mont Fort', distance_m: 11300, ascent_m: 780 } },
        { properties: { uuid: ZERO, name: '', distance_m: 4000, ascent_m: null } },
        { properties: { token: 'abc', pending: true, name: 'Shared', distance_m: 1 } },
      ],
    });
    window.pwaTripsSheet.open();

    plus().click();
    await settle();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe(ROUTES_URL);
    expect(fetch.mock.calls[0][1].credentials).toBe('same-origin');
    const links = picker().querySelectorAll('a[role="menuitem"]');
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute('href')).toBe(`${NEW_URL}?route=${UUID}`);
    expect(links[0].querySelector('[data-trip-route-picker-name]').textContent).toBe('Mont Fort');
    expect(links[0].querySelector('[data-trip-route-picker-meta]').textContent).toBe(
      '11.3km · 780m ↑',
    );
    expect(links[1].getAttribute('href')).toBe(`${NEW_URL}?route=${ZERO}`);
    expect(links[1].querySelector('[data-trip-route-picker-name]').textContent).toBe(
      'Untitled route',
    );
    expect(links[1].querySelector('[data-trip-route-picker-meta]').textContent).toBe('4.0km');
    expect(picker().textContent).not.toContain('Shared');
  });

  it('reads again on every open, not on the close', async () => {
    const fetch = answer({ features: [] });
    window.pwaTripsSheet.open();

    plus().click();
    await settle();
    plus().click();
    plus().click();
    await settle();

    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('with no routes, offers one item that goes to the Routes tab', async () => {
    answer({ type: 'FeatureCollection', features: [] });
    window.pwaTripsSheet.open();

    plus().click();
    await settle();

    const items = picker().querySelectorAll('[role="menuitem"]');
    expect(items).toHaveLength(1);
    expect(items[0].tagName).toBe('BUTTON');
    expect(items[0].getAttribute('data-saved-go')).toBe('routes');
    expect(items[0].textContent).toBe('Add a route first');
  });

  it('says so when the routes cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 500 }));
    window.pwaTripsSheet.open();

    plus().click();
    await settle();

    expect(picker().querySelector('[role="menuitem"]')).toBeNull();
    expect(picker().textContent).toBe("Your routes couldn't be loaded.");
  });

  it('says so when the request itself fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'));
    window.pwaTripsSheet.open();

    plus().click();
    await settle();

    expect(picker().textContent).toBe("Your routes couldn't be loaded.");
  });
});
