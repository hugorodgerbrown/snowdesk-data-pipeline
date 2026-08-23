/*
 * tests/js/test_account_favourites.js — the account page's favourites list
 * (SNOW-711).
 *
 * /account/ renders the same row as the map panels, plus one thing they do
 * not have: a chevron expanding the pin's detail card under its own row.
 * static/js/account_favourites.js is the two behaviours that row needs
 * here, and both of them are state machines — which is why this is a Vitest
 * suite and not a browser test.
 *
 *   RENAME — the interaction is inline_rename.js's and the POST is
 *   row_rename_commit.js's, both covered where they live. What is asserted
 *   here is this page's own half: the response IS this row, in this shape,
 *   so it is swapped straight back in rather than the whole list re-read
 *   (a refetch would discard every expanded card on the page to update one
 *   label), and the swapped-in row is handed to htmx.process so its Remove
 *   form works.
 *
 *   EXPAND/COLLAPSE — the chevron's own hx-get does the fetching, so HTMX
 *   fires the events favourites_offline.js's write-through listens for.
 *   This module owns aria-expanded, and owns the collapse entirely: it
 *   empties the panel and stops the click reaching HTMX, which would
 *   otherwise re-fetch the card the user just closed. That stopping is
 *   done in the capture phase, and the test for it binds a listener where
 *   HTMX binds its own — on the control itself.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/inline_rename.js';
import '../../static/js/row_rename_commit.js';

const UUID = '11111111-2222-3333-4444-555555555555';
const RENAME_URL = `/favourites/partials/${UUID}/rename/`;
const CARD_URL = `/favourites/partials/${UUID}/card/`;

globalThis.htmx = { process: vi.fn(), ajax: vi.fn(), trigger: vi.fn() };

await import('../../static/js/account_favourites.js');

/** The row markup favourites:list renders, cut to what this module reads.
 *
 * The expand panel is INSIDE the row's own `<li>` (SNOW-711), which is
 * what makes Remove take an expanded card down with the header that
 * opened it. It was a sibling `<li>` for one commit, and a delete left the
 * orphaned card behind on screen.
 *
 * @param {string} name The pin's name.
 * @returns {string} One row, its header line and its own (empty) panel.
 */
function rowMarkup(name) {
  return `
    <li id="favourite-${UUID}" data-row-renameable>
      <div>
        <span data-row-label>${name}</span>
        <input type="text" data-row-rename-input hidden aria-label="Favourite name">
        <button type="button" data-row-rename data-favourite-rename="${UUID}"
                aria-label="Rename ${name}">edit</button>
        <form hx-post="/favourites/partials/${UUID}/delete/">
          <input type="hidden" name="csrfmiddlewaretoken" value="tok">
          <button type="submit" aria-label="Remove ${name}">bin</button>
        </form>
        <a href="/favourites/${UUID}/" hx-get="${CARD_URL}"
           hx-target="#favourite-panel-${UUID}" data-row-disclosure
           aria-controls="favourite-panel-${UUID}" aria-expanded="false">chevron</a>
      </div>
      <div id="favourite-panel-${UUID}" data-row-panel></div>
    </li>`;
}

/** Paint the page as it stands once favourites:list has been swapped in.
 *
 * @param {string} name The pin's name.
 * @returns {void}
 */
