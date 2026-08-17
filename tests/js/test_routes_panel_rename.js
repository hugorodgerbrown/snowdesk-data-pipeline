/*
 * tests/js/test_routes_panel_rename.js — renaming a route from the map panel
 * (SNOW-686).
 *
 * Hugo's map-panel design: "Where a row can be renamed, that happens in place
 * on the label." The pencil reveals the editor already sitting hidden in the
 * row; Enter or blur commits, Escape cancels.
 *
 * The interaction itself is static/js/inline_rename.js's, reused UNCHANGED
 * from the favourites and downloads panels and covered by
 * tests/js/test_inline_rename.js. What is worth asserting HERE is the wiring
 * routes.js owns:
 *
 *   - the pencil opens the editor, seeded from what the row is showing, and
 *     the label does not (one trigger, not two — SNOW-658);
 *   - the post carries HX-Request (route_rename is @require_htmx) and the
 *     CSRF token, read from the upload form rather than a create template
 *     since this surface has no create form;
 *   - on success the LIST is re-read rather than the row patched. route_rename
 *     answers with routes/partials/_route.html — the DEFAULT variant's row,
 *     deliberately a different shape from this panel's — so it cannot be
 *     swapped in. That endpoint is left exactly as SNOW-685 shipped it;
 *     teaching it ``?variant=`` would put a second surface's concern into a
 *     create/rename endpoint to save one refetch;
 *   - a failure toasts rather than leaving the row silently stale;
 *   - a cancel, an unchanged name and an emptied field write nothing — a
 *     rename that fires on "I looked and left it alone" is a spurious
 *     mutation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/inline_rename.js';
import '../../static/js/map_sheet.js';

const LIST_URL = '/routes/partials/list/?variant=map';
const UUID = '11111111-2222-3333-4444-555555555555';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="true"
          data-signin-url="/sign-in/"
          data-route-create-url="/routes/partials/create/"
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
    <input type="file" id="route-upload-input" name="file">
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

/** Open the panel and put one renameable row inside it.
 *
 * Stands in for what routes:list swaps in — the row's shape is
 * includes/_ugc_panel_row.html's plus
 * routes/partials/_route_row_map_actions.html's, both asserted server-side
 * in tests/routes/test_views.py::TestRouteListVariants.
 *
 * @returns {HTMLElement} The row.
 */
function openPanelWithRow() {
  btn.click();
  const rows = sheet.querySelector('[data-routes-rows]');
  rows.innerHTML = `
    <ul><li id="route-${UUID}" data-row-renameable>
      <span data-row-label>Haute Route</span>
      <input type="text" data-row-rename-input hidden aria-label="Route name">
      <button type="button" data-row-rename
              data-route-rename="${UUID}" aria-label="Rename Haute Route">✎</button>
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
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200 }));
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('renaming a route in place', () => {
  it('opens the editor from the pencil, seeded with the row’s own label', () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();

    const input = row.querySelector('[data-row-rename-input]');
    expect(input.hidden).toBe(false);
    expect(input.value).toBe('Haute Route');
    expect(row.querySelector('[data-row-label]').hidden).toBe(true);
  });

  it('does not open the editor from a click on the label', () => {
    // One trigger, not two. Hugo: "We have inline editing & the pencil -
    // choose one." The pencil is the one that works for everybody.
    const row = openPanelWithRow();

    row.querySelector('[data-row-label]').click();

    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(true);
    expect(row.querySelector('[data-row-label]').hidden).toBe(false);
  });

  it('posts the new name to the rename endpoint for that uuid', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Vallée Blanche', 'Enter');
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(`/routes/partials/${UUID}/rename/`);
    expect(opts.method).toBe('POST');
    // route_rename is @require_htmx, and this is a plain fetch.
    expect(opts.headers['HX-Request']).toBe('true');
    // Read from the upload form — this surface has no create-form template.
    expect(opts.headers['X-CSRFToken']).toBe('tok');
    expect(opts.body).toBe('name=Vall%C3%A9e+Blanche');
  });

  it('commits on blur too — leaving the field is not a cancel', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Vallée Blanche', 'blur');
    await Promise.resolve();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('re-reads the list on success rather than patching the row', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Vallée Blanche', 'Enter');
    await Promise.resolve();
    await Promise.resolve();

    // One call for the open, one for the reload — and the reload goes to the
    // same URL, ?variant=map included.
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(2);
    expect(globalThis.htmx.ajax.mock.calls[1][1]).toBe(LIST_URL);
  });

  it('writes nothing on Escape', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Vallée Blanche', 'Escape');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('writes nothing when the name comes back unchanged', async () => {
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Haute Route', 'Enter');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('writes nothing when the field is emptied', async () => {
    // The design's own rule: a trimmed-empty input leaves the title alone.
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, '   ', 'Enter');
    await Promise.resolve();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('toasts when the rename does not land', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 400 }));
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();
    finishEdit(row, 'Vallée Blanche', 'Enter');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(document.getElementById('map-sheet-toast').textContent).toContain(
      "couldn't be saved",
    );
  });

  it('does not open the file picker — a pencil click is not an add', () => {
    // Both are delegated on the same sheet listener, and the rename handler
    // returns first so the add test never runs for a pencil tap.
    const picker = vi.spyOn(
      document.getElementById('route-upload-input'),
      'click',
    );
    const row = openPanelWithRow();

    row.querySelector('[data-row-rename]').click();

    expect(picker).not.toHaveBeenCalled();
  });
});
