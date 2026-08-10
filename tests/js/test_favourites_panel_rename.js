/*
 * tests/js/test_favourites_panel_rename.js — Vitest DOM tests for the
 * favourites panel row's Rename menu item (SNOW-658, second pass).
 *
 * The map row's always-visible name `<input>` is gone: every UGC panel's
 * state-changing actions live in the row's "…" menu now, and a field
 * permanently in edit mode is not a row at rest. Rename is therefore a
 * prompt-then-post — the same interaction map_downloads_manager.js has used
 * for a custom area since SNOW-635, rather than a second one invented here.
 *
 * What is worth asserting, and why:
 *
 *   - the prompt is seeded with the name currently showing, so accepting it
 *     unchanged is a no-op rather than a blanking;
 *   - Cancel and an unchanged name write nothing — a rename that fires on
 *     "I looked and left it alone" is a spurious mutation;
 *   - a BLANK name is a real edit, unlike a downloaded area's: a favourite
 *     with no name is a supported state ("Unnamed pin");
 *   - the post carries HX-Request (the endpoint is @require_htmx) and the
 *     CSRF token read from the create template;
 *   - on success the LIST is re-read rather than the row patched. The
 *     rename endpoint answers with the MANAGE page's row partial, which is
 *     deliberately a different shape from the map's and cannot be swapped
 *     in — re-reading the truth is also what downloads' own rename does;
 *   - a failure toasts rather than leaving the row silently stale;
 *   - the handler is delegated on the sheet, because the panel body is
 *     re-cloned on every open.
 *
 * The listener must NOT stopPropagation: the click has to keep bubbling to
 * overflow_menu.js's document listener, or the menu it came from stays open.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const LIST_URL = '/favourites/partials/list/?variant=map';
const UUID = '11111111-2222-3333-4444-555555555555';

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
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class"
       class="hidden">
    <span data-toast-body></span>
  </div>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const btn = document.getElementById('favourite-add-btn');
const sheet = document.getElementById('favourite-sheet');

/** Open the panel and put one row, with its Rename item, inside it.
 *
 * Stands in for what favourites:list swaps in — the menu item's shape is
 * favourites/partials/_favourite_row_map_menu_items.html's, asserted
 * server-side in tests/favourites/test_views.py.
 *
 * @returns {HTMLButtonElement} The row's Rename menu item.
 */
function openPanelWithRow() {
  btn.click();
  const rows = sheet.querySelector('[data-favourites-rows]');
  rows.innerHTML = `
    <ul><li id="favourite-${UUID}">
      <button type="button"
              data-favourite-rename="${UUID}"
              data-favourite-current-name="Old name">Rename</button>
    </li></ul>`;
  return rows.querySelector('[data-favourite-rename]');
}

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true }));
  window.prompt = vi.fn(() => 'New name');
  window.PlacePicker = { activate: vi.fn(), deactivate: vi.fn() };
  window.snowdeskMapState = { map: { getCenter: () => ({ lat: 46.1, lng: 7.2 }) } };
});

afterEach(() => {
  delete window.PlacePicker;
  delete window.snowdeskMapState;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('the Rename menu item', () => {
  it('prompts seeded with the name the row is currently showing', () => {
    openPanelWithRow().click();

    expect(window.prompt).toHaveBeenCalledTimes(1);
    expect(window.prompt.mock.calls[0][1]).toBe('Old name');
  });

  it('posts the new name to the rename endpoint for that uuid', async () => {
    openPanelWithRow().click();
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(`/favourites/partials/${UUID}/rename/`);
    expect(opts.method).toBe('POST');
    // The endpoint is @require_htmx, and this is a plain fetch.
    expect(opts.headers['HX-Request']).toBe('true');
    expect(opts.headers['X-CSRFToken']).toBe('tok');
    expect(opts.body).toBe('name=New+name');
  });

  it('re-reads the list on success rather than patching the row', async () => {
    // One call for the open, one for the reload — and the reload goes to
    // the same URL, ?variant=map included.
    openPanelWithRow().click();
    await Promise.resolve();
    await Promise.resolve();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(2);
    expect(globalThis.htmx.ajax.mock.calls[1][1]).toBe(LIST_URL);
  });

  it('tells the map its pins changed, so the renamed one repaints', async () => {
    const changed = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', changed);

    openPanelWithRow().click();
    await Promise.resolve();
    await Promise.resolve();

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:favourites-changed', changed);
  });

  it('writes nothing on Cancel', () => {
    window.prompt = vi.fn(() => null);

    openPanelWithRow().click();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('writes nothing when the name comes back unchanged', () => {
    window.prompt = vi.fn(() => 'Old name');

    openPanelWithRow().click();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('accepts a blank name — an unnamed pin is a supported state', async () => {
    window.prompt = vi.fn(() => '   ');

    openPanelWithRow().click();
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(globalThis.fetch.mock.calls[0][1].body).toBe('name=');
  });

  it('toasts when the rename does not land', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));

    openPanelWithRow().click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    const toast = document.getElementById('map-sheet-toast');
    expect(toast.textContent).toContain("couldn't be saved");
  });

  it('lets the click through, so the open menu still closes', () => {
    const item = openPanelWithRow();
    // Registered after the panel is open, so only the Rename tap counts.
    const seen = vi.fn();
    document.addEventListener('click', seen);

    item.click();

    expect(seen).toHaveBeenCalledTimes(1);
    document.removeEventListener('click', seen);
  });
});
