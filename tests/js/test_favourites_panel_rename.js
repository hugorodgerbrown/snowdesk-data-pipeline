/*
 * tests/js/test_favourites_panel_rename.js — Vitest DOM tests for renaming
 * a favourite from the map panel (SNOW-658).
 *
 * Hugo's map-panel design: "Where a row can be renamed, that happens in
 * place on the label." The pencil, or a click on the label, reveals the
 * editor already sitting hidden in the row; Enter or blur commits, Escape
 * cancels. It replaces a window.prompt that lasted one day, which itself
 * replaced an always-visible name field.
 *
 * The interaction itself is static/js/inline_rename.js's and is covered by
 * tests/js/test_inline_rename.js. What is worth asserting HERE is the wiring
 * this module owns:
 *
 *   - the pencil and the label both open the editor, seeded from what the
 *     row is currently showing;
 *   - the post carries HX-Request (the endpoint is @require_htmx) and the
 *     CSRF token read from the create template;
 *   - on success the LIST is re-read rather than the row patched. The
 *     rename endpoint answers with the MANAGE page's row partial, which is
 *     deliberately a different shape from the map's and cannot be swapped
 *     in — re-reading the truth is also what downloads' own rename does;
 *   - a failure toasts rather than leaving the row silently stale;
 *   - a cancel and an emptied field write nothing — a rename that fires on
 *     "I looked and left it alone" is a spurious mutation;
 *   - the handler is delegated on the sheet, because the panel body is
 *     re-cloned on every open.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/inline_rename.js';
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

/** Open the panel and put one renameable row inside it.
 *
 * Stands in for what favourites:list swaps in — the row's shape is
 * includes/_ugc_panel_row.html's plus
 * favourites/partials/_favourite_row_map_actions.html's, both asserted
 * server-side in tests/favourites/test_views.py.
 *
 * @returns {HTMLElement} The row.
 */
function openPanelWithRow() {
  btn.click();
  const rows = sheet.querySelector('[data-favourites-rows]');
  rows.innerHTML = `
    <ul><li id="favourite-${UUID}" data-row-renameable>
      <span data-row-label>Old name</span>
      <input type="text" data-row-rename-input hidden aria-label="Favourite name">
      <button type="button" data-row-rename
              data-favourite-rename="${UUID}" aria-label="Rename Old name">✎</button>
    </li></ul>`;
  return rows.querySelector('li');
}

/** Type into the open editor and finish the edit.
 *
 * @param {HTMLElement} row The row being edited.
 * @param {string} value What the user typed.
 * @param {string} key 'Enter' or 'Escape'; anything else blurs instead.
 * @returns {void}
 */
function finishEdit(row, value, key) {
  const input = row.querySelector('[data-row-rename-input]');
  input.value = value;
  if (key === 'Enter' || key === 'Escape') {
    input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    return;
  }
  input.dispatchEvent(new FocusEvent('blur'));
}

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true }));
  window.PlacePicker = { activate: vi.fn(), deactivate: vi.fn() };
  window.snowdeskMapState = { map: { getCenter: () => ({ lat: 46.1, lng: 7.2 }) } };
});

afterEach(() => {
  delete window.PlacePicker;
  delete window.snowdeskMapState;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('renaming a favourite in place', () => {
  it('opens the editor from the pencil, seeded with the row’s own label', () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();

    const input = row.querySelector('[data-row-rename-input]');
    expect(input.hidden).toBe(false);
    expect(input.value).toBe('Old name');
    expect(row.querySelector('[data-row-label]').hidden).toBe(true);
  });

  it('does not open the editor from a click on the label (SNOW-658)', () => {
    // The label opened it too for a day. Hugo: "We have inline editing &
    // the pencil - choose one."
    const row = openPanelWithRow();

    row.querySelector('[data-row-label]').click();

    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(true);
    expect(row.querySelector('[data-row-label]').hidden).toBe(false);
  });

  it('posts the new name to the rename endpoint for that uuid', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'Enter');
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

  it('commits on blur too — leaving the field is not a cancel', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'blur');
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('re-reads the list on success rather than patching the row', async () => {
    // One call for the open, one for the reload — and the reload goes to
    // the same URL, ?variant=map included.
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'Enter');
    await Promise.resolve();
    await Promise.resolve();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(2);
    expect(globalThis.htmx.ajax.mock.calls[1][1]).toBe(LIST_URL);
  });

  it('tells the map its pins changed, so the renamed one repaints', async () => {
    const changed = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', changed);
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'Enter');
    await Promise.resolve();
    await Promise.resolve();

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:favourites-changed', changed);
  });

  it('writes nothing on Escape', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'Escape');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('writes nothing when the name comes back unchanged', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Old name', 'Enter');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('writes nothing when the field is emptied', async () => {
    // The design's own commit(): a trimmed-empty input leaves the title
    // unchanged. This is a CHANGE from the prompt this replaces, which
    // treated a blank as "clear the name" — one rule for both renameable
    // panels beats one exception, and a pin can still be created with no
    // name at all.
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, '   ', 'Enter');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('toasts when the rename does not land', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'New name', 'Enter');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    const toast = document.getElementById('map-sheet-toast');
    expect(toast.textContent).toContain("couldn't be saved");
  });

  it('lets the click through — it swallows nothing the sheet still needs', () => {
    const row = openPanelWithRow();
    // Registered after the panel is open, so only the pencil tap counts.
    const seen = vi.fn();
    document.addEventListener('click', seen);

    row.querySelector('[data-row-rename]').click();

    expect(seen).toHaveBeenCalledTimes(1);
    document.removeEventListener('click', seen);
  });
});
