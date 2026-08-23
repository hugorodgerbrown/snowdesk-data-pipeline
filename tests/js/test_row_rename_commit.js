/*
 * tests/js/test_row_rename_commit.js — the shared rename commit (SNOW-711).
 *
 * static/js/row_rename_commit.js is the twenty lines the favourites panel,
 * the routes panel and the account page had a copy of each: read the row's
 * uuid off the pencil, POST the committed name, hand the response back.
 * Each caller's own wiring is covered where it lives
 * (test_favourites_panel_rename.js, test_routes_panel_rename.js, and
 * test_account_favourites.js); what is asserted HERE is the contract those
 * three share, including the parts none of them exercises:
 *
 *   - the boolean a delegated listener branches on — false means "not a
 *     rename, keep testing", and a click that IS a rename but cannot be
 *     served still returns true, or it would fall through to the caller's
 *     add-CTA test;
 *   - the CSRF token is read at COMMIT time, not at bind time: every one of
 *     these surfaces re-renders the element carrying it;
 *   - onCommitted receives the unread response and the row, which is what
 *     lets the account page swap it in while the map panels ignore it;
 *   - onFailed covers a non-2xx, a dropped request AND a throw out of
 *     onCommitted — all three leave the row showing something the server
 *     never agreed to.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/inline_rename.js';
import '../../static/js/row_rename_commit.js';

const UUID = '11111111-2222-3333-4444-555555555555';

/** Render one renameable row and return it.
 *
 * The shape includes/_ugc_panel_row.html renders, cut to the hooks this
 * module reads.
 *
 * @param {string} uuidAttribute The panel's own uuid hook on the pencil.
 * @param {string} uuid The value it carries; '' omits the attribute value.
 * @returns {HTMLElement} The row.
 */
function renderRow(uuidAttribute, uuid) {
  document.body.innerHTML = `
    <ul><li id="row-${UUID}" data-row-renameable>
      <span data-row-label>Haute Route</span>
      <input type="text" data-row-rename-input hidden aria-label="Name">
      <button type="button" data-row-rename ${uuidAttribute}="${uuid}">edit</button>
    </li></ul>`;
  return document.querySelector('li');
}

/** Drive one whole rename: click the pencil, type, commit with Enter.
 *
 * handleClick is called directly rather than from a delegated document
 * listener, because a caller binds ONE listener for the life of the page
 * and a test that binds a fresh one per case would leave the earlier
 * cases' options handling every later click.
 *
 * @param {HTMLElement} row The row to rename.
 * @param {string} value What the user typed.
 * @param {object} opts The options under test.
 * @returns {boolean} handleClick's own answer.
 */
function rename(row, value, opts) {
  const handled = window.pwaRowRenameCommit.handleClick(
    { target: row.querySelector('[data-row-rename]') },
    opts,
  );
  const input = row.querySelector('[data-row-rename-input]');
  input.value = value;
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  return handled;
}

/** The options a caller passes, with everything overridable.
 *
 * @param {object} overrides Fields to replace.
 * @returns {object} Options for handleClick.
 */
function options(overrides) {
  return Object.assign(
    {
      uuidAttribute: 'data-route-rename',
      urlTemplate: '/routes/partials/__UUID__/rename/',
      csrfToken: () => 'tok',
      onCommitted: vi.fn(),
      onFailed: vi.fn(),
    },
    overrides || {},
  );
}

beforeEach(() => {
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200 }));
});

describe('window.pwaRowRenameCommit', () => {
  it('is frozen, like every other pwa* global', () => {
    expect(Object.isFrozen(window.pwaRowRenameCommit)).toBe(true);
  });

  it('reports a click outside a pencil as not a rename', () => {
    const row = renderRow('data-route-rename', UUID);
    const label = row.querySelector('[data-row-label]');

    const handled = window.pwaRowRenameCommit.handleClick(
      { target: label },
      options(),
    );

    expect(handled).toBe(false);
    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(true);
  });

  it('posts the committed name to the row’s own rename URL', async () => {
    const row = renderRow('data-route-rename', UUID);
    const opts = options();
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();

    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(`/routes/partials/${UUID}/rename/`);
    expect(init.method).toBe('POST');
    // Every rename endpoint is @require_htmx and this is a plain fetch.
    expect(init.headers['HX-Request']).toBe('true');
    expect(init.headers['X-CSRFToken']).toBe('tok');
    expect(init.body).toBe('name=Vall%C3%A9e+Blanche');
  });

  it('reads the CSRF token at commit time, not at bind time', async () => {
    // The favourites panel reads its token out of a <template> it clones on
    // every open, and the account page out of markup HTMX swaps. A token
    // captured when the listener was bound would be the wrong one.
    const row = renderRow('data-route-rename', UUID);
    const csrfToken = vi.fn(() => 'fresh');
    const opts = options({ csrfToken });
    expect(csrfToken).not.toHaveBeenCalled();
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();

    expect(globalThis.fetch.mock.calls[0][1].headers['X-CSRFToken']).toBe('fresh');
  });

  it('hands the unread response and the row to onCommitted', async () => {
    // Unread: the account page reads it as text and swaps it into the row,
    // and the two map panels throw it away. Reading it here would take that
    // choice away from both.
    const row = renderRow('data-route-rename', UUID);
    const response = { ok: true, status: 200 };
    globalThis.fetch = vi.fn(() => Promise.resolve(response));
    const opts = options();
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();
    await Promise.resolve();

    expect(opts.onCommitted).toHaveBeenCalledWith(response, row);
    expect(opts.onFailed).not.toHaveBeenCalled();
  });

  it('calls onFailed on a non-2xx and never onCommitted', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 400 }));
    const row = renderRow('data-route-rename', UUID);
    const opts = options();
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(opts.onCommitted).not.toHaveBeenCalled();
    expect(opts.onFailed).toHaveBeenCalledTimes(1);
  });

  it('calls onFailed when the request never lands', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('offline')));
    const row = renderRow('data-route-rename', UUID);
    const opts = options();
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();
    await Promise.resolve();

    expect(opts.onFailed).toHaveBeenCalledTimes(1);
  });

  it('calls onFailed when the caller’s own swap throws', async () => {
    // A swap that threw used to leave the row renamed on screen, the write
    // landed, and nothing said the page had stopped agreeing with itself.
    const row = renderRow('data-route-rename', UUID);
    const opts = options({
      onCommitted: vi.fn(() => {
        throw new Error('swap failed');
      }),
    });
    rename(row, 'Vallée Blanche', opts);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(opts.onFailed).toHaveBeenCalledTimes(1);
  });

  it('claims the click but writes nothing when the row carries no uuid', async () => {
    // True, not false: the click WAS a rename. Returning false would send it
    // on to the caller's next test — on the map that is the add CTA, so a
    // pencil tap would open the create flow.
    const row = renderRow('data-route-rename', '');
    const opts = options();

    const handled = window.pwaRowRenameCommit.handleClick(
      { target: row.querySelector('[data-row-rename]') },
      opts,
    );
    await Promise.resolve();

    expect(handled).toBe(true);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('claims the click but writes nothing with no URL template', () => {
    const row = renderRow('data-route-rename', UUID);

    const handled = window.pwaRowRenameCommit.handleClick(
      { target: row.querySelector('[data-row-rename]') },
      options({ urlTemplate: '' }),
    );

    expect(handled).toBe(true);
    expect(globalThis.fetch).not.toHaveBeenCalled();
    // The editor never opened either — there is nothing to commit to.
    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(true);
  });
});