function renderList(name) {
  document.body.innerHTML = `
    <div id="htmx-error-banner" class="hidden">Something went wrong.</div>
    <div data-favourite-list data-rename-url-template="/favourites/partials/__UUID__/rename/">
      <ul>${rowMarkup(name)}</ul>
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

/** @returns {HTMLElement} The row's disclosure control. */
const chevron = () => document.querySelector('[data-row-disclosure]');

/** @returns {HTMLElement} The row's own expand panel. */
const panel = () => document.getElementById(`favourite-panel-${UUID}`);

beforeEach(() => {
  globalThis.htmx.process.mockClear();
  renderList('Mine');
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      // favourite_rename returns the whole row, panel included and empty —
      // the endpoint has no idea the user has this pin expanded. Carrying
      // the open card across that swap is swapRow's job, asserted below.
      text: () => Promise.resolve(rowMarkup('Renamed')),
    }),
  );
});

describe('renaming a row', () => {
  it('posts to the URL built from the list’s own template', async () => {
    await rename('Renamed');

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(RENAME_URL);
    // favourite_rename is @require_htmx, and this is a plain fetch.
    expect(init.headers['HX-Request']).toBe('true');
    // Read off the row's own Remove form — this page has no create form.
    expect(init.headers['X-CSRFToken']).toBe('tok');
  });

  it('swaps the response into the row rather than re-reading the list', async () => {
    await rename('Renamed');

    expect(document.querySelector('[data-row-label]').textContent).toBe(
      'Renamed',
    );
    // One row, not two: it replaced itself.
    expect(document.querySelectorAll('[data-row-renameable]').length).toBe(1);
    // And the list was not re-fetched — the expanded cards on the page
    // would all have been thrown away with it.
    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });

  it('hands the swapped-in row to htmx.process', async () => {
    // Nothing else will: HTMX processes what it swapped in itself, and
    // this swap was ours. Without it the new row's Remove form is inert.
    await rename('Renamed');

    expect(globalThis.htmx.process).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.process.mock.calls[0][0].id).toBe(
      `favourite-${UUID}`,
    );
  });

  it('keeps an expanded row expanded across the swap', async () => {
    // The response carries this row's panel, empty — the endpoint has no
    // idea the user has the pin open. So the swap would drop the card on
    // the floor, and the fresh row would read closed while its card was
    // still on screen. swapRow carries the card across and says so.
    panel().innerHTML = '<div>card</div>';

    await rename('Renamed');

    expect(chevron().getAttribute('aria-expanded')).toBe('true');
    expect(panel().children.length).toBe(1);
  });

  it('reveals the page’s error banner when the rename does not land', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 400 }));

    await rename('Renamed');

    expect(
      document.getElementById('htmx-error-banner').className,
    ).not.toContain('hidden');
    // And the row still shows what the server still holds.
    expect(document.querySelector('[data-row-label]').textContent).toBe('Mine');
  });
});

describe('expanding a row', () => {
  it('records the open state and leaves the fetch to HTMX', () => {
    // Stands in for HTMX's own listener, preventDefault included — that is
    // what stops the anchor navigating when it handles the click.
    const htmxListener = vi.fn((event) => event.preventDefault());
    chevron().addEventListener('click', htmxListener);

    chevron().click();

    expect(chevron().getAttribute('aria-expanded')).toBe('true');
    // The click reached the element's own listener — which on the page is
    // HTMX's, and its hx-get is what favourites_offline.js watches for.
    expect(htmxListener).toHaveBeenCalledTimes(1);
  });

  it('empties the panel and stops the click on the closing tap', () => {
    // Where HTMX binds: on the control. The capture-phase listener has to
    // run before this one and stop the event, or closing a card would
    // fetch it again.
    const htmxListener = vi.fn((event) => event.preventDefault());
    chevron().addEventListener('click', htmxListener);
    chevron().click();
    panel().innerHTML = '<div>card</div>';
    htmxListener.mockClear();

    chevron().click();

    expect(chevron().getAttribute('aria-expanded')).toBe('false');
    expect(panel().children.length).toBe(0);
    expect(htmxListener).not.toHaveBeenCalled();
  });

  it('aborts a card still in flight when the row is closed again', () => {
    // aria-expanded is set optimistically on the opening click, so a user
    // who opens and closes before the GET lands would otherwise get the
    // response swapped into a panel they had already closed — a card on
    // screen under a control reading "collapsed".
    globalThis.htmx.trigger.mockClear();
    chevron().click();

    chevron().click();

    expect(globalThis.htmx.trigger).toHaveBeenCalledWith(
      chevron(),
      'htmx:abort',
    );
  });

  it('ignores a click anywhere else in the row', () => {
    document.querySelector('[data-row-label]').click();

    expect(chevron().getAttribute('aria-expanded')).toBe('false');
  });
});
