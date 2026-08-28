/*
 * tests/js/test_account_routes.js — the account page's routes list (SNOW-713).
 *
 * /account/routes/ renders the shared UGC row that every route surface
 * renders. The trash needs no JS to DELETE (it is a plain HTMX form) and the
 * pencil does; SNOW-752 added a second behaviour to the trash all the same,
 * because the removal it produces empties one <li> and nothing else.
 * static/js/account_routes.js is those two behaviours.
 *
 * The interaction is inline_rename.js's and the POST is
 * row_rename_commit.js's, both covered where they live
 * (test_inline_rename.js, test_row_rename_commit.js). What is asserted HERE
 * is this page's own half:
 *
 *   - the URL is built from the template the PAGE carries, not one baked
 *     into the module — the module must not know how the project spells its
 *     URLs, and an absent template must not send a request to a guess;
 *   - the response is swapped into the row it came from, rather than the
 *     list re-read as static/js/routes.js does on the map. Since SNOW-711
 *     route_rename answers with exactly this row in exactly this shape, so
 *     there is nothing to reconcile;
 *   - the swapped-in row is handed to htmx.process, or its Remove form —
 *     markup HTMX has never seen — silently stops working;
 *   - a failure reveals the page's shared error banner rather than writing a
 *     string from JS, which could not be translated (docs/i18n.md);
 *   - a removal announces `snowdesk:routes-changed` on the document, which
 *     is what the page's own list wrapper re-reads on (SNOW-752). The empty
 *     state is a server-side clause, so a page that has just lost its last
 *     route can only get one from a fresh response.
 *
 * The sibling of test_account_favourites.js, and shorter by everything that
 * suite spends on the disclosure chevron: a route has no detail page, so its
 * row has no expand control and no open card to carry across a swap.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/inline_rename.js';
import '../../static/js/row_rename_commit.js';
import '../../static/js/row_removed.js';

const UUID = '11111111-2222-3333-4444-555555555555';
const RENAME_URL = `/routes/partials/${UUID}/rename/`;

globalThis.htmx = { process: vi.fn() };

await import('../../static/js/account_routes.js');

/** The row routes/partials/_route.html renders, cut to what this module reads.
 *
 * No `data-row-panel` and no `data-row-disclosure`, unlike the favourite
 * row: a route has no detail page, so neither list variant has a
 * disclosure.
 *
 * @param {string} name The route's name.
 * @returns {string} One row.
 */
function rowMarkup(name) {
  return `
    <li id="route-${UUID}" data-row-renameable>
      <div>
        <span data-row-label>${name}</span>
        <input type="text" data-row-rename-input hidden aria-label="Route name">
        <button type="button" data-row-rename data-route-rename="${UUID}"
                aria-label="Rename ${name}">edit</button>
        <form data-row-remove hx-post="/routes/partials/${UUID}/delete/">
          <input type="hidden" name="csrfmiddlewaretoken" value="tok">
          <button type="submit" aria-label="Remove ${name}">bin</button>
        </form>
      </div>
    </li>`;
}

/** Paint the page as my_routes.html renders it.
 *
 * @param {string} name The route's name.
 * @param {string} template The rename URL template the page carries.
 * @returns {void}
 */
function renderPage(name, template = '/routes/partials/__UUID__/rename/') {
  document.body.innerHTML = `
    <div id="htmx-error-banner" class="hidden">Something went wrong.</div>
    <div data-route-list ${template ? `data-rename-url-template="${template}"` : ''}>
      <div data-testid="route-list-default"><ul>${rowMarkup(name)}</ul></div>
    </div>`;
}

/** Rename the row on screen and let the fetch settle.
 *
 * @param {string} value What the user typed.
 * @returns {Promise<void>}
 */
async function rename(value) {
  document.querySelector('[data-row-rename]').click();
  const input = document.querySelector('[data-row-rename-input]');
  input.value = value;
  input.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  );
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

/** @returns {HTMLElement} The shared error banner. */
const banner = () => document.getElementById('htmx-error-banner');

beforeEach(() => {
  globalThis.htmx.process.mockClear();
  renderPage('Haute Route');
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(rowMarkup('Renamed')),
    }),
  );
});

describe('renaming a route', () => {
  it('posts to the URL built from the page’s own template', async () => {
    await rename('Renamed');

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(RENAME_URL);
    // route_rename is @require_htmx, and this is a plain fetch.
    expect(init.headers['HX-Request']).toBe('true');
    // Read off the row's own Remove form — this page has no create form.
    expect(init.headers['X-CSRFToken']).toBe('tok');
  });

  it('swaps the response into the row rather than re-reading the list', async () => {
    await rename('Renamed');

    expect(document.querySelector('[data-row-label]').textContent).toBe(
      'Renamed',
    );
    // One row still, not two — a swap, not an append.
    expect(document.querySelectorAll('[data-row-label]')).toHaveLength(1);
  });

  it('hands the swapped-in row to htmx.process', async () => {
    await rename('Renamed');

    // HTMX only processes what it swapped in itself, and this swap was
    // ours — without this the trash on a just-renamed row does nothing.
    expect(globalThis.htmx.process).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.process.mock.calls[0][0].id).toBe(`route-${UUID}`);
  });

  it('sends nothing when the page carries no URL template', async () => {
    // The guard that matters: a missing attribute must not become a request
    // to a guessed path. row_rename_commit.js returns early, so the row is
    // simply left alone.
    renderPage('Haute Route', '');
    await rename('Renamed');

    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(document.querySelector('[data-row-label]').textContent).toBe(
      'Haute Route',
    );
  });
});

describe('when the rename fails', () => {
  it('reveals the shared error banner on a non-2xx', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve('') }),
    );
    await rename('Renamed');

    expect(banner().classList.contains('hidden')).toBe(false);
  });

  it('reveals the shared error banner on a dropped request', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('offline')));
    await rename('Renamed');

    // Rename is online-only on every surface. A queued replay would commit
    // a name against a row the user may have deleted meanwhile.
    expect(banner().classList.contains('hidden')).toBe(false);
  });

  it('leaves the old name on screen when the commit fails', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve('') }),
    );
    await rename('Renamed');

    // The row must not show a name the server never agreed to.
    expect(document.querySelector('[data-row-label]').textContent).toBe(
      'Haute Route',
    );
  });
});

describe('a row removed from the list', () => {
  beforeEach(() => {
    renderPage('Haute Route');
  });

  it('announces the change, so the page re-reads its own list', () => {
    // The list wrapper carries the hx-get and re-reads on this event (see
    // my_routes.html) — the URL is written once, in the template. The row's
    // own swap empties one <li>, so a page that has just lost its last
    // route keeps rendering as a list of none: routes:list's empty state is
    // a server-side clause and only a fresh response can carry it.
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);

    const xhr = {};
    const form = document.querySelector('[data-row-remove]');
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: form } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:routes-changed', changed);
  });

  it('stays quiet when the delete failed', () => {
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);

    const xhr = {};
    const form = document.querySelector('[data-row-remove]');
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: form } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: false } }),
    );

    expect(changed).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:routes-changed', changed);
  });
});
